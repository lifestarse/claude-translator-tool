#!/usr/bin/env python3
"""Drop translated author's notes from accepted chapters after a corpus clean.

When clean_corpus.py learns a new spam family, every chapter already translated
against the old rules is left one or more paragraphs long: the note was still in
the source when the translator saw it, so it exists in Russian too. verify.py
then rejects all of them and autorun retranslates -- 75 chapters at ~27k tokens
each, to delete a line that says "thanks for the tip".

This removes those paragraphs instead. It is deliberately unwilling: a chapter
is only touched when the notes it finds account for the paragraph gap EXACTLY.
Anything else is left for a human, because guessing which paragraph to delete
from an accepted translation is how prose gets lost.

    python resync_ru.py --dry-run
    python resync_ru.py

Chapters it refuses are reported and should go to verify.py --quarantine.
"""

import argparse
import os
import re
import sys

from common import (
    CHAPTERS_DIR,
    RU_DIR,
    read_text,
    split_paragraphs,
    write_text,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# What a translated author's note looks like in Russian. Every pattern needs a
# donation/vote/postscript signal -- bare "спасибо" is dialogue and appears in
# the novel constantly ("Спасибо, предсказатель").
NOTE_PATTERNS = (
    # Postscript markers. The translator rendered the source's "ps:" a dozen
    # different ways, so all of them are listed rather than guessed at.
    r"^\s*(?:p\.?\s?s\.?|п\.?\s?с\.?)\s*[:：.]",
    r"^\s*(?:постскриптум|примечание\s+автора|от\s+автора)\s*[:：.]",
    r"(?:спасибо|благодар)\w*[^.!?\n]{0,60}(?:донат|пожертв|подар|"
    r"обложк|красн\w+\s+конверт|день\s?рожд)",
    r"донат\w*\s*[~～]",
    r"(?:спасибо|благодар)\w*\s*[~～][^\n]{0,40}[~～]",     # ~nickname~ form
    r"голосуйте[^.!?\n]{0,40}(?:обновлен|билет)",
    # Thanks for a sum. The gap runs to 80 because the notes carry the donor's
    # nickname between the two, and nicknames are long ("Как ни велико умение —
    # судьбе не переспорить, 588"). Still bounded, and still barred from
    # crossing a sentence end.
    r"(?:спасибо|благодар)\w*[^.!?\n]{0,80}\d{3,}",
)
NOTE_RE = re.compile("|".join(NOTE_PATTERNS), re.I)

# A note is short. This is the same guard common.THANKS_MAX_LEN exists for: a
# long paragraph that merely mentions gratitude is a scene, not a note.
NOTE_MAX_LEN = 200


def note_indices(paragraphs):
    """Indices of paragraphs that read as an author's note, tail-biased.

    Only the last quarter of the chapter is considered. The Qidian promo does
    sit at the top, but it was never a paragraph of its own in Russian -- the
    translator folded it into the scene -- so a head-position match here would
    only ever be a false positive.
    """
    start = max(0, int(len(paragraphs) * 0.75) - 1)
    out = []
    for i in range(start, len(paragraphs)):
        p = paragraphs[i]
        if len(p) <= NOTE_MAX_LEN and NOTE_RE.search(p):
            out.append(i)
    return out


def main():
    ap = argparse.ArgumentParser(
        description="Снятие переведённых авторских приписок из принятых глав")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--samples", type=int, default=10)
    args = ap.parse_args()

    files = sorted(
        f for f in os.listdir(RU_DIR)
        if f.startswith("chapter_") and f.endswith(".txt")
    )

    fixed, refused, samples = 0, [], []
    for name in files:
        src = os.path.join(CHAPTERS_DIR, name)
        if not os.path.exists(src):
            continue
        n_src = len(split_paragraphs(read_text(src)))
        ru_path_ = os.path.join(RU_DIR, name)
        ru_ps = split_paragraphs(read_text(ru_path_))
        gap = len(ru_ps) - n_src
        if gap <= 0:
            continue                      # aligned, or short -- not our problem

        idx = note_indices(ru_ps)
        if len(idx) != gap:
            # Refuse rather than pick. Too few means the note was worded in a
            # way these patterns miss; too many means one of the matches is
            # prose, and deleting the wrong one is unrecoverable.
            refused.append((name, n_src, len(ru_ps), gap, len(idx)))
            continue

        kept = [p for i, p in enumerate(ru_ps) if i not in set(idx)]
        if len(samples) < args.samples:
            samples.append((name, [ru_ps[i] for i in idx]))
        fixed += 1
        if not args.dry_run:
            write_text(ru_path_, "\n\n".join(kept) + "\n")

    print("=== СНЯТИЕ АВТОРСКИХ ПРИПИСОК ИЗ ПРИНЯТЫХ ГЛАВ ===")
    print("Проверено глав          : %d" % len(files))
    print("Выровнено               : %d" % fixed)
    print("Отказано (нужен человек): %d" % len(refused))
    if args.dry_run:
        print("")
        print("(пробный прогон, файлы не изменены)")

    if samples:
        print("")
        print("Что удаляется:")
        for name, notes in samples:
            print("  %s" % name)
            for p in notes:
                print("     - %s" % p[:96])

    if refused:
        print("")
        print("Отказано — приписки не покрывают расхождение точно:")
        for name, a, b, gap, found in refused:
            print("  %s  источник=%d ru=%d  лишних=%d, найдено приписок=%d"
                  % (name, a, b, gap, found))
        print("")
        print("Эти главы переводить заново: python verify.py --quarantine")

    raise SystemExit(0)


if __name__ == "__main__":
    main()
