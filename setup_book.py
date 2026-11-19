#!/usr/bin/env python3
"""Штамповка тайтла в инструментарий: BOOK.md -> промпты агентов и скриптов.

Заполни BOOK.md (секции «Название» и «BRIEF»), затем:

    python setup_book.py            # показать, что будет заменено
    python setup_book.py --apply    # заменить

Плейсхолдеры: {{BOOK_BRIEF}}, {{BOOK_TITLE}} (русское), {{BOOK_TITLE_CN}} / {{BOOK_ORIG}},
{{BOOK_TITLE_EN}} / {{BOOK_EN}}, {{BOOK_AUTHOR}} и языковые из lang.py: {{SRC_ADJ_F}},
{{SRC_FROM}}, {{SRC_TRANSCRIPTION}}, {{SRC_RATIO}}. Замены идемпотентны только до
применения: плейсхолдеры существуют в файлах ровно один раз — до первого --apply.
Сменить тайтл после штамповки = начать с чистой копии инструмента (git).

build_epub.py и config_ui.py не штампуются: они читают BOOK.md при запуске
(read_book ниже), поэтому правка BOOK.md доходит до них и после штамповки.
Сюжетные эпохи тоже не штампуются: они заполняются по ходу перевода, агенты
glossmap/glossmapcheck читают секцию BOOK.md сами.

После штамповки (и при пробном прогоне) скрипт ищет остатки прошлой книги —
OLD_BOOK_MARKERS — и печатает каждое место: шаблоны инструмента писались под
неё, и не всё в них было плейсхолдером.
"""

import argparse
import glob
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from lang import LANG  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
BOOK_MD = os.path.join(HERE, "BOOK.md")

FILES = glob.glob(os.path.join(HERE, ".claude", "agents", "*.md")) + [
    os.path.join(HERE, f) for f in ("translate.py", "build_reader_map.py")]

# Следы прошлой книги («进化的四十六亿重奏», czbooks ui63l, 4884 главы, герой Линь).
# Каждый уже раз пролез в новую книгу мимо штамповки: название в judge/arbiter/
# fixer/proofreader, подзаголовок и автор в build_epub, «4885 глав» в fixer.
# Находка — не обязательно ошибка (история в комментариях законна), но смотреть
# глазами надо каждую.
OLD_BOOK_MARKERS = re.compile(
    r"进化的四十六亿|進化的四十六億|四十六亿重奏|四十六億重奏|相位行者|Xiangwei|"
    r"Symphony of Evolution|Billion.Year|ui63l|s62gnp|\b488[45]\b|"
    r"\bЛинь\b|\bЛиня\b|\bЛиню\b|\bЛинем\b|[Сс]имфони|SIMPHONY|4[.,]6 миллиард")
# Где искать остатки: код и промпты инструмента, не данные тайтла.
SCAN_GLOBS = ("*.py", "*.md", "*.ps1", "tools/*.py", ".claude/agents/*.md")
SCAN_SKIP = {"setup_book.py", "BOOK.md"}
PER_FILE = 5     # файл, захардкоженный целиком (glossary_builder), не должен топить остальные


def _field(book, label):
    """Значение строки «- <label>…: значение» из BOOK.md; SET-ME и пусто -> ''."""
    m = re.search(r"^-\s*" + label + r"[^:\n]*:[ \t]*(.*)$", book, re.M)
    v = m.group(1).strip() if m else ""
    return "" if "SET-ME" in v else v


def read_book(path=BOOK_MD):
    """Метаданные тайтла из BOOK.md. Незаполненное (SET-ME) приходит пустой строкой.

    Общий разбор для штамповки, build_epub.py и config_ui.py — чтобы название
    на обложке EPUB, в пульте и в промптах бралось из одного места.
    """
    book = io.open(path, encoding="utf-8").read()
    m = re.search(r"## BRIEF[^\n]*\n(.+?)(?:\n\n|\n## |\Z)", book, re.S)
    brief = " ".join(m.group(1).split()) if m else ""
    if "SET-ME" in brief:
        brief = ""
    m = re.search(r"## Сюжетные эпохи[^\n]*\n(.*?)(?:\n## |\Z)", book, re.S)
    epochs = m.group(1).strip() if m else ""
    return {
        "brief": brief,
        "title": _field(book, "Русское"),
        "title_cn": _field(book, r"Оригинал"),
        "title_en": _field(book, "Английское"),
        "author": _field(book, "Автор"),
        "cover": _field(book, "Обложка"),
        "epochs": epochs,
    }


def brief_sentence(brief):
    """BRIEF законченным предложением: ровно одна точка в конце.

    В шаблонах бывает «{{BOOK_BRIEF}}.», а BRIEF в BOOK.md сам кончается точкой —
    так получались «…Су Хао..» в трёх агентах. Свою точку шаблона съедаем.
    """
    b = brief.rstrip()
    return b if b.endswith((".", "!", "?", "…")) else b + "."


