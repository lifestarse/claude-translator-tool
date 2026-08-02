#!/usr/bin/env python3
"""Collect proofreader verdicts from qc_verdicts/ and act on them.

Reports by default and changes nothing. Two things it can do on request:

    python qc_report.py                 # summary
    python qc_report.py --detail        # every defect, with quotes
    python qc_report.py --quarantine    # retranslate verdicts -> ru_rejected/

Quarantine is deliberately opt-in and deliberately separate from build_qc.py:
an agent's verdict is evidence, not an execution order, and moving 40 finished
chapters out of ru/ on the strength of one model's opinion is not something
that should happen as a side effect of asking for a report.

The control sample matters more than the totals. Chapters picked at random are
there to measure the risk signals in build_qc.py: if they come back as dirty as
the ranked ones, the ranking is not finding anything the corpus does not have
everywhere, and reading only the top of it is a false economy. It only measures
the corpus if it is judged BLIND -- the six sample tasks issued on 28-29.07
carried the sentence "a clean verdict is expected here", so their 50% defect
rate is a lower bound, not a measurement. build_qc.py no longer writes it.
"""

import argparse
import collections
import datetime
import os
import shutil
import sys

from common import ROOT, load_json, load_task_provenance, ru_path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

VERDICT_DIR = os.path.join(ROOT, "qc_verdicts")
QC_DIR = os.path.join(ROOT, "qc_batches")
META_DIR = os.path.join(QC_DIR, "meta")
QUARANTINE_DIR = os.path.join(ROOT, "ru_rejected")

# Below this many verdicts in a group, comparing defect RATES between groups is
# arithmetic on noise: on six random chapters one verdict moves the figure by
# 17 points. Print the counts and say so instead of shouting a conclusion.
MIN_GROUP_FOR_RATES = 5

KIND_RU = {
    "omission": "выхолащивание",
    "invention": "отсебятина",
    "spam": "реклама сайта",
    "glossary": "термин не по канону",
    "calque": "подстрочник",
}
VERDICTS = ("ok", "fix", "retranslate")


def load_verdicts():
    if not os.path.isdir(VERDICT_DIR):
        return [], ["каталога qc_verdicts/ нет — приёмка ещё не запускалась"]
    out, problems = [], []
    for name in sorted(os.listdir(VERDICT_DIR)):
        if not name.endswith(".json"):
            continue
        data = load_json(os.path.join(VERDICT_DIR, name))
        if not isinstance(data, dict):
            problems.append("%s: не разобрался JSON" % name)
            continue
        v = str(data.get("verdict", "")).strip().lower()
        if v not in VERDICTS:
            problems.append("%s: вердикт «%s» не из списка %s" % (name, v, "/".join(VERDICTS)))
            continue
        try:
            idx = int(data.get("chapter", name[3:7]))
        except (TypeError, ValueError):
            problems.append("%s: не разобрался номер главы" % name)
            continue
        issues = data.get("issues") or []
        if not isinstance(issues, list):
            problems.append("%s: issues не список" % name)
            issues = []
        out.append({"index": idx, "verdict": v, "issues": issues, "file": name})
    return out, problems


def load_provenance():
    """index -> provenance record for every task build_qc.py has ever written.

    Reads qc_batches/meta/, not qc_batches/index.json. index.json holds one
    batch -- the last one -- because build_qc.py rebuilt it from scratch every
    run, so `--only 131 137` erased how the other seventeen chapters had been
    picked and the table below counted 17 of 19 verdicts as "?". Details and
    the recovery procedure: plans/04-provenance.md.

    Wraps the shared reader so that tests can retarget qc_report.QC_DIR.
    """
    return load_task_provenance(QC_DIR)


