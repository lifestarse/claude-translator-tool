#!/usr/bin/env python3
"""Build repair tasks for chapters translated before a term became canonical.

Terminology is decided after the fact whenever glossary_overrides.json resolves
a conflict, so chapters already on disk keep the variant their agent invented.
A plain search-and-replace cannot do this: six of the resolutions change gender
or number (Белый жир м. -> Белая паста ж., Молот-Голова ж. -> Молотоглав м.),
and everything agreeing with the term has to move with it. So this script only
locates the damage and writes a task file; a `termfix` agent does the editing.

    python termfix.py --dry-run
    python termfix.py
"""

import argparse
import json
import os
import re
import sys

from common import RU_DIR, ROOT, read_text, save_json, write_text

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

FIXES_FILE = os.path.join(ROOT, "term_fixes.json")
FIX_DIR = os.path.join(ROOT, "fixes")

TEMPLATE = """# Правка терминологии: глава {idx:04d}

Файл главы: `{path}`

Главу ЦЕЛИКОМ не читай. Ниже перечислены все затронутые абзацы с номерами
строк: читай инструментом Read только эти диапазоны (offset/limit), затем
правь точечными Edit. Число абзацев главы меняться не должно.

## Замены

{table}

## Затронутые абзацы

{snippets}

## Чего делать нельзя

Ничего, кроме этих замен и согласования с ними. Текст вокруг не трогать:
ни формулировки, ни порядок слов, ни пунктуацию, ни разбиение на абзацы.
"""


# Endings a Russian noun phrase takes as it declines. Not a morphology engine --
# just enough to build strings a correct translation could plausibly contain.
ENDINGS = (
    "ый", "ий", "ой", "ого", "ему", "ому", "ым", "ом", "ые", "ых", "ыми", "им",
    "ая", "ую", "ое", "ые", "а", "ы", "и", "е", "у", "ю", "ом", "ам", "ами", "ах",
)


def self_conflict(canon, patterns):
    """Patterns that match the canon itself, or a declined form of it.

    A replacement rule describes obsolete SPELLINGS. Number and case are not
    spellings: the glossary entry fixes the dictionary form, and prose declines
    it as the sentence requires. A rule that catches its own canon sends an
    agent to "fix" correct text -- and it does not merely waste a run. The rule
    "Заражённые -> Заражённый" flagged six clean chapters, and in chapter 48 an
    agent obeyed it and collapsed a plural the original marks with 那些 and 它們,
    dragging the pronoun and the verb into the singular with it.

    Сверка нарочно с учётом регистра и только по последнему слову: половина
    реестра — правки капитализации («водомер -> Водомер»), их шаблоны обязаны
    ловить строчную форму канона. Склонения канона с СОВПАДАЮЩЕЙ
    капитализацией отсеиваются не здесь, а фильтром в scan() — там виден сам
    найденный текст, и только там отличима правка регистра от шума.
    """
    words = canon.split()
    forms = {canon}
    for cut in (1, 2, 3):
        if len(words[-1]) - cut < 3:
            continue
        base = " ".join(words[:-1] + [words[-1][:-cut]]).strip()
        for end in ENDINGS:
            forms.add(base + end)
    bad = []
    for p in patterns:
        rx = re.compile(p)
        for form in sorted(forms):
            if rx.search(form):
                bad.append((p, form))
                break
    return bad


def load_fixes():
    data = json.loads(read_text(FIXES_FILE))
    out, broken, unscoped = [], [], []
    for f in data["fixes"]:
        # Пустой список глав — это НЕ «везде». Скоупинг строится поиском
        # китайского термина по исходникам; пустота значит, что термин не
        # нашёлся и зона правила неизвестна. Правило «Страж <- Защитник» с
        # пустой зоной ушло бы на весь корпус, где «Защитник» — чужой канон.
        # Отсутствующий ключ — та же пустая зона: README и реестр требуют зону
        # явно, «по умолчанию везде» не бывает.
        if not f.get("chapters"):
            unscoped.append(f["canon"])
            continue
        conflicts = self_conflict(f["canon"], f["find"])
        conflicted = {p for p, _ in conflicts}
        keep = [p for p in f["find"] if p not in conflicted]
        if conflicts:
            broken.append((f["canon"], conflicts, bool(keep)))
        if not keep:
            continue
        out.append({
            "canon": f["canon"],
            "gender": f.get("gender", ""),
            "was": f.get("was", ""),
            "strict": f.get("strict", False),
            "res": [re.compile(p) for p in keep],
            "chapters": set(f["chapters"]) if f.get("chapters") else None,
        })
    if broken:
        print("[!] Шаблоны, ловящие собственный канон — отброшены:")
        for canon, conflicts, kept in broken:
            for pat, form in conflicts:
                print("    %s: шаблон %s срабатывает на «%s»%s"
                      % (canon, pat, form, "" if kept else " — правило пусто, снято целиком"))
        print("")
    if unscoped:
        print("[!] Правила с пустой зоной глав — сняты: %s" % ", ".join(unscoped))
        print("")
    return out


