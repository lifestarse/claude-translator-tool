#!/usr/bin/env python3
"""Режим письма: план книги по уровням и память о написанном.

    python write_plan.py              # довести план до следующей ненаписанной главы
    python write_plan.py --upto 40    # довести поглавный план до главы 40
    python write_plan.py --show       # что уже спланировано

Уровни: книга -> тома -> арки -> блоки глав. Ни один вызов планировщика не
охватывает больше одного уровня вниз:

  тома    один раз на всю книгу и только у длинных книг («Делить на тома от»
          в BOOK.md, по умолчанию от 300 глав);
  арки    на один том (у короткой книги — на всю книгу), когда том начинается;
  главы   блоком внутри одной арки («Глав в блоке плана»).

План строится по ходу письма, а не вперёд на всю книгу: когда арка дописана,
агент chronicler сводит её фактический итог и список открытых линий, когда
дописан том — итог тома. Следующие арки и главы планируются уже по этим итогам,
а не по давним намерениям. write_run.py зовёт всё это сам.
"""

import argparse
import os

import autorun
from author_common import (
    DIGESTS_FILE,
    MAX_THREADS,
    OUTLINE_FILE,
    STORY_DIR,
    TASK_DIR,
    arc_of,
    bible_block,
    concept_block,
    load_bible,
    load_digests,
    load_outline,
    load_summaries,
    rel,
    require_concept,
    story_map,
    summaries_block,
    threads_block,
    volume_of,
)
from common import load_json, save_json, write_text

PLAN_TIMEOUT = 1800


def check_spans(data, key, lo, hi):
    """Тома или арки: подряд, без дыр, ровно от lo до hi."""
    items = (data or {}).get(key)
    if not isinstance(items, list) or not items:
        return "нет списка %s" % key
    expect = lo
    for n, a in enumerate(items, 1):
        if not isinstance(a, dict):
            return "%s[%d] — не объект" % (key, n)
        for field in ("title", "summary"):
            if not str(a.get(field) or "").strip():
                return "%s[%d]: пустое поле %s" % (key, n, field)
        if a.get("from") != expect or not isinstance(a.get("to"), int) or a["to"] < a["from"]:
            return "%s[%d]: главы должны идти подряд с %d" % (key, n, expect)
        expect = a["to"] + 1
    if expect - 1 != hi:
        return "%s покрывают главы %d–%d, а нужно %d–%d" % (key, lo, expect - 1, lo, hi)
    return None


def check_block(data, lo, hi):
    chapters = (data or {}).get("chapters")
    if not isinstance(chapters, list):
        return "нет списка chapters"
    got = {}
    for ch in chapters:
        if not isinstance(ch, dict) or not isinstance(ch.get("n"), int):
            return "запись главы без номера n"
        for key in ("title", "brief"):
            if not str(ch.get(key) or "").strip():
                return "у главы %s пустое поле %s" % (ch.get("n"), key)
        if not isinstance(ch.get("beats"), list) or len(ch["beats"]) < 2:
            return "у главы %d меньше двух событий в beats" % ch["n"]
        got[ch["n"]] = ch
    missing = [n for n in range(lo, hi + 1) if n not in got]
    if missing:
        return "в плане нет глав: %s" % missing
    return None


def check_digest(data, with_threads):
    if not isinstance(data, dict) or len(str(data.get("digest") or "").strip()) < 80:
        return "нет внятного digest"
    if with_threads:
        threads = data.get("threads")
        if not isinstance(threads, list) or any(not str(t).strip() for t in threads):
            return "threads — не список строк"
    return None


