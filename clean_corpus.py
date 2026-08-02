#!/usr/bin/env python3
"""Strip czbooks watermarks from already-downloaded chapters.

The site injects piracy watermarks and vote solicitations into chapter bodies.
Left alone they get handed to the translator agents as if they were prose, and
they poison glossary extraction. See common.SPAM_MARKERS for the details.

Always dry-run first and read the samples -- this rewrites source files.

    python clean_corpus.py --dry-run
    python clean_corpus.py
"""

import argparse
import os
import sys

from common import (
    CHAPTERS_DIR,
    clean_paragraphs,
    read_text,
    split_paragraphs,
    write_text,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="Удаление спама сайта из скачанных глав")
    ap.add_argument("--dry-run", action="store_true", help="только показать, ничего не менять")
    ap.add_argument("--samples", type=int, default=12)
    args = ap.parse_args()

    files = sorted(
        f for f in os.listdir(CHAPTERS_DIR) if f.startswith("chapter_") and f.endswith(".txt")
    )
    if not files:
        print("В chapters/ пусто.")
        raise SystemExit(1)

    touched = 0
    paras_dropped = 0
    chars_removed = 0
    truncated_samples = []

    for name in files:
        path = os.path.join(CHAPTERS_DIR, name)
        text = read_text(path)
        # No pre-filter here on purpose. Gating on a subset of the spam families
        # silently reported "0 files touched" twice -- once for the 308 chapters
        # carrying only SPAM_SPANS footers, again for the 325 carrying only
        # author's notes. clean_paragraphs is the single source of truth for
        # what counts as spam, and the before/after comparison below already
        # skips untouched files, so the gate bought nothing and cost two bugs.
        before = split_paragraphs(text)
        after = clean_paragraphs(before)

        if len(after) == len(before) and "\n\n".join(after) == "\n\n".join(before):
            continue

        touched += 1
        paras_dropped += len(before) - len(after)
        chars_removed += sum(len(p) for p in before) - sum(len(p) for p in after)

        # Record cases where story text survived alongside the cut, so the
        # samples show what was preserved rather than only what was deleted.
        if len(truncated_samples) < args.samples:
            for old, new in zip(before, after):
                if old != new and new:
                    truncated_samples.append((name, old[-46:], new[-30:]))
                    break

        if not args.dry_run:
            write_text(path, "\n\n".join(after) + "\n")

    print("=== ОЧИСТКА ОТ СПАМА САЙТА ===")
    print("Проверено файлов        : %d" % len(files))
    print("Затронуто файлов        : %d" % touched)
    print("Удалено абзацев целиком : %d" % paras_dropped)
    print("Удалено символов        : %d" % chars_removed)
    if args.dry_run:
        print("")
        print("(пробный прогон, файлы не изменены)")

    if truncated_samples:
        print("")
        print("Примеры обрезки — сюжетный текст сохранён, хвост срезан:")
        for name, old, new in truncated_samples:
            print("  %s" % name)
            print("     было : ...%s" % old)
            print("     стало: ...%s" % new)

    raise SystemExit(0)


if __name__ == "__main__":
    main()
