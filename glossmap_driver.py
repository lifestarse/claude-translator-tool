#!/usr/bin/env python3
"""Драйвер картографов глоссария: агент glossmap на пакет записей.

Претензии через атомарный .claim, приёмка — JSON-вердикт существует и парсится.

    python glossmap_driver.py --slot 1
"""

import argparse
import json
import os
import subprocess
import sys
import time

import llm
from common import ROOT, load_json

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DIR = os.path.join(ROOT, "glossmap")
TIMEOUT = 1800  # с на пакет


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


def unclaim(tag):
    try:
        os.remove(os.path.join(DIR, "task_%03d.claim" % tag))
    except OSError:
        pass


def entries_in(out):
    """Число записей в ответе, или None, если ответа нет или он битый."""
    try:
        return len(json.load(open(out, encoding="utf-8"))["entries"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", default="0")
    ap.add_argument("--reclaim-stale", action="store_true",
                    help="снять .claim старше таймаута без ответа (после Ctrl+C/падения)")
    args = ap.parse_args()

    # Нет claude у профиля, которому он нужен, — llm остановит запуск здесь,
    # до захвата заданий (иначе остался бы залипший .claim).
    llm.agent_cmd("проверка")
    index = os.path.join(DIR, "task_index.json")
    if not os.path.exists(index):
        raise SystemExit("Нет %s — заданий для картографов нет.\n"
                         "Сборщика скелета и нарезки task_NNN.md в каркасе пока нет, "
                         "стадия 10 не запускается." % os.path.relpath(index, ROOT))

    for b in load_json(index, []):
        tag = b["task"]
        out = os.path.join(DIR, "out_%03d.json" % tag)
        claim_path = os.path.join(DIR, "task_%03d.claim" % tag)
        # Валидный ответ мог дописать и осиротевший агент после таймаута —
        # его не выдаём повторно.
        if entries_in(out) is not None:
            continue
        if args.reclaim_stale and os.path.exists(claim_path) \
                and time.time() - os.path.getmtime(claim_path) > TIMEOUT:
            log(args.slot, "пакет %03d: снимаю протухший claim" % tag)
            unclaim(tag)
        if not claim(tag):
            continue
        if os.path.exists(out):
            # Битый ответ прошлой попытки иначе блокировал пакет навсегда.
            os.replace(out, os.path.join(DIR, "out_%03d.bad.json" % tag))
        try:
            r = subprocess.run(
                llm.agent_cmd("Файл задания: glossmap/task_%03d.md" % tag, "glossmap", "sonnet"),
                env=llm.agent_env(), cwd=ROOT, capture_output=True, timeout=TIMEOUT,
                encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            # На Windows таймаут убивает claude.cmd, но не обязательно node под
            # ним: ответ может дописаться позже, его подхватит проверка выше.
            log(args.slot, "пакет %03d: таймаут, claim снят" % tag)
            unclaim(tag)
            continue
        if r.returncode:
            log(args.slot, "пакет %03d: claude вернул %d: %s"
                % (tag, r.returncode, (r.stderr or r.stdout or "").strip()[-300:]))
        got = entries_in(out)
        if got is None:
            log(args.slot, "пакет %03d: ответ не записан или битый" % tag)
            if os.path.exists(out):
                os.replace(out, os.path.join(DIR, "out_%03d.bad.json" % tag))
            unclaim(tag)
            continue
        log(args.slot, "пакет %03d: записей %d" % (tag, got))
    log(args.slot, "конец")


if __name__ == "__main__":
    main()