def run_agent(agent, task_file, out_file, check, model, effort, tag):
    """Запуск планировщика или летописца с одной повторной попыткой."""
    why = None
    for attempt in (1, 2):
        if os.path.exists(out_file):
            os.remove(out_file)
        prompt = "Файл задания: %s" % rel(task_file)
        if why:
            prompt += "\nПрошлая попытка не принята: %s. Исправь именно это." % why
        ok, report = autorun.run_claude(prompt, agent, timeout=PLAN_TIMEOUT,
                                        tag=tag, model=model, effort=effort)
        data = load_json(out_file, None)
        why = check(data) if ok else "вызов агента упал: %s" % autorun.short(report)
        if not why:
            return data
        autorun.log("    [!] %s: результат не принят (попытка %d): %s" % (agent, attempt, why))
    raise SystemExit("Агент %s дважды не справился: %s. Задание: %s"
                     % (agent, why, rel(task_file)))


def header(title, mode, concept, out, extra=""):
    return ("# %s\n\nРежим: `%s`. %s\n\n- Всего глав в книге: %d\n"
            "- Объём главы: около %d знаков\n- Результат запиши в: `%s`\n\n%s"
            % (title, mode, extra, concept["total"], concept["chars"], rel(out),
               concept_block(concept)))


# --- память: итоги дописанных арок и томов ---------------------------------

def ensure_digests(concept, idx, model, effort):
    """Свести итог каждой арки и каждого тома, целиком лежащих до главы idx.

    Вызывается перед любым планированием и перед каждой главой, поэтому
    прерванный прогон сам досводит пропущенное."""
    outline = load_outline()
    summaries = load_summaries()
    for arc in outline["arcs"]:
        digests = load_digests()
        if arc["to"] >= idx or str(arc["n"]) in digests["arcs"]:
            continue
        if any(str(i) not in summaries for i in range(arc["from"], arc["to"] + 1)):
            continue                      # арка ещё не дописана целиком
        task = os.path.join(TASK_DIR, "digest_arc_%03d.md" % arc["n"])
        out = os.path.join(STORY_DIR, "digest_arc_%03d.json" % arc["n"])
        chapters = "\n".join("- Гл. %d: %s" % (i, summaries[str(i)])
                             for i in range(arc["from"], arc["to"] + 1))
        write_text(task, header(
            "Задание летописцу: итог арки %d" % arc["n"], "arc", concept, out,
            "Сведи фактический итог дописанной арки и обнови открытые линии.") + (
            "\n## Арка %d «%s», гл. %d–%d — как она была задумана\n\n%s\n\n"
            "## Что в ней произошло на самом деле (пересказ глав)\n\n%s\n\n"
            "## Открытые линии до этой арки\n\n%s\n\n"
            "Открытых линий держи не больше %d.\n"
        ) % (arc["n"], arc["title"], arc["from"], arc["to"], arc["summary"], chapters,
             threads_block(digests), MAX_THREADS))
        autorun.log("Летописец: итог арки %d (гл. %d–%d)" % (arc["n"], arc["from"], arc["to"]))
        data = run_agent("chronicler", task, out, lambda d: check_digest(d, True),
                         model, effort, "digest_a%03d" % arc["n"])
        digests = load_digests()
        digests["arcs"][str(arc["n"])] = " ".join(str(data["digest"]).split())
        digests["threads"] = [" ".join(str(t).split()) for t in data["threads"]][:MAX_THREADS]
        save_json(DIGESTS_FILE, digests)

    for vol in outline["volumes"]:
        digests = load_digests()
        arcs = [a for a in outline["arcs"] if vol["from"] <= a["from"] <= vol["to"]]
        if vol["to"] >= idx or str(vol["n"]) in digests["volumes"] or not arcs \
                or arcs[-1]["to"] != vol["to"] \
                or any(str(a["n"]) not in digests["arcs"] for a in arcs):
            continue
        task = os.path.join(TASK_DIR, "digest_volume_%03d.md" % vol["n"])
        out = os.path.join(STORY_DIR, "digest_volume_%03d.json" % vol["n"])
        write_text(task, header(
            "Задание летописцу: итог тома %d" % vol["n"], "volume", concept, out,
            "Сведи фактический итог дописанного тома.") + (
            "\n## Том %d «%s», гл. %d–%d — как он был задуман\n\n%s\n\n"
            "## Итоги его арок по тексту\n\n%s\n\n## Открытые линии\n\n%s\n"
        ) % (vol["n"], vol["title"], vol["from"], vol["to"], vol["summary"],
             "\n".join("- Арка %d «%s», гл. %d–%d: %s" % (
                 a["n"], a["title"], a["from"], a["to"], digests["arcs"][str(a["n"])])
                 for a in arcs), threads_block(digests)))
        autorun.log("Летописец: итог тома %d (гл. %d–%d)" % (vol["n"], vol["from"], vol["to"]))
        data = run_agent("chronicler", task, out, lambda d: check_digest(d, False),
                         model, effort, "digest_v%03d" % vol["n"])
        digests = load_digests()
        digests["volumes"][str(vol["n"])] = " ".join(str(data["digest"]).split())
        save_json(DIGESTS_FILE, digests)


