#!/usr/bin/env python3
"""Сводка по параллельным прогонам autorun: один экран вместо 16 логов.

    python parallel_status.py              # один снимок
    python parallel_status.py --watch      # обновляется каждые 30 с (Ctrl+C — выход)
    python parallel_status.py --watch 10   # каждые 10 с

Читает parallel_ranges.json (границы участков), логи autorun_NN.log
(последняя строка = чем слот занят) и ru/ (сколько глав участка уже на диске).
Ничего не пишет.
"""

import argparse
import datetime as dt
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


def snapshot():
    if not os.path.exists(RANGES):
        raise SystemExit("Нет parallel_ranges.json — параллельный прогон не запускался.")
    with open(RANGES, encoding="utf-8") as fh:
        ranges = json.load(fh)

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
