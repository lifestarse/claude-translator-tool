#!/usr/bin/env python3
"""Штамповка тайтла в инструментарий: BOOK.md -> промпты агентов и скриптов.

Заполни BOOK.md (секции «Название» и «BRIEF»), затем:

    python setup_book.py            # показать, что будет заменено
    python setup_book.py --apply    # заменить

Замены идемпотентны только до применения: плейсхолдеры {{BOOK_BRIEF}},
{{BOOK_TITLE}}, {{BOOK_ORIG}}, {{BOOK_EN}}, {{BOOK_AUTHOR}} существуют в файлах ровно один раз — до первого --apply.
Сменить тайтл после штамповки = начать с чистой копии инструмента (git).
"""

import argparse
import glob
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

FILES = sorted(glob.glob(os.path.join(ROOT, ".claude", "agents", "*.md"))) + [
    os.path.join(ROOT, f) for f in ("translate.py", "build_reader_map.py", "build_epub.py")]
PLACEHOLDERS = ("BOOK_BRIEF", "BOOK_TITLE", "BOOK_ORIG", "BOOK_EN", "BOOK_AUTHOR")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    book = io.open(os.path.join(ROOT, "BOOK.md"), encoding="utf-8").read()
    if "SET-ME" in book.split("## Источник")[0]:
        raise SystemExit("Сначала заполни «Название» и «BRIEF» в BOOK.md (там остались SET-ME).")

    m = re.search(r"## BRIEF[^\n]*\n(.+?)\n\n", book, re.S)
    brief = " ".join(m.group(1).split()) if m else ""
    m = re.search(r"Русское[^:]*:\s*(.+)", book)
    title = (m.group(1).strip() if m else "").strip()
    if not brief or not title:
        raise SystemExit("Не смог вычитать BRIEF или русское название из BOOK.md.")

    def field(label):
        m = re.search(r"- %s[^:\n]*:\s*(.+)" % label, book)
        return m.group(1).strip() if m else ""

    values = {"BOOK_BRIEF": brief, "BOOK_TITLE": title,
              "BOOK_ORIG": field("Оригинал"), "BOOK_EN": field("Английское"),
              "BOOK_AUTHOR": field("Автор")}

    print("BRIEF :", brief[:100])
    print("TITLE :", title)
    total = 0
    for path in FILES:
        t = io.open(path, encoding="utf-8").read()
        n = sum(t.count("{{%s}}" % k) for k in PLACEHOLDERS)
        if not n:
            continue
        total += n
        print("  %s: %d замен" % (os.path.relpath(path, ROOT), n))
        if args.apply:
            for k in PLACEHOLDERS:
                t = t.replace("{{%s}}" % k, values[k])
            io.open(path, "w", encoding="utf-8", newline="\n").write(t)
    if not total:
        print("Плейсхолдеров не осталось — уже проштамповано.")
    elif not args.apply:
        print("Пробный прогон. Применить: python setup_book.py --apply")
    else:
        print("Готово. Не забудь константы: scraper.py (CATALOG_URL), publish_ranobelib.py (SLUG, TEAM_ID).")


if __name__ == "__main__":
    main()
