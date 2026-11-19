#!/usr/bin/env python3
"""Режим письма: модель пишет книгу глава за главой по замыслу из BOOK.md.

    python write_run.py                # писать с первой ненаписанной главы до конца книги
    python write_run.py --count 5      # только пять глав
    python write_run.py --to 40        # до главы 40 включительно
    python write_run.py --edit         # каждую главу читает редактор; брак переписывается
    python write_run.py --status       # сколько написано

Главы пишутся строго по порядку и в один поток: каждая опирается на текст и
итог предыдущей, параллельных слотов здесь быть не может. На каждую главу:

  1. план главы (если его нет — write_plan.py спланирует том, арки или блок
     глав; итоги дописанных арок и томов перед этим сводит летописец);
  2. задание write_tasks/task_NNNN.md: замысел, карта книги, открытые линии,
     библия мира, пересказ последних глав, конец предыдущей главы, план этой;
  3. агент writer пишет ru/chapter_NNNN.txt и заметки story/notes_NNNN.json;
  4. формальная приёмка (заголовок, объём, разметка, обрыв), до трёх попыток;
  5. с --edit — агент editor сверяет главу с планом и историей;
  6. итог главы уходит в story/summaries.json и story/bible.json.

Прерванный прогон продолжается с первой главы, у которой нет итога.
Готовые главы собираются в книгу как обычно: python build_epub.py
"""

import argparse
import os
import shutil

import autorun
from author_common import (
    STORY_DIR,
    TASK_DIR,
    apply_notes,
    bible_block,
    chapter_tail,
    concept_block,
    load_bible,
    load_digests,
    load_outline,
    load_summaries,
    notes_path,
    rel,
    require_concept,
    review_path,
    story_map,
    summaries_block,
    task_path,
    threads_block,
    validate_chapter,
    validate_notes,
    written,
)
from common import ROOT, load_json, ru_path, write_text
from write_plan import ensure_digests, ensure_plan

REJECT_DIR = os.path.join(ROOT, "ru_rejected")
MAX_ATTEMPTS = 3
WRITE_TIMEOUT = 1800
EDIT_TIMEOUT = 1200


def build_task(concept, idx, feedback=None):
    outline = load_outline()
    plan = outline["chapters"][str(idx)]
    digests = load_digests()
    nxt = outline["chapters"].get(str(idx + 1))
    focus = " ".join([plan["title"], plan["brief"]] + plan["beats"])
    parts = [
        "# Задание писателю: глава %d\n" % idx,
        "- Файл главы: `%s`" % rel(ru_path(idx)),
        "- Файл заметок: `%s`" % rel(notes_path(idx)),
        "- Первая строка главы: `Глава %d: %s`" % (idx, plan["title"]),
        "- Объём: около %d знаков (допустимо от %d до %d)" % (
            concept["chars"], int(concept["chars"] * 0.8), int(concept["chars"] * 1.3)),
        "- Глава %d из %d%s\n" % (idx, concept["total"],
                                  " — ПОСЛЕДНЯЯ глава книги, история должна закончиться"
                                  if idx == concept["total"] else ""),
        concept_block(concept),
        "## Карта книги\n\n%s\n" % story_map(outline, digests, idx),
        "## Открытые линии (то, чего читатель ждёт; не забывай о них)\n\n%s\n"
        % threads_block(digests),
        "## Библия мира\n\nИмена, род и факты держи строго по ней.\n\n%s\n"
        % bible_block(load_bible(), idx, focus),
        "## Что было раньше (пересказ последних глав)\n\n%s\n"
        % summaries_block(load_summaries(), idx),
    ]
    tail = chapter_tail(idx - 1)
    if tail:
        parts.append("## Конец предыдущей главы (продолжай с этого места, не пересказывая)\n\n"
                     "%s\n" % tail)
    parts.append("## План этой главы\n\n**%s**\n\n%s\n\nСобытия по порядку:\n%s\n" % (
        plan["title"], plan["brief"], "\n".join("%d. %s" % (i, b)
                                                for i, b in enumerate(plan["beats"], 1))))
    if plan.get("hook"):
        parts.append("Чем глава кончается: %s\n" % plan["hook"])
    if nxt:
        parts.append("## Следующая глава (НЕ пиши её события, только не противоречь)\n\n"
                     "«%s»: %s\n" % (nxt["title"], nxt["brief"]))
    if feedback:
        parts.append("## Прошлая попытка отклонена — исправь\n\n%s\n"
                     % "\n".join("- %s" % f for f in feedback))
    write_text(task_path(idx), "\n".join(parts))


def set_aside(idx, attempt):
    """Убрать отклонённый текст из ru/: недописанную главу не должен подхватить сборщик."""
    if os.path.exists(ru_path(idx)):
        os.makedirs(REJECT_DIR, exist_ok=True)
        shutil.move(ru_path(idx), os.path.join(
            REJECT_DIR, "chapter_%04d_write%d.txt" % (idx, attempt)))


