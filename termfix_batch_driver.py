#!/usr/bin/env python3
"""Драйвер пакетной правки: агент termfix-batch на пакет глав.

Приёмка идёт поглавно: испорченная глава восстанавливается из снимка, снятого
до запуска агента (ru/ в .gitignore — git checkout её не вернёт), остальные
из пакета остаются. Несколько экземпляров делят пакеты атомарным .claim.

    python termfix_batch_driver.py --slot 1
    python termfix_batch_driver.py --retry-failed   # снять .fail и залипшие .claim

Маркеры пакета: .done — все главы приняты и скан чист; .part — что-то
восстановлено, агент не успел (таймаут) или скан ещё видит варианты; .fail —
не используется для новых прогонов, снимается --retry-failed. Упавший вызов
агента (лимит, авторизация, сеть) маркера не ставит: пакет вернётся в очередь.
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
# авторизация, а не пакет.
MAX_AGENT_ERRORS = 3


def log(slot, msg):
    line = "%s [b%s] %s" % (time.strftime("%H:%M:%S"), slot, msg)
    print(line, flush=True)
    with open(os.path.join(ROOT, "termfix_batch.log"), "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def marker_path(tag, kind):
    return os.path.join(FIX_DIR, "batch_%03d.%s" % (tag, kind))


def finished(tag):
    return any(os.path.exists(marker_path(tag, m)) for m in MARKERS)


def claim(tag):
    try:
        fd = os.open(marker_path(tag, "claim"), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        return True
    except FileExistsError:
        return False


def release(tag):
    try:
        os.remove(marker_path(tag, "claim"))
    except OSError:
        pass


def snapshot(path):
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


def run_agent(tag):
    """(ok, отчёт): ok=False — вызов упал (код возврата или is_error)."""
    proc = subprocess.run(
        llm.agent_cmd("Файл задания: fixes/batch_%03d.md" % tag, "termfix-batch", "sonnet"),
        env=llm.agent_env(), cwd=ROOT, capture_output=True, timeout=3600,
        encoding="utf-8", errors="replace",
    )
    data = {}
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
    except (ValueError, OSError, AttributeError):
        pass
    if not isinstance(data, dict):
        data = {}
    ok = proc.returncode == 0 and not data.get("is_error")
    report = str(data.get("result") or "")[:80]
    if not ok and not report:
        report = (proc.stderr or "").strip()[-80:]
    return ok, report


def accept(chapters, snaps, fixes):
    """Поглавная приёмка: (восстановленные, с остатком вариантов)."""
    broken, left = [], []
    for idx in chapters:
        if idx not in snaps:
            continue
        path = ru_path(idx)
        problems = validate_translation(read_text(chapter_path(idx)), read_text(path))
        if problems:
            restore(path, snaps[idx])
            broken.append(idx)
        elif termfix.scan(read_text(path), fixes, idx):
            left.append(idx)
    return broken, left


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", default="0")
    ap.add_argument("--retry-failed", action="store_true",
                    help="снять .fail и .claim (только когда других экземпляров нет)")
    args = ap.parse_args()

    # Нет claude у профиля, которому он нужен, — llm остановит запуск здесь,
    # до захвата заданий (иначе остался бы залипший .claim).
    llm.agent_cmd("проверка")
    index_file = os.path.join(FIX_DIR, "batch_index.json")
    if not os.path.exists(index_file):
        print("[!] Нет fixes/batch_index.json. Сначала: python build_termfix_batches.py")
        raise SystemExit(1)

    batches = load_json(index_file, [])
    if args.retry_failed:
        for b in batches:
            for kind in ("fail", "claim"):
                if os.path.exists(marker_path(b["batch"], kind)):
                    os.remove(marker_path(b["batch"], kind))

    fixes = termfix.load_fixes()
    busy = agent_errors = 0
    for b in batches:
        tag = b["batch"]
        if finished(tag):
            continue
        if not claim(tag):
            busy += 1
            continue
        if finished(tag):               # чужой экземпляр закончил, пока брали claim
            release(tag)
            continue
        snaps = {}
        for idx in b["chapters"]:
            if os.path.exists(ru_path(idx)):
                snaps[idx] = snapshot(ru_path(idx))
            else:
                log(args.slot, "пакет %03d: нет ru/chapter_%04d.txt — глава пропущена"
                    % (tag, idx))
        try:
            timed_out = False
            try:
                agent_ok, report = run_agent(tag)
            except subprocess.TimeoutExpired:
                # Агент мог успеть поправить часть глав: каждую — через ту же
                # приёмку, что и в обычной ветке.
                timed_out, agent_ok, report = True, True, "таймаут"
            if not agent_ok:
                for idx, blob in snaps.items():
                    restore(ru_path(idx), blob)
                release(tag)
                agent_errors += 1
                log(args.slot, "пакет %03d: вызов агента упал (%s) — главы восстановлены, "
                    "пакет вернётся в очередь" % (tag, report or "без вывода"))
                if agent_errors >= MAX_AGENT_ERRORS:
                    log(args.slot, "агент падает %d раз подряд — стоп (лимит или авторизация?)"
                        % agent_errors)
                    break
                continue
            agent_errors = 0
            broken, left = accept(b["chapters"], snaps, fixes)
        except BaseException:
            # Ctrl+C или сбой посреди правки: пакет — к снимкам, claim снять.
            for idx, blob in snaps.items():
                restore(ru_path(idx), blob)
            release(tag)
            raise
        marker = "done" if not (broken or left or timed_out) else "part"
        open(marker_path(tag, marker), "w").close()
        release(tag)
        log(args.slot, "пакет %03d: %s%s%s" % (
            tag, report or "агент молчит",
            "" if not broken else " | ОТКАЧЕНО: %s" % broken,
            "" if not left else " | остались варианты: %s" % left))
    log(args.slot, "конец")
    if busy:
        log(args.slot, "пропущено из-за чужих .claim: %d. Если других экземпляров нет — "
            "это залипшие захваты: python termfix_batch_driver.py --retry-failed" % busy)


if __name__ == "__main__":
    main()