def main():
    ap = argparse.ArgumentParser(description="Сводка вердиктов приёмки")
    ap.add_argument("--detail", action="store_true")
    ap.add_argument("--quarantine", action="store_true",
                    help="перенести главы с вердиктом retranslate в ru_rejected/")
    args = ap.parse_args()

    verdicts, problems = load_verdicts()
    if problems:
        print("[!] Проблемы при чтении вердиктов:")
        for p in problems:
            print("    - %s" % p)
    if not verdicts:
        print("Вердиктов нет. Сначала: python build_qc.py, затем агенты proofreader.")
        raise SystemExit(1)

    prov = load_provenance()
    origin = {k: str(v.get("picked_by", "?") or "?") for k, v in prov.items()}
    by_verdict = collections.Counter(v["verdict"] for v in verdicts)
    kinds = collections.Counter(
        i.get("kind", "?") for v in verdicts for i in v["issues"] if isinstance(i, dict)
    )

    print("=== ПРИЁМКА ===")
    print("Вердиктов          : %d" % len(verdicts))
    for name in VERDICTS:
        print("  %-14s   : %d" % (name, by_verdict[name]))
    print("Дефектов всего     : %d" % sum(kinds.values()))
    for kind, n in kinds.most_common():
        print("  %-18s : %d" % (KIND_RU.get(kind, kind), n))

    # The control group. Same defect rate in both columns means the ranking is
    # not selecting for anything.
    groups = collections.defaultdict(collections.Counter)
    unknown_origin = []
    for v in verdicts:
        name = origin.get(v["index"], "?")
        groups[name][v["verdict"]] += 1
        if name == "?":
            unknown_origin.append(v["index"])
    # A `sample` chapter that had risk signals but fell below the cut measures
    # something quite different from one with no signals at all: the first tests
    # the CUTOFF, the second tests the SIGNALS. Averaging them together hides
    # both answers.
    sample_split = collections.defaultdict(collections.Counter)
    for v in verdicts:
        if origin.get(v["index"]) != "sample":
            continue
        rec = prov.get(v["index"]) or {}
        key = ("sample (сигналы есть, не вошли в топ)" if rec.get("flagged")
               else "sample (без сигналов)")
        sample_split[key][v["verdict"]] += 1

    if len(groups) > 1:
        print("")
        print("=== ВЫБОРКА ПРОТИВ РЕЙТИНГА ===")
        print("%-40s %6s %6s %6s %6s   %s"
              % ("отбор", "всего", "ok", "fix", "retr", "брак"))

        def row(name, g):
            total = sum(g.values())
            bad = g["fix"] + g["retranslate"]
            print("%-40s %6d %6d %6d %6d   %.0f%%"
                  % (name, total, g["ok"], g["fix"], g["retranslate"],
                     100.0 * bad / total))

        # Known labels first, then anything a future version of build_qc.py
        # invents, then "?" last. The old version hardcoded the four names and
        # dropped any new label on the floor without a word.
        known = ("risk", "sample", "only")
        for name in known:
            if groups.get(name):
                row(name, groups[name])
                if name == "sample" and len(sample_split) > 1:
                    for sub in sorted(sample_split):
                        row("    " + sub, sample_split[sub])
        for name in sorted(k for k in groups if k not in known and k != "?"):
            row(name, groups[name])
        if groups.get("?"):
            row("?", groups["?"])
            print("")
            print("[!] Пометка отбора не найдена для глав: %s"
                  % ", ".join(str(i) for i in sorted(unknown_origin)))
            print("    Восстановление: python tools/qc_backfill_provenance.py")

        risk, sample = groups.get("risk"), groups.get("sample")
        if risk and sample:
            nr, ns = sum(risk.values()), sum(sample.values())
            if nr < MIN_GROUP_FOR_RATES or ns < MIN_GROUP_FOR_RATES:
                print("")
                print("[i] Данных мало для сравнения долей: risk %d, sample %d."
                      % (nr, ns))
            else:
                rb = (risk["fix"] + risk["retranslate"]) / nr
                sb = (sample["fix"] + sample["retranslate"]) / ns
                if sb >= rb:
                    print("")
                    print("[!] Случайные главы бракуются не реже отобранных по риску.")
                    print("    Значит сигналы в build_qc.py ничего не выделяют, и читать")
                    print("    только верх рейтинга — самообман. Смотри, что они пропускают.")

    # A verdict older than the task it belongs to was written against a
    # different text: the agent read a task file that has since been rebuilt,
    # so its paragraph numbers may point at other paragraphs.
    older = []
    for v in verdicts:
        rec = prov.get(v["index"]) or {}
        issued = rec.get("issued_at")
        path = os.path.join(VERDICT_DIR, v["file"])
        if not issued or not os.path.exists(path):
            continue
        try:
            stamp = datetime.datetime.fromtimestamp(
                os.path.getmtime(path), datetime.timezone.utc)
            if stamp < datetime.datetime.fromisoformat(issued):
                older.append(v["index"])
        except (ValueError, OSError):
            continue
    if older:
        print("")
        print("[!] Вердикт старше своего задания: %s"
              % ", ".join(str(i) for i in sorted(older)))
        print("    Задание переиздано после вердикта — номера абзацев в issues")
        print("    могут указывать не туда. Сверить: python build_qc.py --report")

    if args.detail:
        print("")
        print("=== ДЕФЕКТЫ ===")
        for v in sorted(verdicts, key=lambda v: (v["verdict"] != "retranslate", v["index"])):
            if not v["issues"]:
                continue
            print("")
            print("гл.%04d  %s  (%s)" % (v["index"], v["verdict"], origin.get(v["index"], "?")))
            for i in v["issues"]:
                if not isinstance(i, dict):
                    continue
                print("    [%s] абз.%s — %s"
                      % (KIND_RU.get(i.get("kind"), i.get("kind")),
                         i.get("para", "?"), i.get("why", "")))
                if i.get("quote"):
                    print("        «%s»" % i["quote"])

    retr = [v for v in verdicts if v["verdict"] == "retranslate"]
    fix = [v for v in verdicts if v["verdict"] == "fix"]

    if retr:
        print("")
        print("[!] На перевод заново: %s" % ", ".join(str(v["index"]) for v in retr))
        if not args.quarantine:
            print("    Перенести в ru_rejected/: python qc_report.py --quarantine")
    if fix:
        print("")
        print("[i] Чинится на месте: %s" % ", ".join(str(v["index"]) for v in fix))
        print("    Термины — через term_fixes.json и агента termfix.")

    if args.quarantine and retr:
        os.makedirs(QUARANTINE_DIR, exist_ok=True)
        moved = 0
        for v in retr:
            src = ru_path(v["index"])
            if not os.path.exists(src):
                continue
            shutil.move(src, os.path.join(QUARANTINE_DIR, "chapter_%04d.txt" % v["index"]))
            moved += 1
        print("")
        print("[+] Перемещено в ru_rejected/: %d файлов." % moved)
        print("    Дальше: python build_batches.py --only %s"
              % " ".join(str(v["index"]) for v in retr))

    raise SystemExit(1 if retr else 0)


if __name__ == "__main__":
    main()
