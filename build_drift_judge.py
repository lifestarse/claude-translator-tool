#!/usr/bin/env python3
"""Сборка судейских пакетов по расхождениям текста с глоссарием.

Берёт из final_check_report.json записи глоссария с промахами (порог тот же,
что у предупреждений: >=3 промахов и доля промахов <=0.8), к каждой достаёт
выровненные пары абзацев из глав-промахов и складывает по SIZE записей в
drift/judge_NNN.md. Вердикты пишет агент glossjudge в drift/verdict_NNN.json.

    python build_drift_judge.py
"""

import argparse
import io
import json
import os
import sys

from common import ROOT, chapter_path, read_text, ru_path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DRIFT_DIR = os.path.join(ROOT, "drift")
SIZE = 15
SAMPLES = 4
HEADER = """# Судейство расхождений с глоссарием: пакет {tag:03d}

Файл вердикта: `drift/verdict_{tag:03d}.json`

По каждой записи реши: «дрейф» (сущность названа другим именем — каким),
«шум» (бытовое употребление слова, перевод верен) или «смешанное».

"""


def samples_for(cn, misses):
    out = []
    for idx in misses:
        if len(out) >= SAMPLES:
            break
        try:
            sp = read_text(chapter_path(idx)).split("\n\n")
            rp = read_text(ru_path(idx)).split("\n\n")
        except OSError:
            continue
        if len(sp) != len(rp):
            continue
        for i, para in enumerate(sp):
            if cn in para and len(out) < SAMPLES:
                pos = para.find(cn)
                out.append("- гл.%04d\n  CN: …%s…\n  RU: %s" % (
                    idx,
                    para[max(0, pos - 25):pos + 35].replace("\n", " "),
                    rp[i][:220].replace("\n", " ")))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default=os.path.join(ROOT, "final_check_report.json"))
    args = ap.parse_args()

    rep = json.load(io.open(args.report, encoding="utf-8"))
    rows = []
    for key, st in rep.get("glossary", {}).items():
        m, h = len(st["misses"]), st["hits"]
        if m < 3 or m / max(m + h, 1) > 0.8:
            continue
        rows.append((m, key, st["misses"]))
    rows.sort(reverse=True)

    os.makedirs(DRIFT_DIR, exist_ok=True)
    # Пакеты нумеруются заново с 001 — старые задания, вердикты и захваты
    # с теми же номерами иначе заставили бы драйвер пропустить новые пакеты.
    for old in os.listdir(DRIFT_DIR):
        if old.startswith(("judge_", "verdict_")) or old.endswith(".claim"):
            os.remove(os.path.join(DRIFT_DIR, old))
    index, tag, block = [], 0, []
    for n, (m, key, misses) in enumerate(rows, 1):
        ru_name, cn = key.rsplit(" (", 1)
        cn = cn.rstrip(")")
        ev = samples_for(cn, misses)
        block.append("## %s\n\nКанон: «%s» (%s), основы нет в %d гл.\n\n%s\n"
                     % (key, ru_name, cn, m, "\n".join(ev) or "(примеры не извлеклись)"))
        if len(block) >= SIZE or n == len(rows):
            tag += 1
            io.open(os.path.join(DRIFT_DIR, "judge_%03d.md" % tag), "w",
                    encoding="utf-8", newline="\n").write(
                HEADER.format(tag=tag) + "\n".join(block))
            index.append({"judge": tag, "entries": len(block)})
            block = []
    io.open(os.path.join(DRIFT_DIR, "judge_index.json"), "w",
            encoding="utf-8", newline="\n").write(
        json.dumps(index, ensure_ascii=False, indent=1))
    print("записей: %d, пакетов: %d" % (len(rows), tag))


if __name__ == "__main__":
    main()
