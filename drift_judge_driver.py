#!/usr/bin/env python3
"""Драйвер судейства глоссарных расхождений: агент glossjudge на пакет.

Претензии через атомарный .claim, как у termfix_batch_driver. Приёмка —
вердикт-файл существует и парсится как JSON с непустым verdicts.

    python drift_judge_driver.py --slot 1
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

from common import ROOT, load_json

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DRIFT_DIR = os.path.join(ROOT, "drift")
CLAUDE = shutil.which("claude") or shutil.which("claude.cmd")


def log(slot, msg):
    line = "%s [gj%s] %s" % (time.strftime("%H:%M:%S"), slot, msg)
    print(line, flush=True)
    with open(os.path.join(ROOT, "drift_judge.log"), "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def claim(tag):
    try:
        fd = os.open(os.path.join(DRIFT_DIR, "judge_%03d.claim" % tag),
                     os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        return True
    except FileExistsError:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", default="0")
    args = ap.parse_args()

    for b in load_json(os.path.join(DRIFT_DIR, "judge_index.json"), []):
        tag = b["judge"]
        verdict = os.path.join(DRIFT_DIR, "verdict_%03d.json" % tag)
        if os.path.exists(verdict) or not claim(tag):
            continue
        try:
            proc = subprocess.run(
                [CLAUDE, "-p", "Файл задания: drift/judge_%03d.md" % tag,
                 "--agent", "glossjudge", "--model", "sonnet",
                 "--permission-mode", "bypassPermissions", "--output-format", "json"],
                cwd=ROOT, capture_output=True, timeout=1200,
                encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            log(args.slot, "пакет %03d: таймаут" % tag)
            continue
        try:
            got = len(json.load(open(verdict, encoding="utf-8"))["verdicts"])
            log(args.slot, "пакет %03d: вердиктов %d" % (tag, got))
        except (OSError, ValueError, KeyError):
            log(args.slot, "пакет %03d: вердикт не записан или битый" % tag)
            os.remove(os.path.join(DRIFT_DIR, "judge_%03d.claim" % tag))
    log(args.slot, "конец")


if __name__ == "__main__":
    main()
