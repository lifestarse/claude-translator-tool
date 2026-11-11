#!/usr/bin/env python3
"""Единое оформление прямой речи в готовом переводе — по разметке оригинала.

Правило перевода: реплики, в том числе по телефону (строка оригинала с «-» в начале),
— через тире «— »; мысли (‘…’ в оригинале) — в «ёлочках»; английских кавычек ‘…’
в русском тексте нет. Агенты держат его неровно: реплика уходит в «ёлочки» и
читается как мысль, телефонная — остаётся с дефисом.

Абзац правится, только если в оригинале он целиком реплика или мысль, а перевод
целиком оформлен иначе. Глава после правки проходит приёмку, иначе не записывается.

    python tools/fix_dialogue.py          показать
    python tools/fix_dialogue.py --apply  записать
"""
import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from common import (  # noqa: E402
    CHAPTERS_DIR, chapter_path, marker_count, read_text, ru_path, split_paragraphs, validate_translation, write_text,
)

SPEECH_OPEN = ("“", "\"", "「", "『")
THOUGHT_OPEN = ("‘", "'")
WHOLE_GUILL = re.compile(r"^«([^«»]+)»([.!?…]*)$")
WHOLE_SINGLE = re.compile(r"^[‘']([^‘’']+)[’']([.!?…]*)$")


def close(inner, tail):
    inner = inner.rstrip()
    if tail and not inner.endswith(("!", "?", "…", ".")):
        return inner + tail
    if not inner.endswith(("!", "?", "…", ".", ",", "—", "~")):
        return inner + "."
    return inner


def fix(src, ru):
    phone = src.startswith("-") and not src.startswith("--")
    if src.startswith(SPEECH_OPEN) or phone:
        m = WHOLE_GUILL.match(ru)
        if m:
            return "— " + close(m.group(1), m.group(2))
        if phone and ru.startswith("-") and len(ru) > 1 and ru[1] not in "-♪♬♩":
            return "— " + ru[1:].lstrip()
    if src.startswith(THOUGHT_OPEN):
        m = WHOLE_SINGLE.match(ru)
        if m:
            inner, tail = m.group(1).rstrip(), m.group(2)
            if inner.endswith(".") and not inner.endswith(".."):
                inner, tail = inner[:-1], tail or "."
            if inner.endswith(("!", "?", "…", "...")):
                tail = ""
            return "«%s»%s" % (inner, tail)
    return ru


def main():
    ap = argparse.ArgumentParser(description="Единое оформление прямой речи")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    total = chapters = shown = 0
    for f in sorted(os.listdir(CHAPTERS_DIR)):
        if not (f.startswith("chapter_") and f.endswith(".txt")):
            continue
        n = int(f[8:-4])
        if not os.path.exists(ru_path(n)):
            continue
        src_text, ru_text = read_text(chapter_path(n)), read_text(ru_path(n))
        s, r = split_paragraphs(src_text), split_paragraphs(ru_text)
        if len(s) != len(r) or marker_count(r) > marker_count(s):
            continue
        new = [r[0]] + [fix(a, b) for a, b in zip(s[1:], r[1:])]
        changed = [(a, b) for a, b in zip(r, new) if a != b]
        if not changed:
            continue
        out = "\n\n".join(new) + "\n"
        if validate_translation(src_text, out):
            print("гл.%04d: правка не прошла приёмку, пропущена" % n)
            continue
        total += len(changed)
        chapters += 1
        if args.apply:
            write_text(ru_path(n), out)
        else:
            for a, b in changed:
                if shown < 12:
                    print("  %04d: %s\n     -> %s" % (n, a[:90], b[:90]))
                    shown += 1
    print("Абзацев: %d в %d главах%s" % (total, chapters, "" if args.apply else " (пробный прогон)"))


if __name__ == "__main__":
    main()