# --- план: тома -> арки -> главы -------------------------------------------

def ensure_volumes(concept, model, effort):
    outline = load_outline()
    if outline["volumes"] or outline["arcs"] or concept["total"] < concept["volume_from"]:
        return
    task = os.path.join(TASK_DIR, "plan_volumes.md")
    out = os.path.join(STORY_DIR, "volumes.json")
    write_text(task, header("Задание планировщику: тома книги", "volumes", concept, out,
                            "Раздели всю книгу на тома."))
    autorun.log("Планирую тома книги на %d глав" % concept["total"])
    data = run_agent("planner", task, out,
                     lambda d: check_spans(d, "volumes", 1, concept["total"]),
                     model, effort, "plan_volumes")
    outline = load_outline()
    outline["volumes"] = [dict(v, n=n) for n, v in enumerate(data["volumes"], 1)]
    save_json(OUTLINE_FILE, outline)
    autorun.log("Томов: %d" % len(outline["volumes"]))


def ensure_arcs(concept, idx, model, effort):
    """Арки для отрезка, в который попадает глава idx: том или вся книга."""
    ensure_volumes(concept, model, effort)
    outline = load_outline()
    if arc_of(outline, idx):
        return
    vol = volume_of(outline, idx)
    lo, hi = (vol["from"], vol["to"]) if vol else (1, concept["total"])
    planned_to = max([a["to"] for a in outline["arcs"]] or [0])
    if planned_to + 1 != lo:
        raise SystemExit("План арок рваный: арки кончаются на гл. %d, а отрезок начинается с %d. "
                         "Поправь story/outline.json." % (planned_to, lo))
    scope = ("тома %d «%s»" % (vol["n"], vol["title"])) if vol else "всей книги"
    task = os.path.join(TASK_DIR, "plan_arcs_%04d.md" % lo)
    out = os.path.join(STORY_DIR, "arcs_%04d.json" % lo)
    write_text(task, header(
        "Задание планировщику: арки %s" % scope, "arcs", concept, out,
        "Раздели на сюжетные арки главы с %d по %d включительно." % (lo, hi)) + (
        "\n## Карта книги\n\n%s\n\n## Открытые линии\n\n%s\n\n"
        "## Что написано только что (пересказ последних глав)\n\n%s\n\n"
        "## Библия мира\n\n%s\n"
    ) % (story_map(outline, idx=lo), threads_block(), summaries_block(load_summaries(), lo),
         bible_block(load_bible())))
    autorun.log("Планирую арки %s (гл. %d–%d)" % (scope, lo, hi))
    data = run_agent("planner", task, out, lambda d: check_spans(d, "arcs", lo, hi),
                     model, effort, "plan_arcs_%04d" % lo)
    outline = load_outline()
    for a in data["arcs"]:
        outline["arcs"].append(dict(a, n=len(outline["arcs"]) + 1))
    save_json(OUTLINE_FILE, outline)
    autorun.log("Арок в плане: %d" % len(outline["arcs"]))


