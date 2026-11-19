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
    ru_path,
    split_paragraphs,
    strip_title_thanks,
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
    held_back = []

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
        if not before:
            continue
        # Первая строка — заголовок, и чистится она отдельно. Через
        # clean_paragraphs он уходил целиком: «血氣（感謝大魚ZT的打賞）» для THANKS_RE
        # — записка автора, и 12 глав второй книги теряли заголовок, а с ним и
        # номер для author_numbers/manifest. С заголовка снимается только
        # скобочный хвост благодарности.
        after = [strip_title_thanks(before[0])] + clean_paragraphs(before[1:])

        if len(after) == len(before) and "\n\n".join(after) == "\n\n".join(before):
            continue

        # Глава уже переведена, а число абзацев оригинала меняется — перевод
        # перестанет с ним сходиться, и verify забракует исправную главу (так
        # было с 721, 816, 881: см. common.SPAM_MARKERS). Такие файлы не
        # переписываются: оригинал и ru/ чинить одним заходом, руками.
        if len(after) != len(before) and os.path.exists(ru_path(int(name[8:12]))):
            held_back.append((name, len(before) - len(after)))
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
    if held_back:
        print("")
        print("НЕ тронуто — глава уже переведена, а абзацев стало бы меньше (%d):"
              % len(held_back))
        for name, n in held_back[:20]:
            print("  %s: -%d абз. — править оригинал и ru/ одним заходом" % (name, n))
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
