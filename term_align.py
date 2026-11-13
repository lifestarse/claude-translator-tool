#!/usr/bin/env python3
"""Имена к канону в готовом переводе — по выравниванию с оригиналом.

Когда термин получил каноническое имя после того, как главы с ним уже
переведены (аудит глоссария, ручная правка overrides), в этих главах остаётся
вариант переводчика. Скрипт находит такие места через выравнивание абзацев 1:1:
в абзаце оригинала стоит ключ, а в русском абзаце нет основ канонического имени
(правила поиска — term_match.py). Правит агент namefix: он меняет имя в нужном
падеже и согласует с ним род, а если сущность названа местоимением или ключ
значит другое — ничего не трогает.

Канон — выверенные записи глоссария и термины, прошедшие аудит
(glossary_audit/audited.json). Непроверенные варианты переводчика каноном не
считаются: иначе текст выровнялся бы к случайному имени. Нарицательные
категории не берутся — промах там чаще значит обычное слово, чем дрейф; имена
с «/» тоже: у них два значения.

Тонкая настройка — необязательный term_align.json в корне проекта:

    {"skip": ["Youth"],                          # ключи-обычные слова
     "only_in": {"Youth": [320, 454]},           # правило только в этих главах
     "resolve": {"Scales of Truth": {"ru": "Юстиция", "gender": "ж", "note": "…"}}}

    python term_align.py build [--range LO HI]          # задания align/fix_NN.md
    python term_align.py run [--model M] [--parallel K] # агент namefix на каждое
    python term_align.py status

Каждая глава после агента проходит приёмку. Если изменено что-то кроме
заданных абзацев, сломалось число абзацев или упала приёмка, глава
откатывается к снимку. Все замены пишутся в align/replacements.txt —
пролистай его глазами: агент ошибается уверенно.
"""

import argparse
import concurrent.futures
import difflib
import glob
import json
import os
import re
import shutil
import sys
import time

from common import ROOT, chapter_path, load_json, read_text, ru_path, save_json, split_paragraphs, \
    validate_translation, write_text
from lang import LANG
from term_match import aligned, canon_rx, gender_label, source_key_rx, translated_chapters

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ALIGN_DIR = os.path.join(ROOT, "align")
PLAN_FILE = os.path.join(ALIGN_DIR, "plan.json")
REPLACEMENTS = os.path.join(ALIGN_DIR, "replacements.txt")
CONFIG = os.path.join(ROOT, "term_align.json")
GLOSSARY_V2 = os.path.join(ROOT, "glossary_v2.json")
AUDITED_FILE = os.path.join(ROOT, "glossary_audit", "audited.json")
SKIP_CATEGORIES = {"Термин", "Термин мира", "Термин мира / класс", "Ранг предмета"}
AGENT = "namefix"
PER_TASK = 45


def rel(path):
    return os.path.relpath(path, ROOT).replace(os.sep, "/")


def load_rules():
    """Ключ оригинала → (каноническое имя, род, пояснение); и ограничения по главам."""
    g = (load_json(GLOSSARY_V2) or {}).get("glossary", [])
    audited = load_json(AUDITED_FILE, {}) or {}
    cfg = load_json(CONFIG, {}) or {}
    skip = set(cfg.get("skip") or [])
    only = {k: set(v) for k, v in (cfg.get("only_in") or {}).items()}
    min_len = 2 if LANG.script_re is not None else 4
    rules = {}
    for e in g:
        cn, ru = e["cn"], e.get("ru") or ""
        if e.get("source") == "translator" and cn not in audited:
            continue
        if cn in skip or e.get("category") in SKIP_CATEGORIES:
            continue
        if not ru or "/" in ru or ";" in ru or len(cn) < min_len:
            continue
        rules[cn] = (ru, e.get("gender", ""), re.sub(r"\s+", " ", e.get("description", ""))[:160])
    for cn, r in (cfg.get("resolve") or {}).items():
        rules[cn] = (r["ru"], gender_label(r.get("gender", "")), r.get("note", ""))
    return rules, only


