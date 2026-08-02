#!/usr/bin/env python3
"""Драйвер картографов глоссария: агент glossmap на пакет записей.

Претензии через атомарный .claim, приёмка — JSON-вердикт существует и парсится.

    python glossmap_driver.py --slot 1
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

DIR = os.path.join(ROOT, "glossmap")
CLAUDE = shutil.which("claude") or shutil.which("claude.cmd")


def log(slot, msg):
    line = "%s [gm%s] %s" % (time.strftime("%H:%M:%S"), slot, msg)
    print(line, flush=True)
    with open(os.path.join(ROOT, "glossmap.log"), "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def claim(tag):
    try:
        fd = os.open(os.path.join(DIR, "task_%03d.claim" % tag),
                     os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        return True
    except FileExistsError:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", default="0")
    args = ap.parse_args()

    for b in load_json(os.path.join(DIR, "task_index.json"), []):
        tag = b["task"]
        out = os.path.join(DIR, "out_%03d.json" % tag)
        if os.path.exists(out) or not claim(tag):
            continue
        try:
            subprocess.run(
                [CLAUDE, "-p", "Файл задания: glossmap/task_%03d.md" % tag,
                 "--agent", "glossmap", "--model", "sonnet",
                 "--permission-mode", "bypassPermissions", "--output-format", "json"],
                cwd=ROOT, capture_output=True, timeout=1800,
                encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            log(args.slot, "пакет %03d: таймаут" % tag)
            continue
        try:
            got = len(json.load(open(out, encoding="utf-8"))["entries"])
            log(args.slot, "пакет %03d: записей %d" % (tag, got))
        except (OSError, ValueError, KeyError):
            log(args.slot, "пакет %03d: ответ не записан или битый" % tag)
            os.remove(os.path.join(DIR, "task_%03d.claim" % tag))
    log(args.slot, "конец")


if __name__ == "__main__":
    main()