def canon_declension(hit, canon):
    """Найденное — склонение самого канона с той же капитализацией?

    Такое совпадение — шум, а не ошибка: «кнопочных существ» при каноне
    «кнопочное существо» — правильный родительный падеж, агенту там нечего
    делать. Но «костяным карликом» при каноне «Костяные карлики» — уже правка
    регистра, её пропускать нельзя: капитализация в этом реестре — часть
    написания. Поэтому сравнение пословное: основы совпадают И регистр первой
    буквы каждого слова совпадает.
    """
    hw, cw = hit.split(), canon.split()
    if len(hw) != len(cw):
        return False
    for h, c in zip(hw, cw):
        if h[:1].isupper() != c[:1].isupper():
            return False
        stem = max(min(len(c) - 3, len(h)), 3)
        if h.lower()[:stem] != c.lower()[:stem]:
            return False
    return True


def scan(text, fixes, idx=None):
    """Which canonical terms this chapter violates, with the variants found.

    Правило может быть привязано к главам. Это нужно там, где старое имя не
    ошибочно само по себе, а лишь занято другим термином: «Проникатель» — верное
    имя для 穿透者 и неверное для 滲透者. Общий по корпусу шаблон переписал бы обоих.
    """
    found = []
    for f in fixes:
        if f.get("chapters") and idx is not None and idx not in f["chapters"]:
            continue
        hits = []
        for rx in f["res"]:
            for m in rx.finditer(text):
                if m.group(0) not in hits:
                    hits.append(m.group(0))
        # strict: склонение канона здесь и есть ошибка (имя в кавычках не
        # склоняется: «разведотряд "Громовой Молот"» остаётся в именительном).
        if not f.get("strict"):
            hits = [h for h in hits if not canon_declension(h, f["canon"])]
        if hits:
            found.append((f, hits))
    return found


def table_for(found):
    rows = ["| Найдено в главе | Заменить на | Род |", "|---|---|---|"]
    for f, hits in found:
        rows.append("| %s | **%s** | %s |" % (
            ", ".join(sorted(hits)[:6]), f["canon"], f["gender"] or "-"))
    return "\n".join(rows)


def snippets_for(text, found):
    """Затронутые абзацы с номерами строк — агент читает только их.

    Полное чтение главы стоило ~24k кэш-токенов на вызов ради одного-двух
    абзацев. Сборщик и так знает, где совпадения; номера строк дают агенту
    честный Read нужного диапазона (правило «Read перед Edit» соблюдено),
    а сам абзац в задании — материал для old_string без угадывания.
    """
    lines = text.splitlines()
    para_bounds = []  # (первая строка, последняя, текст) для каждого абзаца
    start = None
    for n, line in enumerate(lines, 1):
        if line.strip():
            if start is None:
                start = n
        elif start is not None:
            para_bounds.append((start, n - 1, "\n".join(lines[start - 1:n - 1])))
            start = None
    if start is not None:
        para_bounds.append((start, len(lines), "\n".join(lines[start - 1:])))

    matched = []
    for lo, hi, ptext in para_bounds:
        for f, _hits in found:
            if any(rx.search(ptext) for rx in f["res"]):
                matched.append((lo, hi, ptext))
                break
    out = []
    for lo, hi, ptext in matched:
        out.append("**Строки %d–%d:**\n\n> %s" % (lo, hi, ptext.replace("\n", "\n> ")))
    return "\n\n".join(out) or "(совпадения не привязались к абзацам — прочитай главу целиком)"


def main():
    ap = argparse.ArgumentParser(description="Сборка заданий на правку терминологии")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", type=int, nargs="*")
    args = ap.parse_args()

    fixes = load_fixes()
    print("[+] Правил замены: %d" % len(fixes))

    names = sorted(n for n in os.listdir(RU_DIR)
                   if n.startswith("chapter_") and n.endswith(".txt"))
    if args.only:
        keep = {"chapter_%04d.txt" % i for i in args.only}
        names = [n for n in names if n in keep]

    if not args.dry_run:
        os.makedirs(FIX_DIR, exist_ok=True)

    prepared = []
    for name in names:
        idx = int(name[8:12])
        path = os.path.join(RU_DIR, name)
        text = read_text(path)
        found = scan(text, fixes, idx)
        if not found:
            continue
        paragraphs = len([p for p in text.split("\n\n") if p.strip()])
        print("    гл.%04d: %s" % (idx, ", ".join(f["canon"] for f, _ in found)))
        if not args.dry_run:
            # Задание пересобрано — прежние отметки .claim/.done/.fail к нему
            # больше не относятся; иначе драйвер молча пропустит главу.
            for ext in ("claim", "done", "fail"):
                stale = os.path.join(FIX_DIR, "fix_%04d.%s" % (idx, ext))
                if os.path.exists(stale):
                    os.remove(stale)
            write_text(
                os.path.join(FIX_DIR, "fix_%04d.md" % idx),
                TEMPLATE.format(
                    idx=idx,
                    path="ru/chapter_%04d.txt" % idx,
                    paragraphs=paragraphs,
                    table=table_for(found),
                    snippets=snippets_for(text, found),
                ),
            )
        prepared.append({
            "index": idx,
            "fix_file": "fixes/fix_%04d.md" % idx,
            "terms": [f["canon"] for f, _ in found],
            "paragraphs": paragraphs,
        })

    print("")
    print("=== ПРАВКА ТЕРМИНОЛОГИИ ===")
    print("Глав затронуто: %d из %d" % (len(prepared), len(names)))
    if args.dry_run:
        print("(пробный прогон, задания не собраны)")
        return
    save_json(os.path.join(FIX_DIR, "index.json"), prepared)
    print("Задания в fixes/. На каждое — агент типа termfix:")
    print('    "Файл задания: fixes/fix_0016.md"')


if __name__ == "__main__":
    main()
