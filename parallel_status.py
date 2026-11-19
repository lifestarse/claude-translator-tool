#!/usr/bin/env python3
"""Сводка по параллельным прогонам autorun: один экран вместо 16 логов.

    python parallel_status.py              # один снимок
    python parallel_status.py --watch      # обновляется каждые 30 с (Ctrl+C — выход)
    python parallel_status.py --watch 10   # каждые 10 с

Слоты находит сам: по файлам parallel_range_NN.json и autorun_state_NN.json,
которые autorun пишет при запуске с --range/--slot. Для прогонов, запущенных
до появления этих файлов, границы берутся из строки лога «Последовательный
прогон: … с LO по HI». Ручной parallel_ranges.json, если он есть, тоже
учитывается. Дальше читает логи autorun_NN.log (последняя строка = чем слот
занят) и ru/ (сколько глав участка уже на диске).
Ничего не пишет.
"""

import argparse
import datetime as dt
import glob
import json
import os
import re
import sys
import time

from common import ROOT, load_excluded, ru_path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

RANGES = os.path.join(ROOT, "parallel_ranges.json")
DONE_RE = re.compile(r"готово \|")
STATE_RE = re.compile(r"^autorun_state_(.+)\.json$")
# Строка, которую autorun пишет в лог на старте каждого прогона.
RUN_RE = re.compile(r"Последовательный прогон: \d+ глав, с (\d+) по (\d+)")


def range_from_log(slot):
    """(lo, hi) из самой ранней строки старта в логе слота, или None.

    Берётся первая, а не последняя: после перезапуска очередь начинается с
    места обрыва, и поздние строки показывают уже урезанный участок.
    """
    log_file = os.path.join(ROOT, "autorun_%s.log" % slot)
    if not os.path.exists(log_file):
        return None
    with open(log_file, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = RUN_RE.search(line)
            if m:
                return int(m.group(1)), int(m.group(2))
    return None


def discover_ranges():
    """[{slot, lo, hi}] — из parallel_ranges.json, если он есть, иначе по слотам."""
    if os.path.exists(RANGES):
        with open(RANGES, encoding="utf-8") as fh:
            return json.load(fh)
    found = []
    for path in sorted(glob.glob(os.path.join(ROOT, "autorun_state_*.json"))):
        m = STATE_RE.match(os.path.basename(path))
        if not m:
            continue
        slot = m.group(1)
        try:
            with open(path, encoding="utf-8") as fh:
                bounds = (json.load(fh) or {}).get("range")
        except (OSError, ValueError):
            bounds = None
        if not bounds:
            bounds = range_from_log(slot)
        if not bounds:
            continue                 # слот без --range: участка нет, считать нечего
        found.append({"slot": slot, "lo": int(bounds[0]), "hi": int(bounds[1])})
    return found


def load_ranges():
    """Участки слотов: parallel_range_NN.json (пишет autorun.py --range --slot)
    и, если есть, общий parallel_ranges.json, составленный вручную."""
    ranges = {}
    if os.path.exists(RANGES):
        with open(RANGES, encoding="utf-8") as fh:
            for r in json.load(fh):
                ranges[str(r["slot"])] = r
    for path in glob.glob(os.path.join(ROOT, "parallel_range_*.json")):
        with open(path, encoding="utf-8") as fh:
            r = json.load(fh)
        ranges[str(r["slot"])] = r
    return [ranges[k] for k in sorted(ranges)]


def snapshot():
    ranges = load_ranges() or discover_ranges()
    if not ranges:
        raise SystemExit("Не нашёл ни одного слота (parallel_range_*.json или "
                         "autorun_state_*.json с --range) — параллельный прогон "
                         "не запускался.")

    excluded = load_excluded()
    total_done = total_all = 0
    print("%-5s %-11s %9s %7s  %s" % ("слот", "главы", "готово", "%", "сейчас"))
    print("-" * 100)
    for r in ranges:
        slot, lo, hi = r["slot"], r["lo"], r["hi"]
        span = [i for i in range(lo, hi + 1) if i not in excluded]
        done = sum(1 for i in span if os.path.exists(ru_path(i)))
        total_done += done
        total_all += len(span)

        last = ""
        log_file = os.path.join(ROOT, "autorun_%s.log" % slot)
        if os.path.exists(log_file):
            with open(log_file, encoding="utf-8", errors="replace") as fh:
                lines = fh.read().splitlines()
            if lines:
                # последняя содержательная строка, без линеек-разделителей
                for line in reversed(lines):
                    if line.strip() and "=====" not in line:
                        last = line[20:].strip()  # без метки времени
                        break
        print("%-5s %-11s %6d/%-3d %6.1f%%  %s"
              % (slot, "%d-%d" % (lo, hi), done, len(span),
                 100.0 * done / max(len(span), 1), last[:70]))

    print("-" * 100)
    print("итого по участкам: %d/%d (%.1f%%)"
          % (total_done, total_all, 100.0 * total_done / max(total_all, 1)))


def main():
    ap = argparse.ArgumentParser(description="Сводка по параллельным прогонам autorun")
    ap.add_argument("--watch", type=int, nargs="?", const=30, metavar="СЕК",
                    help="обновлять каждые СЕК секунд (без числа — 30)")
    args = ap.parse_args()

    if not args.watch:
        snapshot()
        return

    try:
        while True:
            # ANSI-очистка вместо os.system("cls"): не порождает процесс
            # и работает в том же окне без мигания.
            print("\033[2J\033[H", end="")
            print("Обновлено %s (каждые %d с, Ctrl+C — выход)\n"
                  % (dt.datetime.now().strftime("%H:%M:%S"), args.watch))
            snapshot()
            time.sleep(args.watch)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
