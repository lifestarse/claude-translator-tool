#!/usr/bin/env python3
"""Раскладка готовых переводов по авторской нумерации.

`ru/chapter_NNNN.txt` назван по позиции страницы в каталоге сайта — это не номер
главы. Здесь те же тексты раскладываются так, как книга нумерует себя сама:

    ru_by_author/
        vol_01/0001 Первая клетка.txt
        vol_04/0216 После войны.txt
        vol_04/0216.1 Город, ведущий в бездну.txt   <- повтор номера в источнике
        index.json    файл-источник <-> том/номер, машиночитаемо
        REPORT.md     всё, что при разметке пришлось решать

Ничего не удаляет и не переписывает в `ru/` — только копирует. Повторный запуск
пересобирает папку с нуля (`--clean`), иначе дописывает недостающее.

    python split_by_author.py            # разложить всё готовое
    python split_by_author.py --clean    # снести ru_by_author и собрать заново
"""

import argparse
import json
import os
import re
import shutil
import sys

from author_numbers import build, _numeric_key
from common import ROOT, load_json, read_text, ru_path, split_paragraphs, write_text

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

OUT_DIR = os.path.join(ROOT, "ru_by_author")

# «Глава 44: Путь», «Главы 11-12: ...» — номер уже стоит в имени файла, в
# названии он лишний. Дефис внутри диапазона обязан сниматься вместе с ним:
# пока он числился завершающим знаком, в названии оставалось «12:».
TITLE_PREFIX_RE = re.compile(r"^.*?глав[аы]\s*\d+(?:\s*[-–—]\s*\d+)?\s*[:.]\s*", re.I)
# Windows не даёт этих знаков в именах файлов, а в названиях глав они есть
# («Создание или эволюция?»).
FORBIDDEN_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_name(title, limit=70):
    name = FORBIDDEN_RE.sub("", title).strip().rstrip(". ")
    name = re.sub(r"\s+", " ", name)
    return name[:limit].rstrip(". ") or "без названия"


def ru_title(idx):
    paras = split_paragraphs(read_text(ru_path(idx)))
    if not paras:
        return ""
    head = paras[0].strip()
    return TITLE_PREFIX_RE.sub("", head).strip() or head


def main():
    ap = argparse.ArgumentParser(description="Разложить переводы по авторской нумерации")
    ap.add_argument("--out", default=OUT_DIR)
    ap.add_argument("--clean", action="store_true", help="пересобрать папку с нуля")
    ap.add_argument("--range", type=int, nargs=2, metavar=("LO", "HI"),
                    help="только эти индексы файлов")
    args = ap.parse_args()

    data = build()
    mapping = data["map"]

    ready = sorted(i for i in mapping if os.path.exists(ru_path(i)))
    if args.range:
        lo, hi = args.range
        ready = [i for i in ready if lo <= i <= hi]

    if args.clean and os.path.isdir(args.out):
        shutil.rmtree(args.out)

    entries = []
    for idx in ready:
        vol, num = mapping[idx]
        title = ru_title(idx)
        # Номер дополняется нулями слева, чтобы список файлов сортировался по
        # книге, а не по строке: иначе «глава 10» встаёт перед «главой 2».
        base, _, frac = num.partition(".")
        stem = "%04d" % int(base) + ("." + frac if frac else "")
        fname = "%s %s.txt" % (stem, safe_name(title))
        rel = os.path.join("vol_%02d" % vol, fname)
        write_text(os.path.join(args.out, rel), read_text(ru_path(idx)))
        entries.append({
            "file": rel.replace("\\", "/"),
            "source": "ru/chapter_%04d.txt" % idx,
            "index": idx,
            "volume": vol,
            "number": num,
            "title": title,
            "title_cn": data["titles"].get(idx, ""),
        })

    # Переведённое, что в книгу под своим номером не встало — вслух и поимённо.
    # Молчаливый пропуск здесь и есть та самая потеря главы, которую потом никто
    # не заметит: файл лежит в `ru/`, а в папке его нет.
    skipped = []
    for idx, owner in sorted(data["covered"].items()):
        if os.path.exists(ru_path(idx)):
            skipped.append((idx, "текст входит в склейку гл.%04d" % owner))
    for idx, why in data["unnumbered"]:
        if os.path.exists(ru_path(idx)):
            skipped.append((idx, why))

    save_index(args.out, entries, data, skipped)
    write_report(args.out, entries, data, skipped)

    by_vol = {}
    for e in entries:
        by_vol.setdefault(e["volume"], 0)
        by_vol[e["volume"]] += 1
    print("Разложено глав: %d в %d томах -> %s" % (len(entries), len(by_vol), args.out))
    for v in sorted(by_vol):
        print("    том %d: %d" % (v, by_vol[v]))
    if skipped:
        print("Не разложено  : %d" % len(skipped))
        for idx, why in skipped:
            print("    гл.%04d — %s" % (idx, why))


