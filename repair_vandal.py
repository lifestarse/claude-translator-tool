#!/usr/bin/env python3
"""Восстановление прозы, испорченной сверхширокими шаблонами реестра канонов.

Движок обратного пословного диффа: сравнивает главу в снимке «до правки»
(--base, по умолчанию ru_birth/) с текущей ru/ и восстанавливает ровно тот
фрагмент снимка, который был заменён вредным правилом; легитимные замены того
же файла не трогаются. Каждый тронутый файл проходит validate_translation.

Откуда классы порчи. Прошлая книга дала их четыре — все от правил с шаблонами
по обычным русским словам: «существовало» -> «Капитаны» (шаблон сущест\\w*),
«скелетно-костяной» -> «скелетно-Кости», «Страж <- Защитник» без зоны глав по
всему корпусу, точечные порчи вроде «Ноль целых» -> «Зеро целых». Сами правила
были той книги и сюда не перенесены: у этой книги свои термины, и правило
отката по чужим именам испортило бы текст уже само. Классы порчи для этой
книги заносятся в VANDAL_RULES по вердиктам termaudit («вандализм») и вычитке
файла замен человеком — пока они пусты, скрипт честно говорит, что делать нечего.

Снимок «до» — не git: ru/ в .gitignore, `git show HEAD:ru/...` пуст. Нужна
копия глав, снятая до массовой правки (например, `ru_birth/` — копия ru/
перед прогоном termfix).

    python repair_vandal.py --dry                  # план, без записи
    python repair_vandal.py --base ru_birth
"""

import argparse
import difflib
import io
import os
import re
import sys

from common import ROOT, RU_DIR, chapter_path, read_text, ru_path, validate_translation

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Классы порчи: пара «было в снимке» -> «стало сейчас», которую надо откатить.
#   old, new — регэкспы по фрагменту снимка и по заменившему его фрагменту;
#   only     — откатывать только в этих главах (необязательно);
#   keep     — НЕ откатывать в этих главах: там замена законна (необязательно).
# Пример формы (правило прошлой книги, здесь не действует):
#   {"old": r"[Сс]ущест", "new": r"Капитан", "why": "сущест\w* -> Капитаны"},
#   {"old": r"[Зз]ащитни", "new": r"Страж", "keep": [37, 38, 54],
#    "why": "Страж вне зоны 護衛者"},
VANDAL_RULES = []

# Точечные ремонты: (глава, регэксп по текущему тексту, замена). Только после
# прицельного поиска и только с номером главы — никаких правил на весь корпус.
CONTEXT_REPAIRS = []


def classify(old, new, chapter):
    """True — пару надо откатить к снимку."""
    for r in VANDAL_RULES:
        if r.get("only") and chapter not in r["only"]:
            continue
        if chapter in (r.get("keep") or ()):
            continue
        if re.search(r["old"], old) and re.search(r["new"], new):
            return True
    return False


def main():
    ap = argparse.ArgumentParser(description="Откат порчи от вредных правил")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--base", default=os.path.join(ROOT, "ru_birth"),
                    help="каталог снимка глав до правки (по умолчанию ru_birth/)")
    args = ap.parse_args()

    if not VANDAL_RULES and not CONTEXT_REPAIRS:
        print("[i] Правил отката нет (VANDAL_RULES и CONTEXT_REPAIRS пусты) — делать нечего.")
        print("    Правила прошлой книги сняты. Классы порчи этой книги заносятся сюда")
        print("    по вердиктам termaudit «вандализм» и вычитке файла замен.")
        return
    if VANDAL_RULES and not os.path.isdir(args.base):
        print("[!] Нет снимка глав до правки: %s" % args.base)
        print("    Пословный дифф строится от снимка, не от git (ru/ в .gitignore).")
        print("    Укажи каталог копии ru/ до массовой правки: --base <каталог>.")
        raise SystemExit(1)

    names = sorted(n for n in os.listdir(RU_DIR)
                   if n.startswith("chapter_") and n.endswith(".txt"))
    touched, failed, total = 0, [], 0
    for name in names:
        idx = int(name[8:12])
        path = ru_path(idx)
        ctx = [(rx, rep) for ch, rx, rep in CONTEXT_REPAIRS if ch == idx]
        base_file = os.path.join(args.base, name)
        use_diff = bool(VANDAL_RULES) and os.path.exists(base_file)
        if not use_diff and not ctx:
            continue
        cur = read_text(path)

        plan = []
        if use_diff:
            head = read_text(base_file)
            a, b = head.split(), cur.split()
            a_pos = list(re.finditer(r"\S+", head))
            b_pos = list(re.finditer(r"\S+", cur))
            sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
            spans = []
            for tag, i1, i2, j1, j2 in sm.get_opcodes():
                if tag != "replace":
                    continue
                pair = (" ".join(a[i1:i2]), " ".join(b[j1:j2]))
                if not classify(pair[0], pair[1], idx):
                    continue
                start, end = b_pos[j1].start(), b_pos[j2 - 1].end()
                # Возвращается кусок снимка как есть, с его пробелами; склейка
                # через " " схлопнула бы перевод строки внутри фрагмента снимка.
                old = head[a_pos[i1].start():a_pos[i2 - 1].end()]
                if "\n" in cur[start:end] or "\n" in old:
                    print("[!] %s: пропуск (замена через границу абзаца): %s" % (name, pair[1][:60]))
                    continue
                spans.append((start, end, old, pair[1]))
            for start, end, old, new in sorted(spans, reverse=True):
                cur = cur[:start] + old + cur[end:]
                plan.append("%s  <=  %s" % (old, new))
        for rx, rep in ctx:
            cur, n = re.subn(rx, rep, cur)
            if n:
                plan.append("%s  <=  %s (контекст, %d)" % (rep, rx, n))

        if not plan:
            continue
        total += len(plan)
        print("%s:" % name)
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
        with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(cur)
        touched += 1

    print("")
    print("=== ИТОГ: файлов %s %d, восстановлений %d, отказов приёмки %d"
          % ("в плане" if args.dry else "исправлено", touched, total, len(failed)))


if __name__ == "__main__":
    main()
