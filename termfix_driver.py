#!/usr/bin/env python3
"""Драйвер флота termfix: гонит агентов по заданиям fixes/fix_*.md.

Каждая правка проверяется приёмкой; испорченная глава восстанавливается из
снимка, снятого до запуска агента, и задание помечается упавшим. Несколько
экземпляров делят работу через атомарный захват .claim-файлов — запускать
можно любым числом копий:

    python termfix_driver.py --slot 1
    python termfix_driver.py --retry-failed   # снять .fail и залипшие .claim

Маркеры задания: .done — правка принята и совпадений не осталось; .part —
принята, но скан ещё видит варианты (законно для strict-правил и зон);
.fail — приёмка не прошла дважды, глава восстановлена. Упавший вызов агента
(лимит, авторизация, сеть) маркера не ставит: задание вернётся в очередь.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

import llm
import termfix
from common import ROOT, chapter_path, load_json, read_text, ru_path, validate_translation


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

FIX_DIR = os.path.join(ROOT, "fixes")
MARKERS = ("done", "part", "fail")
# Столько упавших подряд вызовов агента — и драйвер встаёт: это лимит или
# авторизация, а не глава, и гнать по ним всю очередь бессмысленно.
MAX_AGENT_ERRORS = 3


def log(slot, msg):
    line = "%s [t%s] %s" % (time.strftime("%H:%M:%S"), slot, msg)
    print(line, flush=True)
    with open(os.path.join(ROOT, "termfix_run.log"), "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def marker_path(idx, kind):
    return os.path.join(FIX_DIR, "fix_%04d.%s" % (idx, kind))


def finished(idx):
    return any(os.path.exists(marker_path(idx, m)) for m in MARKERS)


def claim(idx):
    try:
        fd = os.open(marker_path(idx, "claim"), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        return True
    except FileExistsError:
        return False


def release(idx):
    try:
        os.remove(marker_path(idx, "claim"))
    except OSError:
        pass


def snapshot(path):
    """Байты главы до агента. ru/ в .gitignore — git checkout её не вернёт."""
    with open(path, "rb") as fh:
        return fh.read()


def restore(path, blob):
    """Вернуть главу побайтно (переводы строк тоже), атомарно."""
    fd, tmp = tempfile.mkstemp(prefix=".%s." % os.path.basename(path),
                               suffix=".tmp", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(blob)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def run_agent(idx):
    """True — агент отработал; False — вызов упал (код возврата или is_error)."""
    proc = subprocess.run(
        llm.agent_cmd("Файл задания: fixes/fix_%04d.md" % idx, "termfix", "sonnet"),
        env=llm.agent_env(), cwd=ROOT, capture_output=True, timeout=900,
        encoding="utf-8", errors="replace",
    )
    # Точный учёт: конверт вызова копится в jsonl, как autorun_usage
    data = {}
    try:
        data = json.loads(proc.stdout or "")
        u = data.get("usage") or {}
        with open(os.path.join(ROOT, "termfix_usage.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"tag": "%04d" % idx,
                "in": u.get("input_tokens", 0),
                "cache_write": u.get("cache_creation_input_tokens", 0),
                "cache_read": u.get("cache_read_input_tokens", 0),
                "out": u.get("output_tokens", 0),
                "usd": data.get("total_cost_usd")}, ensure_ascii=False) + "\n")
    except (ValueError, OSError, AttributeError):
        pass
    return proc.returncode == 0 and isinstance(data, dict) and not data.get("is_error")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", default="0")
    ap.add_argument("--retry-failed", action="store_true",
                    help="снять .fail и .claim (только когда других экземпляров нет)")
    args = ap.parse_args()

    # Нет claude у профиля, которому он нужен, — llm остановит запуск здесь,
    # до захвата заданий (иначе остался бы залипший .claim).
    llm.agent_cmd("проверка")
    index_file = os.path.join(FIX_DIR, "index.json")
    if not os.path.exists(index_file):
        print("[!] Нет fixes/index.json. Сначала: python termfix.py")
        raise SystemExit(1)

    # Только задания текущей сборки: в fixes/ лежат и файлы прошлых прогонов,
    # чинить по ним — вносить уже отменённые правки.
    tasks = sorted(e["index"] for e in load_json(index_file, []))
    if args.retry_failed:
        for idx in tasks:
            for kind in ("fail", "claim"):
                if os.path.exists(marker_path(idx, kind)):
                    os.remove(marker_path(idx, kind))

    fixes = termfix.load_fixes()
    done = parts = fails = busy = agent_errors = 0
    for idx in tasks:
        if finished(idx):
            continue
        if not claim(idx):
            busy += 1
            continue
        if finished(idx):               # чужой экземпляр закончил, пока брали claim
            release(idx)
            continue
        path = ru_path(idx)
        if not os.path.exists(path):
            log(args.slot, "гл.%04d: нет %s — пропуск" % (idx, path))
            release(idx)
            continue
        orig = snapshot(path)
        marker = None
        try:
            for attempt in (1, 2):
                try:
                    agent_ok = run_agent(idx)
                except subprocess.TimeoutExpired:
                    log(args.slot, "гл.%04d: таймаут агента (попытка %d) — восстановлено"
                        % (idx, attempt))
                    restore(path, orig)
                    continue
                if not agent_ok:
                    # Лимит/сеть/CLI: правка не делалась или оборвана. Главу —
                    # к снимку, задание — обратно в очередь без маркера.
                    restore(path, orig)
                    agent_errors += 1
                    log(args.slot, "гл.%04d: вызов агента упал — восстановлено, "
                        "задание вернётся в очередь" % idx)
                    break
                agent_errors = 0
                problems = validate_translation(read_text(chapter_path(idx)),
                                                read_text(path))
                if not problems:
                    left = termfix.scan(read_text(path), fixes, idx)
                    marker = "part" if left else "done"
                    if left:
                        log(args.slot, "гл.%04d: приёмка прошла, но остались варианты: %s"
                            % (idx, ", ".join(f["canon"] for f, _ in left)))
                    break
                log(args.slot, "гл.%04d: приёмка сломана (%s) — восстановлено из снимка"
                    % (idx, problems[:1]))
                restore(path, orig)
            else:
                marker = "fail"
        except BaseException:
            # Ctrl+C или сбой посреди правки: глава — к снимку, claim снять,
            # чтобы следующий запуск взял задание заново.
            restore(path, orig)
            release(idx)
            raise
        if marker:
            with open(marker_path(idx, marker), "w") as fh:
                fh.write("")
        release(idx)
        done += marker == "done"
        parts += marker == "part"
        fails += marker == "fail"
        if marker in ("done", "part"):
            log(args.slot, "гл.%04d: готово%s (всего %d)"
                % (idx, "" if marker == "done" else " частично", done + parts))
        if agent_errors >= MAX_AGENT_ERRORS:
            log(args.slot, "агент падает %d раз подряд — стоп (лимит или авторизация?)"
                % agent_errors)
            break
    log(args.slot, "конец: сделано %d, частично %d, упало %d" % (done, parts, fails))
    if busy:
        log(args.slot, "пропущено из-за чужих .claim: %d. Если других экземпляров нет — "
            "это залипшие захваты: python termfix_driver.py --retry-failed" % busy)


if __name__ == "__main__":
    main()
