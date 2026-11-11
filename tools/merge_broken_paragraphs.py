#!/usr/bin/env python3
"""Склейка абзацев, между которыми разрезана одна фраза.

Читатель видит фразу, оборванную на полуслове, а следующий абзац начинается со
строчной буквы. Бывает двух видов:

  * разрезал источник: сайт рвёт абзац посреди предложения, переводчик честно
    держит 1:1 и переносит разрыв в перевод;
  * разрезал переводчик: автор ставит придаточное отдельным абзацем («…할 무렵.»
    — «когда…»), переводчик пристёгивает его запятой к следующему. Так было в
    308 местах 109 глав из 262. Деление на отдельные фразы («Пока репортёры
    снимали.» / «Образ героя изменился.») читатели отвергли — висящее
    придаточное читается как обрывок, — поэтому фраза склеивается целиком.

Склейка делается СИНХРОННО в оригинале (`chapters/`) и переводе (`ru/`): иначе
сломается соответствие абзацев 1:1, на котором держится вся приёмка. Русский
абзац склеивается со следующим, если тот начинается со строчной, а сам абзац
  * не закончен (запятая, двоеточие, тире или вовсе без знака) — фраза оборвана;
  * закончен репликой или звуком («— Угх!», «Звяк.»), а дальше идёт продолжение
    («и нас окутал дым») — реплика со словами автора соединяется через «, —».
Если оригинал свой абзац закончил, русский следующий должен начинаться со
строчной кириллицы (латинское «iPhone» в начале абзаца — не продолжение).

autorun.py зовёт склейку сразу после приёмки каждой главы. Заголовок (абзац 0)
не трогается никогда. После склейки глава проходит `validate_translation`;
если до правки глава приёмку проходила, а после — нет, правка не записывается.

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
LOWER_START_RE = re.compile(r"^[«\"„“(]?[a-zа-яё]")
CYR_LOWER_START_RE = re.compile(r"^([«\"„“(]?)([а-яё])")
OPEN_END_RE = re.compile(r"(?:[,;:—–\-]|[^\W_])$")
FINISHED_RE = re.compile(r"[.!?»]$")


def _is_break(cn_a, cn_b, ru_a, ru_b):
    if not (cn_a and cn_b and ru_a and ru_b):
        return False
    source_cut = CN_END is not None and cn_a[-1] not in CN_END and cn_b[0] not in CN_OPEN
    if ru_a.endswith(",") and re.match(r"[А-ЯЁA-Z]", ru_b):
        return True               # продолжение с имени: «…звук,» / «Ан Юнсын добрался…»
    if not (LOWER_START_RE if source_cut else CYR_LOWER_START_RE).match(ru_b):
        return False
    if OPEN_END_RE.search(ru_a):
        return True
    return bool(FINISHED_RE.search(ru_a)) and not ru_a.endswith("...")


def join_ru(a, b):
    """Склеить русскую фразу из двух абзацев. Реплика или мысль + слова автора — через «, —»."""
    if a.startswith("«") and re.search(r"»\.?$", a):
        return a.rstrip(".") + ", — " + b
    if a.startswith(("—", "–")):
        if a.endswith("."):
            a = a[:-1] + ","
        elif re.search(r"[^\W_]$", a):
            a += ","
        return a + " — " + b
    if FINISHED_RE.search(a):
        m = CYR_LOWER_START_RE.match(b)
        if m:
            b = m.group(1) + m.group(2).upper() + b[m.end():]
    return a + " " + b


def comma_before_speech(ru_paras):
    """Номера абзацев (с 1), которые кончаются запятой перед репликой, мыслью или
    сообщением. Склеить их нельзя — нужна правка руками или агентом."""
    return [i + 1 for i in range(1, len(ru_paras) - 1)
            if ru_paras[i].endswith(",") and ru_paras[i + 1].startswith(("—", "–", "«", "[", "("))]


def find_breaks(cn_paras, ru_paras):
    """Индексы i, где абзац i и i+1 — одна фраза, разрезанная источником."""
    if len(cn_paras) != len(ru_paras):
        return []
    return [i for i in range(1, len(cn_paras) - 1)
            if _is_break(cn_paras[i], cn_paras[i + 1], ru_paras[i], ru_paras[i + 1])]


def _merge(paras, breaks, sep):
    """sep — строка-разделитель (оригинал) или функция склейки (перевод)."""
    join = sep if callable(sep) else (lambda a, b: a + sep + b)
    out, skip = [], set(i + 1 for i in breaks)
    for i, p in enumerate(paras):
        if i in skip:
            out[-1] = join(out[-1], p)
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
    new_ru = "\n\n".join(_merge(ru_paras, breaks, join_ru)) + "\n"
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
    manual = []
    for idx in chapter_indices(lo, hi):
        n, detail = fix_chapter(idx, apply=args.apply)
        if n:
            touched += 1
            total += n
            print("гл.%04d: склеено %d (%s)" % (idx, n, detail))
        elif detail.startswith("склейка ломает"):
            print("гл.%04d: пропущено — %s" % (idx, detail))
        manual += [(idx, i) for i in comma_before_speech(split_paragraphs(read_text(ru_path(idx))))]
    print("Итого: %d склеек в %d главах%s"
          % (total, touched, "" if args.apply else " (пробный прогон, ничего не записано)"))
    if manual:
        print("Запятая перед репликой/кавычками — поправь руками или агентом (%d): %s"
              % (len(manual), ", ".join("гл.%d ¶%d" % m for m in manual[:20])))


if __name__ == "__main__":
    main()
