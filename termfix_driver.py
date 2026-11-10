#!/usr/bin/env python3
"""Драйвер флота termfix: гонит агентов по заданиям fixes/fix_*.md.

Каждая правка проверяется приёмкой; испорченная глава откатывается к снимку,
снятому перед агентом,
и задание помечается упавшим. Несколько экземпляров делят работу через
атомарный захват .claim-файлов — запускать можно любым числом копий:

    python termfix_driver.py --slot 1
"""

import argparse
import os
import subprocess
import sys
import time

import llm
from common import ROOT, accept_or_restore, load_json, snapshot_ru


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

FIX_DIR = os.path.join(ROOT, "fixes")


def log(slot, msg):
    line = "%s [t%s] %s" % (time.strftime("%H:%M:%S"), slot, msg)
    print(line, flush=True)
    with open(os.path.join(ROOT, "termfix_run.log"), "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def claim(idx):
    try:
        fd = os.open(os.path.join(FIX_DIR, "fix_%04d.claim" % idx),
                     os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        return True
    except FileExistsError:
        return False


def run_agent(idx):
    proc = subprocess.run(
        llm.agent_cmd("Файл задания: fixes/fix_%04d.md" % idx, "termfix", "sonnet"),
        env=llm.agent_env(), cwd=ROOT, capture_output=True, timeout=900,
        encoding="utf-8", errors="replace",
    )
    # Точный учёт: конверт вызова копится в jsonl, как autorun_usage
    try:
        import json as _json
        data = _json.loads(proc.stdout or "")
        u = data.get("usage") or {}
        with open(os.path.join(ROOT, "termfix_usage.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(_json.dumps({"tag": "%04d" % idx,
                "in": u.get("input_tokens", 0),
                "cache_write": u.get("cache_creation_input_tokens", 0),
                "cache_read": u.get("cache_read_input_tokens", 0),
                "out": u.get("output_tokens", 0),
                "usd": data.get("total_cost_usd")}, ensure_ascii=False) + "\n")
    except (ValueError, OSError):
        pass
    return proc.returncode == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", default="0")
    args = ap.parse_args()

    # Только задания текущей сборки: в fixes/ лежат и файлы прошлых прогонов,
    # чинить по ним — вносить уже отменённые правки.
    tasks = sorted(e["index"] for e in load_json(os.path.join(FIX_DIR, "index.json"), []))
    done = fails = 0
    for idx in tasks:
        if os.path.exists(os.path.join(FIX_DIR, "fix_%04d.done" % idx)):
            continue
        if not claim(idx):
            continue
        ok = False
        snapshot = snapshot_ru([idx])
        for attempt in (1, 2):
            try:
                run_agent(idx)
            except subprocess.TimeoutExpired:
                log(args.slot, "гл.%04d: таймаут агента (попытка %d)" % (idx, attempt))
                accept_or_restore(idx, snapshot)   # полуправку — откатить
                continue
            problems = accept_or_restore(idx, snapshot)
            if not problems:
                ok = True
                break
            log(args.slot, "гл.%04d: приёмка сломана (%s) — откат" % (idx, problems[:1]))
        marker = "done" if ok else "fail"
        with open(os.path.join(FIX_DIR, "fix_%04d.%s" % (idx, marker)), "w") as fh:
            fh.write("")
        done += ok
        fails += not ok
        if ok:
            log(args.slot, "гл.%04d: готово (всего %d)" % (idx, done))
    log(args.slot, "конец: сделано %d, упало %d" % (done, fails))


if __name__ == "__main__":
    main()
