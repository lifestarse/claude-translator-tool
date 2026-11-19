#!/usr/bin/env python3
"""Драйвер смысловой приёмки: агент proofreader на главу.

Берёт все qc_batches/qc_NNNN.md без вердикта. Претензии через атомарный
.claim, как у drift_judge_driver, поэтому слотов можно запустить сколько
угодно — они не возьмут одну главу дважды. Приёмка — вердикт существует,
парсится как JSON и в нём есть verdict.

    python qc_driver.py --slot 1
    python qc_driver.py --slot 1 --model sonnet   # дешевле, грубее
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

from common import ROOT

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

QC_DIR = os.path.join(ROOT, "qc_batches")
VERDICT_DIR = os.path.join(ROOT, "qc_verdicts")
CLAUDE = shutil.which("claude") or shutil.which("claude.cmd")
AUTH_RE = re.compile(r"not logged in|please run /login|invalid api key|authentication_error"
                     r"|oauth token has expired|credit balance is too low", re.I)


def log(slot, msg):
    line = "%s [qc%s] %s" % (time.strftime("%H:%M:%S"), slot, msg)
    print(line, flush=True)
    with open(os.path.join(ROOT, "qc_driver.log"), "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def claim_path(idx):
    return os.path.join(QC_DIR, "qc_%04d.claim" % idx)


def claim(idx):
    try:
        os.close(os.open(claim_path(idx), os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        return True
    except FileExistsError:
        return False


def unclaim(idx):
    try:
        os.remove(claim_path(idx))
    except OSError:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", default="0")
    ap.add_argument("--model", default=None,
                    help="модель агента; по умолчанию — из .claude/agents/proofreader.md")
    args = ap.parse_args()
    if not CLAUDE:
        raise SystemExit("claude не найден в PATH — драйверу нечем запускать агента proofreader.")
    os.makedirs(VERDICT_DIR, exist_ok=True)

    tasks = sorted(int(m.group(1)) for m in
                   (re.match(r"qc_(\d{4})\.md$", f) for f in os.listdir(QC_DIR)) if m)
    failures = 0
    for idx in tasks:
        verdict = os.path.join(VERDICT_DIR, "qc_%04d.json" % idx)
        if os.path.exists(verdict) or not claim(idx):
            continue
        cmd = [CLAUDE, "-p", "Файл задания: qc_batches/qc_%04d.md" % idx,
               "--agent", "proofreader",
               "--permission-mode", "bypassPermissions", "--output-format", "json"]
        if args.model:
            cmd[4:4] = ["--model", args.model]
        try:
            proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, timeout=1500,
                                  encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            log(args.slot, "глава %04d: таймаут, будет повтор" % idx)
            unclaim(idx)
            continue
        if AUTH_RE.search((proc.stdout or "") + (proc.stderr or "")):
            unclaim(idx)
            log(args.slot, "нет авторизации claude — выполни /login и перезапусти")
            raise SystemExit(2)
        try:
            v = json.load(open(verdict, encoding="utf-8"))
            log(args.slot, "глава %04d: %s, дефектов %d"
                % (idx, v["verdict"], len(v.get("issues") or [])))
            failures = 0
        except (OSError, ValueError, KeyError, TypeError):
            log(args.slot, "глава %04d: вердикт не записан или битый" % idx)
            unclaim(idx)
            failures += 1
            if failures >= 5:
                log(args.slot, "пять провалов подряд — слот остановлен")
                raise SystemExit(1)
    log(args.slot, "конец")


if __name__ == "__main__":
    main()