def build(lo=None, hi=None):
    rules, only = load_rules()
    chapters = {}
    for n in translated_chapters(lo, hi):
        al = aligned(n)
        if al:
            chapters[n] = (al[0], al[1], "\n".join(al[0]))
    spots = {}
    # Длинные ключи раньше: «Yamasaka Iori» закрывает «Iori» в том же абзаце.
    for cn in sorted(rules, key=len, reverse=True):
        ru, gender, note = rules[cn]
        rx, crx = source_key_rx(cn), canon_rx(ru)
        if crx is None:
            continue
        for n, (src, tr, joined) in chapters.items():
            if (cn in only and n not in only[cn]) or not rx.search(joined):
                continue
            for i, (ps, pr) in enumerate(zip(src, tr)):
                if rx.search(ps) and not crx.search(pr):
                    have = spots.setdefault((n, i), [])
                    if not any(cn in other for other, *_ in have):
                        have.append((cn, ru, gender, note))

    os.makedirs(ALIGN_DIR, exist_ok=True)
    for old in glob.glob(os.path.join(ALIGN_DIR, "fix_*")):
        os.remove(old)
    # Глава целиком в одном задании: два агента не правят один файл.
    by_ch = {}
    for n, i in sorted(spots):
        by_ch.setdefault(n, []).append(i)
    tasks, cur = [], []
    for n, idx in sorted(by_ch.items()):
        if cur and sum(len(x[1]) for x in cur) + len(idx) > PER_TASK:
            tasks.append(cur)
            cur = []
        cur.append((n, idx))
    if cur:
        tasks.append(cur)
    plan = {}
    for t, chunk in enumerate(tasks, 1):
        nn = "%02d" % t
        out = ["# Имена к канону: пакет %s" % nn, "",
               "Отчёт запиши в `%s`." % rel(os.path.join(ALIGN_DIR, "fixed_%s.json" % nn)), ""]
        for n, idx in chunk:
            src, tr, _ = chapters[n]
            for i in idx:
                out.append("## гл.%d ¶%d — файл %s" % (n, i + 1, rel(ru_path(n))))
                for cn, ru, gender, note in spots[(n, i)]:
                    out.append("- «%s» должно быть **%s**%s%s" % (cn, ru, " (род: %s)" % gender if gender else "",
                                                                " — %s" % note if note else ""))
                out.append("Оригинал: %s" % src[i])
                out.append("Перевод: %s" % tr[i])
                out.append("")
        open(os.path.join(ALIGN_DIR, "fix_%s.md" % nn), "w", encoding="utf-8").write("\n".join(out) + "\n")
        plan[nn] = {str(n): [i + 1 for i in idx] for n, idx in chunk}
    save_json(PLAN_FILE, plan)
    return len(spots), len(by_ch), len(tasks)


def word_diff(a, b):
    wa, wb = a.split(" "), b.split(" ")
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, wa, wb, autojunk=False).get_opcodes():
        if op != "equal":
            out.append("«%s» → «%s»" % (" ".join(wa[max(0, i1 - 1):i2 + 1]), " ".join(wb[max(0, j1 - 1):j2 + 1])))
    return "; ".join(out)


def verify(nn, chapters, before):
    """Поглавная приёмка после агента. Возвращает (принято глав, откачено, строки замен)."""
    kept = rolled = 0
    lines = []
    for key, allowed in chapters.items():
        n = int(key)
        snap, now = before[n], read_text(ru_path(n))
        a, b = split_paragraphs(snap), split_paragraphs(now)
        why = None
        if len(a) != len(b):
            why = "число абзацев %d → %d" % (len(a), len(b))
        else:
            extra = [i for i, (x, y) in enumerate(zip(a, b), 1) if x != y and i not in allowed]
            if extra:
                why = "правка вне заданных абзацев: %s" % extra[:5]
            else:
                src = read_text(chapter_path(n))
                problems = validate_translation(src, now)
                if problems and not validate_translation(src, snap):
                    why = "приёмка: %s" % problems[0]
        if why:
            write_text(ru_path(n), snap)
            rolled += 1
            lines.append("гл.%d ОТКАТ (пакет %s) — %s" % (n, nn, why))
            continue
        kept += 1
        for i in allowed:
            if a[i - 1] != b[i - 1]:
                lines.append("гл.%d ¶%d: %s" % (n, i, word_diff(a[i - 1], b[i - 1])))
    return kept, rolled, lines


