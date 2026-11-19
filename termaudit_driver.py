#!/usr/bin/env python3
"""Драйвер аудита замен: агент termaudit на пакет пар «БЫЛО → СТАЛО».

Претензии через атомарный .claim, приёмка — JSON-вердикт существует и парсится.
Без вердикта (таймаут, сбой агента, битый ответ, Ctrl+C) claim снимается, и
пакет вернётся в очередь при следующем запуске.

Входа termaudit/task_index.json (+ task_NNN.md) в каркасе не собирает ни один
скрипт: он строится из гистограммы замен «рождение главы → сейчас», а снимка
рождения глав (ru/ в .gitignore) пока нет. Без него драйвер говорит об этом
и выходит, а не молча печатает «конец».

    python termaudit_driver.py --slot 1
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

DIR = os.path.join(ROOT, "termaudit")


def log(slot, msg):
    line = "%s [gm%s] %s" % (time.strftime("%H:%M:%S"), slot, msg)
    print(line, flush=True)
    with open(os.path.join(ROOT, "termaudit.log"), "a", encoding="utf-8") as fh:
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

    index_file = os.path.join(DIR, "task_index.json")
    if not os.path.exists(index_file):
        print("[!] Нет termaudit/task_index.json — заданий аудита замен нет.")
        print("    Сборщика заданий termaudit в каркасе нет: ему нужна гистограмма")
        print("    замен «рождение главы → сейчас», а снимка рождения глав ещё нет")
        print("    (ru/ в .gitignore, git его не хранит). Формат входа: список")
        print('    {"task": N}, рядом termaudit/task_NNN.md с парами БЫЛО → СТАЛО.')
        raise SystemExit(1)
    # Нет claude у профиля, которому он нужен, — llm остановит запуск здесь,
    # до захвата заданий (иначе остался бы залипший .claim).
    llm.agent_cmd("проверка")

    busy = 0
    for b in load_json(index_file, []):
        tag = b["task"]
        out = os.path.join(DIR, "out_%03d.json" % tag)
        if os.path.exists(out):
            continue
        if not claim(tag):
            busy += 1
            continue
        got = None
        try:
            proc = subprocess.run(
                llm.agent_cmd("Файл задания: termaudit/task_%03d.md" % tag, "termaudit", "opus"),
                env=llm.agent_env(), cwd=ROOT, capture_output=True, timeout=2700,
                encoding="utf-8", errors="replace")
            if proc.returncode != 0:
                log(args.slot, "пакет %03d: агент упал (код %s)" % (tag, proc.returncode))
            # with: открытый дескриптор на Windows не дал бы удалить битый out_
            with open(out, encoding="utf-8") as fh:
                got = len(json.load(fh)["verdicts"])
            log(args.slot, "пакет %03d: записей %d" % (tag, got))
        except subprocess.TimeoutExpired:
            log(args.slot, "пакет %03d: таймаут" % tag)
        except (OSError, ValueError, KeyError, TypeError):
            log(args.slot, "пакет %03d: ответ не записан или битый" % tag)
        finally:
            # Без принятого вердикта задание возвращается в очередь; битый
            # out_ убирается, иначе следующий запуск счёл бы пакет готовым.
            if got is None:
                if os.path.exists(out):
                    os.remove(out)
                os.remove(os.path.join(DIR, "task_%03d.claim" % tag))
    log(args.slot, "конец")
    if busy:
        log(args.slot, "пропущено из-за чужих .claim: %d. Если других экземпляров нет — "
            "удали termaudit/task_*.claim" % busy)


if __name__ == "__main__":
    main()
