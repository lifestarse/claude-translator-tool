#!/usr/bin/env python3
"""Восстановление прозы, испорченной сверхширокими шаблонами реестра канонов.

Гистограмма замен (replacement_detail.json, HEAD против рабочего дерева)
показала четыре класса порчи от правил с шаблонами по обычным русским словам:

  * «Капитаны» с шаблоном сущест\\w* — «существовало» -> «Капитаны»,
    «существа-Кнопки» -> «Капитаны-Кнопки», задвоения «Капитан-капитан»;
  * «Кости» с шаблоном костян\\w* — «скелетно-костяной узор» -> «скелетно-Кости»;
  * «Страж <- Защитник» без зоны глав — «Защитник» переписан по всему корпусу,
    хотя он законный канон другого термина; зона Стража — главы с 護衛者;
  * точечные: «Ноль целых» -> «Зеро целых», «Поглощение пищи» -> «Глотающий
    пищи», канон «механические жители Эрш», съеденный правилом «эршианин».

Ремонт обратным пословным диффом: восстанавливается ровно тот фрагмент HEAD,
который был заменён вредным правилом; легитимные замены того же файла не
трогаются. Каждый тронутый файл проходит validate_translation.

    python repair_vandal.py --dry   # план, без записи
    python repair_vandal.py
"""

import argparse
import difflib
import io
import json
import os
import re
import subprocess
import sys

from common import ROOT, chapter_path, read_text, validate_translation

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

STRAZH_SCOPE = {37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 54}  # главы 護衛者


def classify(old, new, chapter):
    """True — пару надо откатить к HEAD."""
    if re.search(r"[Кк]остян", old) and re.search(r"[Кк]ост", new):
        return True
    if re.search(r"[Сс]ущест", old) and "Капитан" in new:
        low = old.lower()
        if re.search(r"Капитан\w*-[Кк]апитан", new):
            return True          # задвоение «Капитан-капитан»
        if "подобн" in low:
            return True          # авторское описание схлопнуто в имя
        if "капитанско" in low:
            return False         # «существа капитанского типа» — легитимно
        if re.search(r"существ\w*-[Кк]апитан", low):
            return False         # «существа-капитаны» — легитимно
        return True              # голое сущест\w*
    if re.search(r"[Зз]ащитни|[Сс]тражник", old) and "Страж" in new:
        return chapter not in STRAZH_SCOPE
    return False


# Точечные ремонты: (глава, регэксп, замена). Найдены прицельным поиском.
CONTEXT_REPAIRS = [
    (1964, r"Зеро целых", "Ноль целых"),
    (2327, r"Девять-Ао-Зеро-Девять", "Девять-Ао-Ноль-Девять"),
    (9, r"Глотающий пищи продолжался", "Поглощение пищи продолжалось"),
]

# «механические жители Эрш» — канон, съеденный шаблоном «эршианин».
# Прилагательное остаётся, существительное возвращается в канон с падежом.
ERSH_SUFFIX = [
    ("эршианами", "жителями Эрш"), ("эршианам", "жителям Эрш"),
    ("эршианах", "жителях Эрш"), ("эршианином", "жителем Эрш"),
    ("эршианину", "жителю Эрш"), ("эршианина", "жителя Эрш"),
    ("эршианине", "жителе Эрш"), ("эршианин", "житель Эрш"),
    ("эршиане", "жители Эрш"), ("эршиан", "жителей Эрш"),
]
ERSH_RE = re.compile(r"([Мм]еханическ\w+|[Мм]ашинн\w+|полу-механическ\w+)\s+(эршиан\w*)")


def ersh_fix(m):
    for suf, repl in ERSH_SUFFIX:
        if m.group(2) == suf:
            return m.group(1) + " " + repl
    return m.group(0)