def plan_block(concept, lo, hi, model, effort):
    outline = load_outline()
    summaries = load_summaries()
    planned = "\n".join(
        "- Гл. %s «%s»: %s" % (n, outline["chapters"][n]["title"], outline["chapters"][n]["brief"])
        for n in sorted(outline["chapters"], key=int)
        if n not in summaries and int(n) < lo) or "(нет)"
    task = os.path.join(TASK_DIR, "plan_%04d_%04d.md" % (lo, hi))
    out = os.path.join(STORY_DIR, "plan_%04d_%04d.json" % (lo, hi))
    write_text(task, header(
        "Задание планировщику: главы %d–%d" % (lo, hi), "chapters", concept, out,
        "Составь поглавный план для глав с %d по %d включительно." % (lo, hi)) + (
        "\n## Карта книги\n\n%s\n\n## Открытые линии\n\n%s\n\n"
        "## Что уже написано (пересказ последних глав)\n\n%s\n\n"
        "## Спланировано, но ещё не написано\n\n%s\n\n"
        "## Библия мира (персонажи, места, понятия)\n\n%s\n"
    ) % (story_map(outline, idx=lo), threads_block(), summaries_block(summaries, lo), planned,
         bible_block(load_bible())))
    autorun.log("Планирую главы %d–%d" % (lo, hi))
    data = run_agent("planner", task, out, lambda d: check_block(d, lo, hi),
                     model, effort, "plan_%04d" % lo)
    outline = load_outline()
    for ch in data["chapters"]:
        if lo <= ch["n"] <= hi:
            outline["chapters"][str(ch["n"])] = {
                "title": str(ch["title"]).strip(),
                "brief": str(ch["brief"]).strip(),
                "beats": [str(b).strip() for b in ch["beats"] if str(b).strip()],
                "hook": str(ch.get("hook") or "").strip(),
            }
    save_json(OUTLINE_FILE, outline)


def ensure_plan(concept, idx, model, effort):
    """Гарантировать, что у главы idx есть план, а всё дописанное до неё сведено.

    Блок глав не пересекает границу арки: следующую арку лучше планировать,
    когда эта уже написана и её итог сведён."""
    os.makedirs(TASK_DIR, exist_ok=True)
    os.makedirs(STORY_DIR, exist_ok=True)
    ensure_digests(concept, idx, model, effort)
    ensure_arcs(concept, idx, model, effort)
    outline = load_outline()
    if str(idx) in outline["chapters"]:
        return
    arc = arc_of(outline, idx)
    plan_block(concept, idx, min(idx + concept["block"] - 1, arc["to"]), model, effort)


def main():
    ap = argparse.ArgumentParser(description="План книги для режима письма")
    ap.add_argument("--upto", type=int, help="довести поглавный план до этой главы")
    ap.add_argument("--show", action="store_true", help="показать готовый план и выйти")
    ap.add_argument("--model", default="claude-opus-5-5")
    ap.add_argument("--effort", choices=("low", "medium", "high", "xhigh", "max"))
    args = ap.parse_args()

    concept = require_concept()
    outline = load_outline()
    if args.show:
        print(story_map(outline))
        threads = load_digests()["threads"]
        if threads:
            print("\nОткрытые линии:\n" + threads_block())
        for n in sorted(outline["chapters"], key=int):
            ch = outline["chapters"][n]
            print("\nГл. %s «%s»\n  %s" % (n, ch["title"], ch["brief"]))
        return

    done = max([int(n) for n in outline["chapters"]] or [0])
    upto = min(args.upto or done + 1, concept["total"])
    idx = done + 1
    while idx <= upto:
        ensure_plan(concept, idx, args.model, args.effort)
        idx = max(int(n) for n in load_outline()["chapters"]) + 1
    print("Спланировано глав: %d из %d. Смотреть: python write_plan.py --show"
          % (max([int(n) for n in load_outline()["chapters"]] or [0]), concept["total"]))


if __name__ == "__main__":
    main()
