#!/usr/bin/env python3
"""Склейка абзацев, разрезанных источником посреди фразы.

Сайт-источник иногда рвёт один абзац на два посреди предложения. Переводчик
честно держит 1:1 и переносит разрыв в перевод — читатель видит фразу,
оборванную на полуслове, а следующий абзац начинается со строчной буквы.

Склейка делается СИНХРОННО в оригинале (`chapters/`) и переводе (`ru/`): иначе
сломается соответствие абзацев 1:1, на котором держится вся приёмка.
Разрыв признаётся только при согласии обеих сторон:

  * абзац оригинала кончается не знаком конца фразы, а следующий не открывает
    реплику;
  * русский абзац кончается не знаком конца фразы, а следующий начинается со
    строчной буквы.

Заголовок (абзац 0) не трогается никогда. После склейки глава проходит
`validate_translation`; если до правки глава приёмку проходила, а после — нет,
правка не записывается.

    python tools/merge_broken_paragraphs.py                 пробный прогон
    python tools/merge_broken_paragraphs.py --apply         записать
    python tools/merge_broken_paragraphs.py --range 1 500 --apply
"""

import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lang import LANG  # noqa: E402
from common import (  # noqa: E402
    RU_DIR,
    chapter_path,
    marker_count,
    read_text,
    ru_path,
    split_paragraphs,
    validate_translation,
    write_text,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Конец фразы и открытие реплики в оригинале зависят от языка (lang.py).
# None — в письменности нет надёжного знака конца фразы (тайский): тогда
# решает только русская сторона.
CN_END = set(LANG.end) if LANG.end is not None else None
CN_OPEN = set(LANG.open)
# Чем склеивать половины в оригинале: в письменностях без пробелов — ничем.
SRC_JOIN = "" if LANG.code in ("zh", "ja", "th") else " "
RU_END = set(".!?…»”\")]:;—–-")
LOWER_START_RE = re.compile(r"^[«\"„“(]?[a-zа-яё]")


def _is_break(cn_a, cn_b, ru_a, ru_b):
    if not (cn_a and cn_b and ru_a and ru_b):
        return False
    if CN_END is not None and (cn_a[-1] in CN_END or cn_b[0] in CN_OPEN):
        return False
    if ru_a[-1] in RU_END:
        return False
    return bool(LOWER_START_RE.match(ru_b))


def find_breaks(cn_paras, ru_paras):
    """Индексы i, где абзац i и i+1 — одна фраза, разрезанная источником."""
    if len(cn_paras) != len(ru_paras):
        return []
    return [i for i in range(1, len(cn_paras) - 1)
            if _is_break(cn_paras[i], cn_paras[i + 1], ru_paras[i], ru_paras[i + 1])]


def _merge(paras, breaks, sep):
    out, skip = [], set(i + 1 for i in breaks)
    for i, p in enumerate(paras):
        if i in skip:
            out[-1] = out[-1] + sep + p
        else:
            out.append(p)
    return out


def fix_chapter(idx, apply=False):
    """(число склеек, пояснение). При apply=True пишет обе стороны."""
    src_file, ru_file = chapter_path(idx), ru_path(idx)
    if not (os.path.exists(src_file) and os.path.exists(ru_file)):
        return 0, "нет оригинала или перевода"
    source, translated = read_text(src_file), read_text(ru_file)
    cn_paras, ru_paras = split_paragraphs(source), split_paragraphs(translated)
    # Служебные маркеры [N] — глава ещё не финализирована, трогать её рано.
    if marker_count(ru_paras) > marker_count(cn_paras):
        return 0, "не финализирована"
    breaks = find_breaks(cn_paras, ru_paras)
    if not breaks:
        return 0, ""

    new_source = "\n\n".join(_merge(cn_paras, breaks, SRC_JOIN)) + "\n"
    new_ru = "\n\n".join(_merge(ru_paras, breaks, " ")) + "\n"
    was_ok = not validate_translation(source, translated)
    problems = validate_translation(new_source, new_ru)
    if was_ok and problems:
        return 0, "склейка ломает приёмку: %s" % problems[0]

    if apply:
        write_text(src_file, new_source)
        write_text(ru_file, new_ru)
    return len(breaks), "абзацы %s" % ", ".join(str(i + 1) for i in breaks[:10])


def chapter_indices(lo=None, hi=None):
    out = []
    if not os.path.isdir(RU_DIR):
        return out
    for name in os.listdir(RU_DIR):
        m = re.fullmatch(r"chapter_(\d+)\.txt", name)
        if not m:
            continue
        idx = int(m.group(1))
        if (lo is None or idx >= lo) and (hi is None or idx <= hi):
            out.append(idx)
    return sorted(out)


def main():
    ap = argparse.ArgumentParser(description="Склейка абзацев, разрезанных источником")
    ap.add_argument("--apply", action="store_true", help="записать изменения")
    ap.add_argument("--range", nargs=2, type=int, metavar=("LO", "HI"))
    args = ap.parse_args()

    lo, hi = args.range if args.range else (None, None)
    total = touched = 0
    for idx in chapter_indices(lo, hi):
        n, detail = fix_chapter(idx, apply=args.apply)
        if n:
            touched += 1
            total += n
            print("гл.%04d: склеено %d (%s)" % (idx, n, detail))
        elif detail.startswith("склейка ломает"):
            print("гл.%04d: пропущено — %s" % (idx, detail))
    print("Итого: %d склеек в %d главах%s"
          % (total, touched, "" if args.apply else " (пробный прогон, ничего не записано)"))


if __name__ == "__main__":
    main()
