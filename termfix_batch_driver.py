#!/usr/bin/env python3
"""Драйвер пакетной правки: агент termfix-batch на пакет глав.

Приёмка идёт поглавно: испорченная глава откатывается из git, остальные из
пакета остаются. Несколько экземпляров делят пакеты атомарным .claim.

    python termfix_batch_driver.py --slot 1
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

from common import ROOT, chapter_path, load_json, read_text, ru_path, validate_translation

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

FIX_DIR = os.path.join(ROOT, "fixes")
CLAUDE = shutil.which("claude") or shutil.which("claude.cmd")


def log(slot, msg):
    line = "%s [b%s] %s" % (time.strftime("%H:%M:%S"), slot, msg)
    print(line, flush=True)
    with open(os.path.join(ROOT, "termfix_batch.log"), "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def claim(tag):
    try:
        fd = os.open(os.path.join(FIX_DIR, "batch_%03d.claim" % tag),
                     os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        return True
    except FileExistsError:
        return False


def run_agent(tag):
    proc = subprocess.run(
        [CLAUDE, "-p", "Файл задания: fixes/batch_%03d.md" % tag,
         "--agent", "termfix-batch", "--model", "sonnet",
         "--permission-mode", "bypassPermissions", "--output-format", "json"],
        cwd=ROOT, capture_output=True, timeout=3600,
        encoding="utf-8", errors="replace",
    )
    try:
        data = json.loads(proc.stdout or "")
        u = data.get("usage") or {}
        with open(os.path.join(ROOT, "termfix_usage.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"tag": "batch_%03d" % tag,
                "in": u.get("input_tokens", 0),
                "cache_write": u.get("cache_creation_input_tokens", 0),
                "cache_read": u.get("cache_read_input_tokens", 0),
                "out": u.get("output_tokens", 0),
                "usd": data.get("total_cost_usd")}, ensure_ascii=False) + "\n")
        return str(data.get("result") or "")[:80]
    except (ValueError, OSError):
        return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", default="0")
    args = ap.parse_args()

    batches = load_json(os.path.join(FIX_DIR, "batch_index.json"), [])
    for b in batches:
        tag = b["batch"]
        if os.path.exists(os.path.join(FIX_DIR, "batch_%03d.done" % tag)):
            continue
        if not claim(tag):
            continue
        try:
            report = run_agent(tag)
        except subprocess.TimeoutExpired:
            log(args.slot, "пакет %03d: таймаут" % tag)
            open(os.path.join(FIX_DIR, "batch_%03d.fail" % tag), "w").close()
            continue

        broken = []
        for idx in b["chapters"]:
            problems = validate_translation(read_text(chapter_path(idx)),
                                            read_text(ru_path(idx)))
            if problems:
                subprocess.run(["git", "checkout", "--", "ru/chapter_%04d.txt" % idx],
                               cwd=ROOT, capture_output=True)
                broken.append(idx)
        marker = "done" if not broken else "part"
        open(os.path.join(FIX_DIR, "batch_%03d.%s" % (tag, marker)), "w").close()
        log(args.slot, "пакет %03d: %s%s" % (
            tag, report or "агент молчит",
            "" if not broken else " | ОТКАЧЕНО: %s" % broken))
    log(args.slot, "конец")


if __name__ == "__main__":
    main()