def scan_old_book():
    """Печатает строки инструмента с маркерами прошлой книги. Возвращает их число."""
    seen, hits = set(), 0
    for pattern in SCAN_GLOBS:
        for path in sorted(glob.glob(os.path.join(HERE, pattern))):
            rel = os.path.relpath(path, HERE)
            if rel in seen or os.path.basename(path) in SCAN_SKIP:
                continue
            seen.add(rel)
            try:
                lines = io.open(path, encoding="utf-8").read().splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            found = 0
            for i, line in enumerate(lines, 1):
                m = OLD_BOOK_MARKERS.search(line)
                if not m:
                    continue
                found += 1
                if found <= PER_FILE:
                    s = line.strip()
                    print("  %s:%d  [%s]  %s" % (rel, i, m.group(0), s[:110] + ("…" if len(s) > 110 else "")))
            if found > PER_FILE:
                print("  %s: …ещё %d строк" % (rel, found - PER_FILE))
            hits += found
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    meta = read_book()
    if not meta["brief"] or not meta["title"]:
        raise SystemExit("Сначала заполни «Русское» название и «BRIEF» в BOOK.md (там SET-ME или пусто).")

    values = {
        "{{BOOK_BRIEF}}": brief_sentence(meta["brief"]),
        "{{BOOK_TITLE}}": meta["title"],
        "{{BOOK_TITLE_CN}}": meta["title_cn"],
        "{{BOOK_TITLE_EN}}": meta["title_en"],
        "{{BOOK_AUTHOR}}": meta["author"],
        # Те же поля под именами серверной ветки каркаса.
        "{{BOOK_ORIG}}": meta["title_cn"],
        "{{BOOK_EN}}": meta["title_en"],
        # Язык оригинала (BOOK.md -> lang.py): «китайской», «с китайского».
        "{{SRC_ADJ_F}}": LANG.adj_f,
        "{{SRC_FROM}}": LANG.from_,
        "{{SRC_TRANSCRIPTION}}": LANG.transcription,
        "{{SRC_RATIO}}": "от %.1f до %.1f объёма оригинала"
                         % (LANG.min_ratio, LANG.max_ratio),
    }
    print("ЯЗЫК  : %s (%s)%s" % (LANG.name, LANG.code,
          "" if LANG.calibrated else " — норма объёма оценочная, см. python lang.py"))
    print("BRIEF :", meta["brief"][:100])
    print("TITLE :", meta["title"], "|", meta["title_cn"] or "—", "|", meta["title_en"] or "—")
    total, empty = 0, []
    for path in FILES:
        rel = os.path.relpath(path, HERE)
        t = io.open(path, encoding="utf-8").read()
        found = {k: t.count(k) for k in values if k in t}
        if not found:
            empty.append(rel)
            continue
        missing = [k for k in found if not values[k]]
        if missing:
            raise SystemExit("%s просит %s, а в BOOK.md это поле не заполнено (SET-ME)." % (rel, ", ".join(missing)))
        n = sum(found.values())
        total += n
        print("  %s: %d замен" % (rel, n))
        if args.apply:
            # «{{BOOK_BRIEF}}.» — раньше общей замены, иначе точка шаблона встанет второй
            t = t.replace("{{BOOK_BRIEF}}.", values["{{BOOK_BRIEF}}"])
            for k, v in values.items():
                t = t.replace(k, v)
            io.open(path, "w", encoding="utf-8", newline="\n").write(t)
    if not total:
        print("Плейсхолдеров не осталось — уже проштамповано.")
    else:
        if empty:
            # На чистой копии каждый файл из FILES должен что-то принимать; молчаливые
            # 0 замен — это захардкоженная прошлая книга (так было с build_epub.py).
            print("Внимание: без плейсхолдеров (проверь глазами): " + ", ".join(empty))
        if not args.apply:
            print("Пробный прогон. Применить: python setup_book.py --apply")
        else:
            print("Готово.")

    print("\nОстатки прошлой книги в коде и промптах:")
    hits = scan_old_book()
    print("  не найдено" if not hits else "  всего %d — каждое место проверь глазами." % hits)

    print("\nНе штампуется, проверь руками:")
    print("  - scraper.py: CATALOG_URL; common.py: семейства SPAM_* под сайт и автора;")
    print("  - publish_ranobelib.py: SLUG, TEAM_ID; build_arbitration.py: MAX_CHAPTERS_SCANNED;")
    print("  - BOOK.md: «Автор» и «Обложка EPUB» (их читает build_epub.py), «Сюжетные эпохи» — по ходу перевода.")
    if not meta["author"]:
        print("  ! «Автор» в BOOK.md не заполнен — build_epub.py откажется собирать книгу.")


if __name__ == "__main__":
    main()