def review(idx, model, effort):
    """Редактор читает главу. -> список претензий (пустой — принято)."""
    if os.path.exists(review_path(idx)):
        os.remove(review_path(idx))
    prompt = ("Файл задания писателя: %s\nФайл главы: %s\nВердикт запиши в: %s"
              % (rel(task_path(idx)), rel(ru_path(idx)), rel(review_path(idx))))
    ok, report = autorun.run_claude(prompt, "editor", timeout=EDIT_TIMEOUT,
                                    tag="e%04d" % idx, model=model, effort=effort)
    data = load_json(review_path(idx), None)
    if not ok or not isinstance(data, dict) or data.get("verdict") not in ("ok", "rewrite"):
        # Редактор — вторая линия; его сбой не должен останавливать книгу.
        autorun.log("    [!] гл.%04d: редактор не дал вердикта (%s) — глава принята без него"
                    % (idx, autorun.short(report)))
        return []
    if data["verdict"] == "ok":
        return []
    return [str(i).strip() for i in (data.get("issues") or []) if str(i).strip()] \
        or ["редактор вернул главу без пояснений"]


def write_chapter(concept, idx, args):
    ensure_plan(concept, idx, args.plan_model or args.model, args.effort)
    feedback, edited = None, False
    for attempt in range(1, MAX_ATTEMPTS + 1):
        build_task(concept, idx, feedback)
        if os.path.exists(notes_path(idx)):
            os.remove(notes_path(idx))
        set_aside(idx, attempt - 1)
        ok, report = autorun.run_claude(
            "Файл задания: %s" % rel(task_path(idx)), "writer",
            timeout=WRITE_TIMEOUT, tag="w%04d" % idx, model=args.model, effort=args.effort)
        if not ok:
            autorun.log("    [!] гл.%04d: вызов писателя упал: %s" % (idx, autorun.short(report)))
            feedback = None
            continue
        problems = validate_chapter(idx, concept["chars"])
        notes, note_problems = validate_notes(idx)
        problems += note_problems
        if not problems and args.edit and not edited:
            edited = True            # редактор возвращает главу не больше одного раза
            problems = review(idx, args.edit_model or args.model, args.effort)
            if problems:
                autorun.log("    гл.%04d: редактор вернул на переписывание" % idx)
        if not problems:
            apply_notes(idx, notes)
            size = os.path.getsize(ru_path(idx))
            autorun.log("гл.%04d готово | %s | %d байт" % (idx, autorun.short(report, 80), size))
            return True
        autorun.log("    [!] гл.%04d отклонена (попытка %d): %s"
                    % (idx, attempt, "; ".join(problems)[:300]))
        feedback = problems
    set_aside(idx, MAX_ATTEMPTS)
    return False


def main():
    ap = argparse.ArgumentParser(description="Режим письма: модель пишет книгу по замыслу")
    ap.add_argument("--to", type=int, help="последняя глава этого прогона")
    ap.add_argument("--count", type=int, help="сколько глав написать")
    ap.add_argument("--model", default="claude-opus-5-5", help="модель писателя")
    ap.add_argument("--plan-model", help="модель планировщика (по умолчанию как --model)")
    ap.add_argument("--edit", action="store_true", help="каждую главу читает агент editor")
    ap.add_argument("--edit-model", help="модель редактора (по умолчанию как --model)")
    ap.add_argument("--effort", choices=("low", "medium", "high", "xhigh", "max"))
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    concept = require_concept()
    summaries = load_summaries()
    first = next((i for i in range(1, concept["total"] + 1) if not written(i, summaries)), None)
    if args.status or first is None:
        done = sum(1 for i in range(1, concept["total"] + 1) if written(i, summaries))
        print("«%s»: написано %d из %d глав, спланировано до гл. %d"
              % (concept["title"], done, concept["total"],
                 max([int(n) for n in load_outline()["chapters"]] or [0])))
        if first is None:
            print("Книга дописана. Сборка: python build_epub.py")
        return

    last = min(args.to or concept["total"], concept["total"])
    if args.count:
        last = min(last, first + args.count - 1)
    for d in (STORY_DIR, TASK_DIR, os.path.dirname(ru_path(1))):
        os.makedirs(d, exist_ok=True)
    autorun.log("Письмо: «%s», главы %d–%d из %d" % (concept["title"], first, last, concept["total"]))
    for idx in range(first, last + 1):
        if not write_chapter(concept, idx, args):
            autorun.log("[!] гл.%04d не далась за %d попытки — стоп. Отклонённые тексты в "
                        "ru_rejected/, задание: %s" % (idx, MAX_ATTEMPTS, rel(task_path(idx))))
            raise SystemExit(1)
    # Итог арки, которая кончилась последней главой прогона, сводится сразу:
    # иначе он появился бы только при следующем запуске.
    ensure_digests(concept, last + 1, args.plan_model or args.model, args.effort)
    autorun.log("Письмо: главы %d–%d готовы" % (first, last))


if __name__ == "__main__":
    main()
