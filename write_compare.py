#!/usr/bin/env python3
"""Режим письма: одна и та же глава несколькими моделями — выбрать писателя.

    python write_compare.py 4 --models claude-opus-5,claude-sonnet-5,claude-haiku-4-5

Берёт готовое задание write_tasks/task_NNNN.md (его строит write_run.py; для
главы 1 достаточно `python write_run.py --count 1`) и отдаёт его агенту writer
на каждой модели. Историю книги не трогает: тексты ложатся в
compare/NNNN/<модель>/, а в ru/ и story/ ничего не пишется.

Для слепого чтения тексты копируются в compare/NNNN/blind/A.txt, B.txt, …
в случайном порядке; какая буква какой модели — в compare/NNNN/key.json.
Сначала читай и ранжируй буквы, потом открывай ключ.
"""

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import llm
from author_common import notes_path, rel, require_concept, task_path, validate_chapter
from common import ROOT, load_json, read_text, ru_path, save_json, split_paragraphs, write_text

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

COMPARE_DIR = os.path.join(ROOT, "compare")
TIMEOUT = 1800


def run_one(idx, model, base, effort):
    out_dir = os.path.join(base, model.replace("/", "_"))
    shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(out_dir)
    chapter = os.path.join(out_dir, "chapter_%04d.txt" % idx)
    notes = os.path.join(out_dir, "notes_%04d.json" % idx)
    task = os.path.join(out_dir, "task_%04d.md" % idx)
    text = read_text(task_path(idx))
    for old, new in ((rel(ru_path(idx)), rel(chapter)), (rel(notes_path(idx)), rel(notes))):
        if old not in text:
            raise SystemExit("В задании нет пути %s — оно собрано не write_run.py?" % old)
        text = text.replace(old, new)
    write_text(task, text)

    started = time.time()
    rec = {"model": model, "dir": rel(out_dir), "chapter": chapter}
    try:
        proc = subprocess.run(
            llm.agent_cmd("Файл задания: %s" % rel(task), "writer", model, effort=effort),
            env=llm.agent_env(), cwd=ROOT, capture_output=True, timeout=TIMEOUT,
            encoding="utf-8", errors="replace")
        data = json.loads(proc.stdout or "{}")
        usage = data.get("usage") or {}
        rec.update(usd=data.get("total_cost_usd"), out_tokens=usage.get("output_tokens"),
                   error=(str(data.get("result"))[:120] if data.get("is_error") or proc.returncode
                          else None))
    except subprocess.TimeoutExpired:
        rec["error"] = "таймаут %d с" % TIMEOUT
    except ValueError:
        rec["error"] = "ответ CLI не JSON: %s" % (proc.stderr or proc.stdout or "")[-120:]
    rec["seconds"] = round(time.time() - started)
    return rec


def main():
    ap = argparse.ArgumentParser(description="Одна глава несколькими моделями")
    ap.add_argument("chapter", type=int)
    ap.add_argument("--models", required=True, help="идентификаторы моделей через запятую")
    ap.add_argument("--effort", choices=("low", "medium", "high", "xhigh", "max"))
    ap.add_argument("--jobs", type=int, default=4, help="сколько моделей писать одновременно")
    args = ap.parse_args()

    concept = require_concept()
    idx = args.chapter
    if not os.path.exists(task_path(idx)):
        raise SystemExit("Нет %s. Задание строит write_run.py, когда доходит до этой главы."
                         % rel(task_path(idx)))
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    base = os.path.join(COMPARE_DIR, "%04d" % idx)
    os.makedirs(base, exist_ok=True)
    llm.agent_cmd("проверка")       # нет claude у профиля, которому он нужен, — стоп здесь

    print("Глава %d, моделей %d, одновременно %d" % (idx, len(models), args.jobs), flush=True)
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(lambda m: run_one(idx, m, base, args.effort), models))

    for r in results:
        problems = [r["error"]] if r.get("error") else validate_chapter(
            idx, concept["chars"], r["chapter"])
        r["problems"] = problems
        if os.path.exists(r["chapter"]):
            body = split_paragraphs(read_text(r["chapter"]))[1:]
            r["chars"], r["paras"] = sum(len(p) for p in body), len(body)
        if not load_json(r["chapter"].replace("chapter_", "notes_").replace(".txt", ".json"), None):
            r["problems"] = problems + ["заметки не записаны или не JSON"]

    blind = os.path.join(base, "blind")
    shutil.rmtree(blind, ignore_errors=True)
    os.makedirs(blind)
    ready = [r for r in results if os.path.exists(r["chapter"])]
    random.shuffle(ready)
    key = {}
    for letter, r in zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", ready):
        shutil.copyfile(r["chapter"], os.path.join(blind, letter + ".txt"))
        key[letter] = r["model"]
    save_json(os.path.join(base, "key.json"), key)
    save_json(os.path.join(base, "results.json"),
              [{k: v for k, v in r.items() if k != "chapter"} for r in results])

    print("\n%-22s %7s %6s %6s %8s  %s" % ("модель", "знаков", "абз.", "сек", "usd", "приёмка"))
    for r in results:
        print("%-22s %7s %6s %6s %8s  %s" % (
            r["model"], r.get("chars", "-"), r.get("paras", "-"), r["seconds"],
            ("%.3f" % r["usd"]) if r.get("usd") is not None else "-",
            "; ".join(r["problems"])[:90] or "принята"))
    print("\nСлепое чтение: %s (ключ — %s)" % (rel(blind), rel(os.path.join(base, "key.json"))))


if __name__ == "__main__":
    main()
