#!/usr/bin/env python3
"""Предложения, разрезанные между абзацами, в уже готовых главах.

Признак: абзац перевода кончается запятой (или без знака), следующий начинается со
строчной. Новые главы чинит сам finalize.py (repair_split_sentences) — этот скрипт
применяет то же правило к главам, переведённым раньше:

  разрезал переводчик (абзац оригинала закончен) — каждый абзац становится
      законченной фразой: запятая -> точка, следующий с заглавной, союз с конца
      первого переезжает во второй. Абзацы остаются 1:1 с оригиналом;
  разорван сам оригинал — не трогаем: обе стороны склеивает
      tools/merge_broken_paragraphs.py --apply.

Механическая парцелляция («Когда я вернулся.» / «Дома никого не было.») иногда
читается угловато — такие места стоит отдать на вычитку редактору или агенту.

    python tools/fix_split.py           показать
    python tools/fix_split.py --apply   записать (каждая глава проходит приёмку)
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from common import (  # noqa: E402
    CHAPTERS_DIR, chapter_path, marker_count, read_text, ru_path, split_paragraphs, validate_translation, write_text,
)
from finalize import LOWER_START_RE, OPEN_END_RE, SRC_END, repair_split_sentences  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Предложения, разрезанные между абзацами")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    fixed = chapters = source_broken = shown = 0
    for f in sorted(os.listdir(CHAPTERS_DIR)):
        if not (f.startswith("chapter_") and f.endswith(".txt")):
            continue
        n = int(f[8:-4])
        if not os.path.exists(ru_path(n)):
            continue
        src_text, ru_text = read_text(chapter_path(n)), read_text(ru_path(n))
        src, ru = split_paragraphs(src_text), split_paragraphs(ru_text)
        if len(src) != len(ru) or marker_count(ru) > marker_count(src):
            continue
        source_broken += sum(1 for i in range(1, len(ru) - 1)
                             if OPEN_END_RE.search(ru[i]) and LOWER_START_RE.match(ru[i + 1])
                             and src[i].rstrip()[-1:] not in SRC_END)
        new = list(ru)
        count = repair_split_sentences(src, new)
        if not count:
            continue
        out = "\n\n".join(new) + "\n"
        if validate_translation(src_text, out):
            print("гл.%04d: правка не прошла приёмку, пропущена" % n)
            continue
        fixed += count
        chapters += 1
        if args.apply:
            write_text(ru_path(n), out)
        else:
            for i, (a, b) in enumerate(zip(ru, new)):
                if a != b and shown < 10:
                    print("  %04d ¶%d: %s\n        -> %s" % (n, i + 1, a[-80:], b[-80:]))
                    shown += 1
    print("Разрезанных переводчиком: %d пар в %d главах%s" % (fixed, chapters, "" if args.apply else " (пробный прогон)"))
    if source_broken:
        print("Разорвано в самом оригинале: %d пар — склей обе стороны: python tools/merge_broken_paragraphs.py --apply"
              % source_broken)


if __name__ == "__main__":
    main()