def run_task(nn, chapters, runner):
    before = {int(k): read_text(ru_path(int(k))) for k in chapters}
    snap_dir = os.path.join(ALIGN_DIR, "snapshot")
    os.makedirs(snap_dir, exist_ok=True)
    for n in before:
        dst = os.path.join(snap_dir, os.path.basename(ru_path(n)))
        if not os.path.exists(dst):              # самый ранний снимок — для ручного отката
            shutil.copy(ru_path(n), dst)
    ok, report = runner("Файл задания: %s" % rel(os.path.join(ALIGN_DIR, "fix_%s.md" % nn)), AGENT, "align_%s" % nn)
    kept, rolled, lines = verify(nn, chapters, before)
    with open(REPLACEMENTS, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + ("\n" if lines else ""))
    open(os.path.join(ALIGN_DIR, "fix_%s.done" % nn), "w").write(time.strftime("%Y-%m-%d %H:%M:%S"))
    return nn, ok, (report or "").replace("\n", " ")[:160], kept, rolled, sum(1 for x in lines if "¶" in x)


def run(runner, parallel=1):
    plan = load_json(PLAN_FILE, {}) or {}
    todo = [(nn, ch) for nn, ch in sorted(plan.items())
            if not os.path.exists(os.path.join(ALIGN_DIR, "fix_%s.done" % nn))]
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
        for res in pool.map(lambda t: run_task(t[0], t[1], runner), todo):
            yield res


def _runner(model, effort):
    try:
        import autorun
        return lambda prompt, agent, tag: autorun.run_claude(prompt, agent, tag=tag, model=model, effort=effort)
    except Exception:  # noqa: BLE001
        import llm

        def plain(prompt, agent, tag):
            proc = llm.run_agent(prompt, agent, model)
            return proc.returncode == 0, proc.stdout
        return plain


def main():
    ap = argparse.ArgumentParser(description="Имена к канону в готовом переводе")
    ap.add_argument("cmd", choices=("build", "run", "status"))
    ap.add_argument("--range", type=int, nargs=2, metavar=("LO", "HI"))
    ap.add_argument("--model", default="sonnet", help="модель агента namefix")
    ap.add_argument("--effort", choices=("low", "medium", "high", "xhigh", "max"))
    ap.add_argument("--parallel", type=int, default=1, help="сколько заданий сразу")
    args = ap.parse_args()

    if args.cmd == "build":
        lo, hi = args.range if args.range else (None, None)
        spots, chapters, tasks = build(lo, hi)
        print("Не по канону: %d абзацев в %d главах → заданий %d (%s)" % (spots, chapters, tasks, rel(ALIGN_DIR)))
        if tasks:
            print("Дальше: python term_align.py run --model <модель> --parallel 4")
    elif args.cmd == "run":
        total = rolled = 0
        for nn, ok, report, kept, back, changed in run(_runner(args.model, args.effort), args.parallel):
            total += changed
            rolled += back
            print("пакет %s: %s | исправлено абзацев %d, откатов глав %d" % (nn, report if ok else "агент не справился", changed, back))
        print("Итого исправлено абзацев: %d, откатов глав: %d. Вычитка: %s" % (total, rolled, rel(REPLACEMENTS)))
    else:
        plan = load_json(PLAN_FILE, {}) or {}
        done = [nn for nn in plan if os.path.exists(os.path.join(ALIGN_DIR, "fix_%s.done" % nn))]
        print("Заданий: %d, выполнено: %d" % (len(plan), len(done)))


if __name__ == "__main__":
    main()
