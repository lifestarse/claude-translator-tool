#!/usr/bin/env python3
"""Штамповка тайтла в инструментарий: BOOK.md -> промпты агентов и скриптов.

Заполни BOOK.md (секции «Название» и «BRIEF»), затем:

    python setup_book.py            # показать, что будет заменено
    python setup_book.py --apply    # заменить

Замены идемпотентны только до применения: плейсхолдеры {{BOOK_BRIEF}} и
{{BOOK_TITLE}} существуют в файлах ровно один раз — до первого --apply.
Сменить тайтл после штамповки = начать с чистой копии инструмента (git).
"""

import argparse
import glob
import io
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

FILES = glob.glob(".claude/agents/*.md") + ["translate.py", "build_reader_map.py", "build_epub.py"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    book = io.open("BOOK.md", encoding="utf-8").read()
    if "SET-ME" in book.split("## Источник")[0]:
        raise SystemExit("Сначала заполни «Название» и «BRIEF» в BOOK.md (там остались SET-ME).")

    m = re.search(r"## BRIEF[^\n]*\n(.+?)\n\n", book, re.S)
    brief = " ".join(m.group(1).split()) if m else ""
    m = re.search(r"Русское[^:]*:\s*(.+)", book)
    title = (m.group(1).strip() if m else "").strip()
    if not brief or not title:
        raise SystemExit("Не смог вычитать BRIEF или русское название из BOOK.md.")

    print("BRIEF :", brief[:100])
    print("TITLE :", title)
    total = 0
    for path in FILES:
        t = io.open(path, encoding="utf-8").read()
        n = t.count("{{BOOK_BRIEF}}") + t.count("{{BOOK_TITLE}}")
        if not n:
            continue
        total += n
        print("  %s: %d замен" % (path, n))
        if args.apply:
            t = t.replace("{{BOOK_BRIEF}}", brief).replace("{{BOOK_TITLE}}", title)
            io.open(path, "w", encoding="utf-8", newline="\n").write(t)
    if not total:
        print("Плейсхолдеров не осталось — уже проштамповано.")
    elif not args.apply:
        print("Пробный прогон. Применить: python setup_book.py --apply")
    else:
        print("Готово. Не забудь константы: scraper.py (CATALOG_URL), publish_ranobelib.py (SLUG, TEAM_ID).")


if __name__ == "__main__":
    main()
