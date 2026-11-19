#!/usr/bin/env python3
"""Драйвер точечных починок: агент qcfix на главу по fixes/qcfix_index.json.

Претензии через атомарный .claim, как у qc_driver: слотов сколько угодно.
Приёмка жёстче, чем у агента в промпте, потому что промпт — просьба, а не
гарантия:
  * validate_translation проходит;
  * число абзацев то же;
  * каждый абзац НЕ из списка разрешённых побайтно тот же, что до агента.
Не прошло — глава откатывается из снимка, снятого перед запуском агента
(ru/ в .gitignore, git checkout тут нечем делать).

    python qcfix_driver.py --slot 1
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import time

from common import (ROOT, chapter_path, load_json, read_text, ru_path,
                    save_json, split_paragraphs, validate_translation)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

FIX_DIR = os.path.join(ROOT, "fixes")
DONE_FILE = os.path.join(FIX_DIR, "qcfix_done_%s.json")
CLAUDE = shutil.which("claude") or shutil.which("claude.cmd")
AUTH_RE = re.compile(r"not logged in|please run /login|invalid api key|authentication_error"
                     r"|oauth token has expired|credit balance is too low", re.I)


def log(slot, msg):
    line = "%s [qf%s] %s" % (time.strftime("%H:%M:%S"), slot, msg)
    print(line, flush=True)
    with open(os.path.join(ROOT, "qcfix_driver.log"), "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def claim(idx):
    try:
        os.close(os.open(os.path.join(FIX_DIR, "qcfix_%04d.claim" % idx),
                         os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        return True
    except FileExistsError:
        return False


def accept(idx, before, allowed):
    """Пусто — принято; иначе причина отказа."""
    after = read_text(ru_path(idx))
    problems = validate_translation(read_text(chapter_path(idx)), after)
    if problems:
        return "приёмка: %s" % "; ".join(problems)[:200]
    a, b = split_paragraphs(before), split_paragraphs(after)
    if len(a) != len(b):
        return "абзацев было %d, стало %d" % (len(a), len(b))
    touched = [n for n in range(1, len(a) + 1) if a[n - 1] != b[n - 1]]
    stray = [n for n in touched if n not in allowed]
    if stray:
        return "тронуты чужие абзацы %s" % stray
    if not touched:
        return "ничего не правлено"
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", default="0")
    args = ap.parse_args()
    if not CLAUDE:
        raise SystemExit("claude не найден в PATH — драйверу нечем запускать агента qcfix.")

    done = {}
    for job in load_json(os.path.join(FIX_DIR, "qcfix_index.json"), []):
        idx, allowed = job["index"], set(job["paras"])
        if not claim(idx):
            continue
        path = ru_path(idx)
        before = read_text(path)
        try:
            proc = subprocess.run(
                [CLAUDE, "-p", "Файл задания: %s" % job["fix_file"], "--agent", "qcfix",
                 "--permission-mode", "bypassPermissions", "--output-format", "json"],
                cwd=ROOT, capture_output=True, timeout=1500,
                encoding="utf-8", errors="replace")
            out = (proc.stdout or "") + (proc.stderr or "")
        except subprocess.TimeoutExpired:
            out = "таймаут"
        if AUTH_RE.search(out):
            open(path, "w", encoding="utf-8", newline="\n").write(before)
            log(args.slot, "нет авторизации claude — выполни /login и перезапусти")
            raise SystemExit(2)
        why = accept(idx, before, allowed)
        if why:
            open(path, "w", encoding="utf-8", newline="\n").write(before)
            log(args.slot, "глава %04d: ОТКАТ — %s" % (idx, why))
            done[idx] = "rollback: " + why
        else:
            changed = sum(1 for x, y in zip(split_paragraphs(before),
                                            split_paragraphs(read_text(path))) if x != y)
            log(args.slot, "глава %04d: ok, абзацев правлено %d из %d"
                % (idx, changed, len(allowed)))
            done[idx] = "ok %d/%d" % (changed, len(allowed))
        save_json(DONE_FILE % args.slot, done)
    log(args.slot, "конец")


if __name__ == "__main__":
    main()
