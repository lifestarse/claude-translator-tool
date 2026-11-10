#!/usr/bin/env python3
"""Independent audit of ru/ against chapters/.

Deliberately does not import the translator's state file: it re-derives every
verdict from the files on disk, so a lying pipeline cannot mark its own work
as done. Run it after any translation run.

    python verify.py            # summary
    python verify.py --detail   # per-chapter table
    python verify.py --quarantine  # move failing files to ru_rejected/
"""

import argparse
import os
import shutil

from lang import LANG
from common import (
    CHAPTERS_DIR,
    MAX_CHAR_RATIO,
    MIN_CHAR_RATIO,
    RU_DIR,
    ROOT,
    chapter_path,
    load_excluded,
    read_text,
    ru_path,
    translation_stats,
    validate_translation,
)

QUARANTINE_DIR = os.path.join(ROOT, "ru_rejected")


def source_indexes():
    out = []
    if not os.path.isdir(CHAPTERS_DIR):
        return out
    for name in sorted(os.listdir(CHAPTERS_DIR)):
        if name.startswith("chapter_") and name.endswith(".txt"):
            out.append(int(name[8:-4]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--detail", action="store_true")
    ap.add_argument("--quarantine", action="store_true", help="убрать битые переводы в ru_rejected/")
    args = ap.parse_args()

    sources = source_indexes()
    if not sources:
        print("В chapters/ нет ни одного файла.")
        raise SystemExit(1)

    # Excluded chapters must not count against coverage, or 100% is
    # unreachable and the number stops meaning anything.
    excluded = load_excluded()
    translatable = [i for i in sources if i not in excluded]

    passed, failed, orphans, excluded_but_translated = [], [], [], []

    for idx in sources:
        rp = ru_path(idx)
        if not os.path.exists(rp):
            continue
        if idx in excluded:
            excluded_but_translated.append((idx, excluded[idx]))
            continue
        source = read_text(chapter_path(idx))
        translated = read_text(rp)
        problems = validate_translation(source, translated)
        stats = translation_stats(source, translated)
        (failed if problems else passed).append((idx, stats, problems))

    # Translations with no source at all -- pure invention, the smoking gun
    # from the previous run (ru/chapter_0011.txt existed, chapters/ did not).
    if os.path.isdir(RU_DIR):
        have = set(sources)
        for name in sorted(os.listdir(RU_DIR)):
            if name.startswith("chapter_") and name.endswith(".txt"):
                idx = int(name[8:-4])
                if idx not in have:
                    orphans.append(idx)

    translated_count = len(passed) + len(failed)
    print("=== АУДИТ ПЕРЕВОДА ===")
    print("Исходных глав:            %d" % len(sources))
    print("Исключено из перевода:    %d (битые/подменённые в источнике)" % len(excluded))
    print("Подлежит переводу:        %d" % len(translatable))
    print("Переведено файлов:        %d (%.2f%% покрытия)"
          % (translated_count, 100.0 * translated_count / max(len(translatable), 1)))
    print("Прошли проверку:          %d" % len(passed))
    print("НЕ прошли проверку:       %d" % len(failed))
    print("Переводы без оригинала:   %d  <- выдуманы целиком" % len(orphans))
    if orphans:
        print("   индексы: %s" % orphans[:30])
    if excluded_but_translated:
        print("")
        print("[!] Переведены главы из списка исключений — их надо удалить:")
        for idx, reason in excluded_but_translated:
            print("    ru/chapter_%04d.txt (%s)" % (idx, reason))

    if passed:
        ratios = sorted(s["char_ratio"] for _, s, _ in passed)
        mid = ratios[len(ratios) // 2]
        print("Медианный коэф. объёма:   %.2fx (норма %.1f-%.1f, оригинал: %s)"
              % (mid, MIN_CHAR_RATIO, MAX_CHAR_RATIO, LANG.name))

    if failed:
        print("")
        print("=== ЗАБРАКОВАНО ===")
        for idx, stats, problems in failed[: (len(failed) if args.detail else 25)]:
            print("chapter_%04d.txt  %.2fx  %d/%d абз."
                  % (idx, stats["char_ratio"], stats["ru_paras"], stats["src_paras"]))
            for p in problems:
                print("    - %s" % p)
        if not args.detail and len(failed) > 25:
            print("... ещё %d. Запусти с --detail" % (len(failed) - 25))

    if args.detail and passed:
        print("")
        print("=== ПРОШЛИ ===")
        print("%-8s %8s %8s %7s %10s" % ("глава", "ориг", "перевод", "коэф", "абзацы"))
        for idx, s, _ in passed:
            print("%-8d %8d %8d %7.2f %6d/%-4d"
                  % (idx, s["src_chars"], s["ru_chars"], s["char_ratio"], s["ru_paras"], s["src_paras"]))

    if args.quarantine and (failed or orphans):
        os.makedirs(QUARANTINE_DIR, exist_ok=True)
        moved = 0
        for idx, _, _ in failed:
            shutil.move(ru_path(idx), os.path.join(QUARANTINE_DIR, "chapter_%04d.txt" % idx))
            moved += 1
        for idx in orphans:
            shutil.move(ru_path(idx), os.path.join(QUARANTINE_DIR, "chapter_%04d.txt" % idx))
            moved += 1
        print("")
        print("[+] Перемещено в ru_rejected/: %d файлов." % moved)

    raise SystemExit(0 if not failed and not orphans and not excluded_but_translated else 1)


if __name__ == "__main__":
    main()