def main():
    ap = argparse.ArgumentParser(description="Откат порчи от вредных правил")
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    detail_file = os.path.join(ROOT, "replacement_detail.json")
    if os.path.exists(detail_file):
        detail = json.load(io.open(detail_file, encoding="utf-8"))
    else:
        print("[i] Нет replacement_detail.json (гистограмма замен по git) — "
              "откат пар не делается, только точечные ремонты.")
        detail = {}
    per_file = {}
    for key, files in detail.items():
        old, new = key.split(" => ", 1)
        for path in files:
            per_file.setdefault(path.replace("\\", "/"), []).append((old, new))
    # Точечные ремонты не зависят от гистограммы: глава из CONTEXT_REPAIRS
    # обязана попасть в обход, даже если в detail её нет.
    for ch, _rx, _rep in CONTEXT_REPAIRS:
        per_file.setdefault("ru/chapter_%04d.txt" % ch, [])

    touched, failed, total = 0, [], 0
    for path in sorted(per_file):
        idx = int(path[11:15])
        want = {(o, n) for o, n in per_file[path] if classify(o, n, idx)}
        ctx = [(rx, rep) for ch, rx, rep in CONTEXT_REPAIRS if ch == idx]
        full = os.path.join(ROOT, path)
        if not os.path.exists(full):
            continue
        cur = read_text(full)
        need_ersh = ERSH_RE.search(cur)
        if not want and not ctx and not need_ersh:
            continue

        plan = []
        if want:
            res = subprocess.run(["git", "show", "HEAD:" + path], cwd=ROOT,
                                 capture_output=True, encoding="utf-8",
                                 errors="replace")
            head = res.stdout
            if res.returncode or not head.strip():
                # Без базиса из git дифф пуст, и откат молча ничего бы не
                # сделал. ru/ должен быть под git, чтобы этот движок работал.
                print("[!] %s: нет версии в HEAD (%s) — откат пар пропущен"
                      % (path, (res.stderr or "пусто").strip()[:80]))
                failed.append((idx, ["нет базиса в git"]))
                want = set()
        if want:
            a, b = head.split(), cur.split()
            b_pos = list(re.finditer(r"\S+", cur))
            sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
            spans = []
            for tag, i1, i2, j1, j2 in sm.get_opcodes():
                if tag != "replace":
                    continue
                pair = (" ".join(a[i1:i2]), " ".join(b[j1:j2]))
                if pair not in want:
                    continue
                start, end = b_pos[j1].start(), b_pos[j2 - 1].end()
                if "\n" in cur[start:end]:
                    print("[!] %s: пропуск (замена через границу абзаца): %s" % (path, pair[1][:60]))
                    continue
                spans.append((start, end, pair[0], pair[1]))
            for start, end, old, new in sorted(spans, reverse=True):
                cur = cur[:start] + old + cur[end:]
                plan.append("%s  <=  %s" % (old, new))
        for rx, rep in ctx:
            cur, n = re.subn(rx, rep, cur)
            if n:
                plan.append("%s  <=  %s (контекст, %d)" % (rep, rx, n))
        if need_ersh:
            cur, n = ERSH_RE.subn(ersh_fix, cur)
            if n:
                plan.append("механические жители Эрш восстановлены (%d)" % n)

        if not plan:
            continue
        total += len(plan)
        print("%s:" % path)
        for line in plan:
            print("    %s" % line)
        if args.dry:
            touched += 1
            continue
        problems = validate_translation(read_text(chapter_path(idx)), cur)
        if problems:
            failed.append((idx, problems))
            print("    [!] НЕ ЗАПИСАНО, приёмка: %s" % "; ".join(problems))
            continue
        io.open(full, "w", encoding="utf-8", newline="\n").write(cur)
        touched += 1

    print("")
    print("=== ИТОГ: файлов %s %d, восстановлений %d, отказов приёмки %d"
          % ("в плане" if args.dry else "исправлено", touched, total, len(failed)))


if __name__ == "__main__":
    main()
