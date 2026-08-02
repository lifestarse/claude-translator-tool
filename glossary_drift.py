#!/usr/bin/env python3
"""Что стоит в переводе там, где глоссарий ждёт своё имя.

Зонды final_check.py говорят, в каких главах основа канонического имени
пропала. Этот скрипт отвечает на следующий вопрос — ЧЕМ она заменена:
абзацы выровнены с оригиналом 1:1, поэтому по позиции китайского термина
в источнике достаётся ровно тот русский абзац, где должно стоять имя.
Дальше решает человек: дрейф превращается в правило term_fixes, шум
(бытовое слово, местоимение) — отбрасывается.

    python glossary_drift.py            # таблица по расхождениям из отчёта
"""

import io
import json
import sys

from common import chapter_path, read_text, ru_path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SAMPLES_PER_ENTRY = 4     # абзацев-примеров на запись
CHAPTERS_PER_ENTRY = 8    # из скольких глав брать примеры


def main():
    rep = json.load(io.open("final_check_report.json", encoding="utf-8"))
    gloss = rep.get("glossary", {})
    rows = []
    for key, st in gloss.items():
        m, h = len(st["misses"]), st["hits"]
        if m < 3 or m / max(m + h, 1) > 0.8:
            continue
        rows.append((m, h, key, st["misses"]))
    rows.sort(reverse=True)
    print("Записей к разбору: %d\n" % len(rows))

    for m, h, key, misses in rows:
        ru_name, cn = key.rsplit(" (", 1)
        cn = cn.rstrip(")")
        print("=" * 70)
        print("«%s» (%s) — нет в %d из %d глав" % (ru_name, cn, m, m + h))
        shown = 0
        for idx in misses[:CHAPTERS_PER_ENTRY]:
            if shown >= SAMPLES_PER_ENTRY:
                break
            try:
                sp = read_text(chapter_path(idx)).split("\n\n")
                rp = read_text(ru_path(idx)).split("\n\n")
            except OSError:
                continue
            if len(sp) != len(rp):
                continue
            for i, para in enumerate(sp):
                if cn in para and shown < SAMPLES_PER_ENTRY:
                    pos = para.find(cn)
                    print("  гл.%04d абзац %d" % (idx, i))
                    print("    CN: …%s…" % para[max(0, pos - 20):pos + 30].replace("\n", " "))
                    print("    RU: %s" % rp[i][:180].replace("\n", " "))
                    shown += 1
        print("")


if __name__ == "__main__":
    main()
