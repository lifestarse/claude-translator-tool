#!/usr/bin/env python3
"""Драйвер выровненной правки имён: агент glossfix на пакет глав.

Копия termfix_batch_driver с другим каталогом и агентом: приёмка поглавная,
испорченная глава откатывается к снимку, снятому до агента, претензии через
атомарный .claim. git checkout здесь не годится: ru/ в .gitignore, checkout
падал с «pathspec did not match», а битая глава оставалась в ru/ под записью
«ОТКАЧЕНО».

    python glossfix_driver.py --slot 1
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time

import llm
from common import (
    ROOT,
    chapter_path,
    load_json,
    read_text,
    ru_path,
    validate_translation,
    write_text,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

GFX_DIR = os.path.join(ROOT, "gfx")
# Куда уходит битая глава, если снимка нет: из ru/ её надо убрать, иначе её
# посчитает готовой каждый скрипт проекта. Тот же каталог, что у autorun.
REJECT_DIR = os.path.join(ROOT, "ru_rejected")
# Отчёт агента glossfix: `batch NNN ok | глав N | замен M`.
REPORT_RE = re.compile(r"batch\s+\d+\s+ok", re.I)
REPLACED_RE = re.compile(r"замен\s+(\d+)", re.I)


def log(slot, msg):
    line = "%s [g%s] %s" % (time.strftime("%H:%M:%S"), slot, msg)
    print(line, flush=True)
    with open(os.path.join(ROOT, "glossfix.log"), "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def claim(tag):
    try:
        fd = os.open(os.path.join(GFX_DIR, "batch_%03d.claim" % tag),
                     os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        return True
    except FileExistsError:
        return False


def unclaim(tag):
    try:
        os.remove(os.path.join(GFX_DIR, "batch_%03d.claim" % tag))
    except OSError:
        pass


def snapshot(chapters):
    """Текст глав пакета до агента: {idx: текст}. Откат идёт только отсюда."""
    snap = {}
    for idx in chapters:
        try:
            snap[idx] = read_text(ru_path(idx))
        except OSError:
            pass
    return snap


def restore(idx, snap):
    """Вернуть главу к снимку; без снимка — убрать из ru/ в ru_rejected/."""
    if idx in snap:
        write_text(ru_path(idx), snap[idx])
        return "к снимку"
    if os.path.exists(ru_path(idx)):
        os.makedirs(REJECT_DIR, exist_ok=True)
        os.replace(ru_path(idx), os.path.join(REJECT_DIR, "chapter_%04d.txt" % idx))
    return "в ru_rejected/"


def accept(chapters, snap):
    """Поглавная приёмка: битые откатываются. -> (битые, изменённые)."""
    broken, changed = [], []
    for idx in chapters:
        try:
            text = read_text(ru_path(idx))
            problems = validate_translation(read_text(chapter_path(idx)), text)
        except OSError as e:
            text, problems = None, [str(e)]
        if problems:
            restore(idx, snap)
            broken.append(idx)
        elif text != snap.get(idx):
            changed.append(idx)
    return broken, changed


def run_agent(tag, model="sonnet"):
    """-> (агент отработал без ошибки, первые 80 символов отчёта)."""
    proc = subprocess.run(
        llm.agent_cmd("Файл задания: gfx/batch_%03d.md" % tag, "glossfix", model),
        env=llm.agent_env(), cwd=ROOT, capture_output=True, timeout=3600,
        encoding="utf-8", errors="replace",
    )
    try:
        data = json.loads(proc.stdout or "")
        u = data.get("usage") or {}
        with open(os.path.join(ROOT, "termfix_usage.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"tag": "gfx_%03d" % tag,
                "in": u.get("input_tokens", 0),
                "cache_write": u.get("cache_creation_input_tokens", 0),
                "cache_read": u.get("cache_read_input_tokens", 0),
                "out": u.get("output_tokens", 0),
                "usd": data.get("total_cost_usd")}, ensure_ascii=False) + "\n")
        ok = proc.returncode == 0 and not data.get("is_error")
        return ok, str(data.get("result") or "")[:80]
    except (ValueError, OSError, AttributeError):
        return False, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", default="0")
    ap.add_argument("--model", default="sonnet",
                    help="модель агента glossfix; с тонкими «границами правки» (омонимы) — opus")
    args = ap.parse_args()
    # Нет claude у профиля, которому он нужен, — llm остановит запуск здесь,
    # до захвата заданий (иначе остался бы залипший .claim).
    llm.agent_cmd("проверка")

    for b in load_json(os.path.join(GFX_DIR, "batch_index.json"), []):
        tag = b["batch"]
        if os.path.exists(os.path.join(GFX_DIR, "batch_%03d.done" % tag)):
            continue
        if not claim(tag):
            continue
        snap = snapshot(b["chapters"])
        try:
            ok, report = run_agent(tag, args.model)
        except subprocess.TimeoutExpired:
            # Часть правок могла лечь на диск: приёмка всё равно нужна. Claim
            # снимается, иначе пакет не повторился бы никогда.
            broken, _changed = accept(b["chapters"], snap)
            unclaim(tag)
            log(args.slot, "пакет %03d: таймаут, будет повтор%s" % (
                tag, "" if not broken else " | ОТКАЧЕНО: %s" % broken))
            continue

        broken, changed = accept(b["chapters"], snap)
        # .done — только когда агент отчитался по формату, а заявленные замены
        # действительно легли на диск. Пустой ответ, ошибка CLI или лимит раньше
        # тоже давали .done: главы проходили приёмку нетронутыми, и пакет не
        # повторялся. «замен 0» без изменений — честный итог (все абзацы
        # оказались бытовыми), его повтор только жёг бы деньги.
        m = REPLACED_RE.search(report or "")
        claimed = int(m.group(1)) if m else None
        landed = bool(changed or broken) or claimed == 0
        if not ok or not REPORT_RE.search(report or "") or not landed:
            unclaim(tag)
            log(args.slot, "пакет %03d: %s — не засчитан, будет повтор%s" % (
                tag, report or "агент молчит",
                "" if not broken else " | ОТКАЧЕНО: %s" % broken))
            continue
        marker = "done" if not broken else "part"
        open(os.path.join(GFX_DIR, "batch_%03d.%s" % (tag, marker)), "w").close()
        log(args.slot, "пакет %03d: %s | изменено глав %d%s" % (
            tag, report, len(changed),
            "" if not broken else " | ОТКАЧЕНО: %s" % broken))
    log(args.slot, "конец")


if __name__ == "__main__":
    main()