def save_index(out, entries, data, skipped):
    idx_path = os.path.join(out, "index.json")
    payload = {
        "note": "Соответствие файла перевода авторской нумерации. Номер и том сняты "
                "с заголовков оригинала (第N章 с перезапуском счёта на каждом томе).",
        "chapters": sorted(entries, key=lambda e: (e["volume"], _numeric_key(e["number"]))),
        "repairs": [{"index": i, "was": w, "now": n, "why": why}
                    for i, w, n, why in data["repairs"]
                    if any(e["index"] == i for e in entries)],
        "duplicates": [{"index": i, "claimed": b, "assigned": l, "taken_by": o}
                       for i, b, l, o in data["duplicates"]
                       if any(e["index"] == i for e in entries)],
        "skipped": [{"index": i, "why": w} for i, w in skipped],
    }
    with open(idx_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def write_report(out, entries, data, skipped):
    have = {e["index"] for e in entries}
    lines = [
        "# Раскладка по авторской нумерации",
        "",
        "Собрано `split_by_author.py`. Номер и том сняты с заголовков оригинала:",
        "счёт `第N章` перезапускается с единицы на каждом томе, всего томов 8.",
        "",
        "Разложено глав: **%d**." % len(entries),
        "",
    ]

    reps = [(i, w, n, why) for i, w, n, why in data["repairs"] if i in have]
    lines += ["## Номера, восстановленные по соседям (%d)" % len(reps), ""]
    if reps:
        lines += ["| Файл | В заголовке | Принято | Основание |", "|---|---|---|---|"]
        lines += ["| ru/chapter_%04d.txt | %d | %d | %s |" % (i, w, n, why)
                  for i, w, n, why in reps]
    else:
        lines.append("Ни одного — все номера сняты с заголовков как есть.")
    lines.append("")

    dups = [(i, b, l, o) for i, b, l, o in data["duplicates"] if i in have]
    lines += ["## Повторы номера (%d)" % len(dups), ""]
    if dups:
        lines += [
            "Источник заявил один и тот же номер у разных глав. Первая по порядку",
            "чтения держит целый номер, следующая получает дробный — так обе",
            "остаются в книге и в правильном порядке, а пара том-номер по-прежнему",
            "указывает ровно на одну главу.",
            "",
            "| Файл | Заявлено | Принято | Номер занял |", "|---|---|---|---|",
        ]
        lines += ["| ru/chapter_%04d.txt | %d | %s | ru/chapter_%04d.txt |" % (i, b, l, o)
                  for i, b, l, o in dups]
    else:
        lines.append("Ни одного.")
    lines.append("")

    lines += ["## Не разложено (%d)" % len(skipped), ""]
    if skipped:
        lines += ["| Файл | Причина |", "|---|---|"]
        lines += ["| ru/chapter_%04d.txt | %s |" % (i, w) for i, w in skipped]
    else:
        lines.append("Ничего: каждый готовый перевод встал под своим номером.")
    lines.append("")

    write_text(os.path.join(out, "REPORT.md"), "\n".join(lines))


if __name__ == "__main__":
    main()
