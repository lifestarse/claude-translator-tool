#!/usr/bin/env python3
"""Prepare arbitration tasks for the `arbiter` agent, and apply its decisions.

glossary_merge.py refuses to resolve two things by itself, and it is right to:

    conflicts     one Chinese term, different Russian renderings from different
                  judges -- picking one at random is how terminology drifts
    ru_collisions two different Chinese terms under one Russian name -- the
                  reader sees two creatures as one

Both need to look at how the book actually uses the terms, which the merge step
cannot do. This script gathers that evidence -- occurrence counts, how many of
those chapters are already translated, and real sentences from the source -- and
hands it to an agent that decides.

    python build_arbitration.py           # build the task from glossary_v2.json
    python build_arbitration.py --apply   # fold decisions into glossary_overrides.json

Decisions land in glossary_overrides.json rather than glossary_v2.json because
the merge regenerates v2 from scratch every run; an edit written there is gone
at the next merge. The overrides file is applied on top, every time.
"""

import argparse
import os
import re
import shutil
import sys

from common import (
    ROOT,
    chapter_path,
    load_json,
    read_text,
    ru_path,
    save_json,
    write_text,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BATCH_DIR = os.path.join(ROOT, "glossary_batches")
JUDGED_DIR = os.path.join(ROOT, "glossary_judged")
GLOSSARY_V2 = os.path.join(ROOT, "glossary_v2.json")
OVERRIDES_FILE = os.path.join(ROOT, "glossary_overrides.json")
STYLE_REF = "glossary_style_reference.md"

QUOTES_PER_TERM = 3
QUOTE_PAD = 26          # characters of context on each side
MAX_CHAPTERS_SCANNED = 4885

SENT_SPLIT_RE = re.compile(r"(?<=[。！？])")


def disputes():
    """(conflicts, collisions) straight out of glossary_v2.json."""
    v2 = load_json(GLOSSARY_V2) or {}
    return v2.get("conflicts") or [], v2.get("ru_collisions") or [], v2.get("glossary") or []


def gather_context(cns):
    """One pass over the corpus: counts, translated-chapter counts, quotes.

    Deliberately a single scan for all disputed terms at once. Per-term scans
    of 4885 files is the kind of thing that turns a rare operation into one
    nobody runs.
    """
    stats = {cn: {"chapters": [], "mentions": 0, "quotes": []} for cn in cns}
    for idx in range(1, MAX_CHAPTERS_SCANNED + 1):
        path = chapter_path(idx)
        if not os.path.exists(path):
            continue
        try:
            text = read_text(path)
        except OSError:
            continue
        for cn in cns:
            n = text.count(cn)
            if not n:
                continue
            s = stats[cn]
            s["mentions"] += n
            s["chapters"].append(idx)
            if len(s["quotes"]) < QUOTES_PER_TERM:
                k = text.find(cn)
                frag = text[max(0, k - QUOTE_PAD): k + len(cn) + QUOTE_PAD]
                s["quotes"].append((idx, frag.replace("\n", " ").strip()))
    for cn, s in stats.items():
        s["translated"] = sum(1 for i in s["chapters"] if os.path.exists(ru_path(i)))
    return stats


def term_line(cn, entry, stats):
    s = stats.get(cn, {})
    bits = [
        "**%s**" % cn,
        "вхождений: %d в %d главах" % (s.get("mentions", 0), len(s.get("chapters", []))),
        "из них переведено: %d" % s.get("translated", 0),
    ]
    if entry:
        if entry.get("category"):
            bits.append("категория: %s" % entry["category"])
        if entry.get("description"):
            bits.append(entry["description"])
    return "- " + " | ".join(bits)


def quotes_block(cn, stats):
    qs = stats.get(cn, {}).get("quotes", [])
    if not qs:
        return "    _цитат нет — термин в скачанных главах не найден_"
    return "\n".join("    гл.%04d: …%s…" % (i, q) for i, q in qs)


def build(conflicts, collisions, glossary):
    by_cn = {e["cn"]: e for e in glossary}
    cns = sorted(
        {c["cn"] for c in conflicts}
        | {t["cn"] for c in collisions for t in c["terms"]}
    )
    print("[+] Спорных записей: конфликтов %d, коллизий %d, терминов под разбор %d"
          % (len(conflicts), len(collisions), len(cns)))
    if not cns:
        print("[+] Разводить нечего — глоссарий чист.")
        return None

    print("[+] Собираю контекст из глав (один проход по корпусу)…")
    stats = gather_context(cns)

    taken = sorted({e["ru"] for e in glossary if e.get("ru")})
    lines = [
        "# Арбитраж глоссария",
        "",
        "Записать решения в: `glossary_judged/arbitration_001.json`",
        "",
        "Образец стиля принятых терминов: `%s` — прочитай его первым." % STYLE_REF,
        "",
    ]

    if collisions:
        lines += [
            "## Коллизии — разные китайские термины под одним русским именем",
            "",
            "По каждой: одно это существо под двумя названиями (`shared`) или",
            "два разных (`rename`)? Ответ ищи в цитатах.",
            "",
        ]
        for n, c in enumerate(collisions, 1):
            lines.append("### %d. «%s»" % (n, c["ru"]))
            lines.append("")
            for t in c["terms"]:
                lines.append(term_line(t["cn"], by_cn.get(t["cn"]), stats))
                lines.append("")
                lines.append(quotes_block(t["cn"], stats))
                lines.append("")

    if conflicts:
        lines += [
            "## Конфликты — один термин, разные варианты у судей",
            "",
            "Выбери один вариант (`pick`). Можно дать свой, если оба плохи.",
            "",
        ]
        for n, c in enumerate(conflicts, 1):
            lines.append("### %d. %s" % (n, c["cn"]))
            lines.append("")
            lines.append(term_line(c["cn"], by_cn.get(c["cn"]), stats))
            lines.append("")
            lines.append("    Варианты судей: %s"
                         % ", ".join("«%s» (%s)" % (v["ru"], v.get("gender") or "род не указан")
                                     for v in c["variants"]))
            lines.append("")
            lines.append(quotes_block(c["cn"], stats))
            lines.append("")

    lines += [
        "## Уже выданные русские названия",
        "",
        "Новое имя не должно совпасть ни с одним из них — иначе ты разведёшь",
        "одну коллизию и создашь другую.",
        "",
        ", ".join(taken),
        "",
    ]

    os.makedirs(BATCH_DIR, exist_ok=True)
    os.makedirs(JUDGED_DIR, exist_ok=True)
    path = os.path.join(BATCH_DIR, "arbitrate_001.md")
    write_text(path, "\n".join(lines))
    print("[+] Задание собрано: %s (%d символов)"
          % (os.path.relpath(path, ROOT), sum(len(x) for x in lines)))
    print("")
    print("Дальше: агент типа arbiter с промптом вида")
    print('    "Файл задания: glossary_batches/arbitrate_001.md"')
    print("Затем: python build_arbitration.py --apply")
    return path


# --------------------------------------------------------------------------
# Applying decisions
# --------------------------------------------------------------------------

def load_decisions():
    out, problems = [], []
    if not os.path.isdir(JUDGED_DIR):
        return out, ["каталога glossary_judged/ нет"]
    for name in sorted(os.listdir(JUDGED_DIR)):
        if not name.startswith("arbitration_") or not name.endswith(".json"):
            continue
        data = load_json(os.path.join(JUDGED_DIR, name))
        items = data.get("decisions") if isinstance(data, dict) else data
        if not isinstance(items, list):
            problems.append("%s: ожидался список decisions" % name)
            continue
        for d in items:
            if isinstance(d, dict) and d.get("action"):
                d["_file"] = name
                out.append(d)
            else:
                problems.append("%s: запись без action" % name)
    return out, problems


def apply_decisions(force):
    decisions, problems = load_decisions()
    for p in problems:
        print("[!] %s" % p)
    if not decisions:
        print("Решений нет — сначала запусти агента arbiter.")
        raise SystemExit(1)

    ov = load_json(OVERRIDES_FILE, {}) or {}
    ov.setdefault("drop", {})
    ov.setdefault("rename", {})
    ov.setdefault("add", [])
    ov.setdefault("allow_shared_ru", {})
    add_by_cn = {a["cn"]: a for a in ov["add"] if isinstance(a, dict) and a.get("cn")}

    # The overrides file holds hand-made decisions accumulated over the whole
    # project. An agent writing into it must not be able to quietly replace one.
    applied, skipped = [], []

    def occupied(cn):
        return cn in ov["drop"] or cn in ov["rename"] or cn in add_by_cn

    for d in decisions:
        action, cn, ru = d.get("action"), d.get("cn"), d.get("ru")
        why = d.get("why", "решение арбитра")
        if action == "shared":
            terms = d.get("terms") or []
            if not ru or len(terms) < 2:
                skipped.append(("shared", ru or "?", "нужны ru и минимум два terms"))
                continue
            if ru in ov["allow_shared_ru"] and not force:
                skipped.append(("shared", ru, "запись уже есть, нужен --force"))
                continue
            ov["allow_shared_ru"][ru] = {"terms": terms, "why": why}
            applied.append(("shared", ru, ", ".join(terms)))
        elif action == "rename":
            if not cn or not ru:
                skipped.append(("rename", cn or "?", "нужны cn и ru"))
                continue
            if occupied(cn) and not force:
                skipped.append(("rename", cn, "решение по термину уже есть, нужен --force"))
                continue
            ov["rename"][cn] = {"ru": ru, "why": why}
            applied.append(("rename", cn, ru))
        elif action == "drop":
            if not cn:
                skipped.append(("drop", "?", "нужен cn"))
                continue
            if occupied(cn) and not force:
                skipped.append(("drop", cn, "решение по термину уже есть, нужен --force"))
                continue
            ov["drop"][cn] = why
            applied.append(("drop", cn, ""))
        elif action == "pick":
            if not cn or not ru:
                skipped.append(("pick", cn or "?", "нужны cn и ru"))
                continue
            if occupied(cn) and not force:
                skipped.append(("pick", cn, "решение по термину уже есть, нужен --force"))
                continue
            entry = {"cn": cn, "ru": ru, "gender": d.get("gender", ""),
                     "category": d.get("category", ""),
                     "description": d.get("description", ""), "source": "arbiter"}
            add_by_cn[cn] = entry
            ov["add"] = [a for a in ov["add"] if a.get("cn") != cn] + [entry]
            applied.append(("pick", cn, ru))
        else:
            skipped.append((action or "?", cn or d.get("ru") or "?", "неизвестное действие"))

    if applied:
        backup = OVERRIDES_FILE + ".bak"
        shutil.copyfile(OVERRIDES_FILE, backup)
        save_json(OVERRIDES_FILE, ov)
        print("[+] Копия прежнего файла: %s" % os.path.basename(backup))

    print("=== АРБИТРАЖ ===")
    print("Решений прочитано : %d" % len(decisions))
    print("Применено         : %d" % len(applied))
    for act, key, val in applied:
        print("    %-8s %-10s %s" % (act, key, val))
    if skipped:
        print("Пропущено         : %d" % len(skipped))
        for act, key, why in skipped:
            print("    %-8s %-10s %s" % (act, key, why))
    print("")
    print("Дальше: python glossary_merge.py")
    raise SystemExit(1 if skipped else 0)


def main():
    ap = argparse.ArgumentParser(description="Арбитраж спорных записей глоссария")
    ap.add_argument("--apply", action="store_true", help="применить решения агента")
    ap.add_argument("--force", action="store_true", help="перебивать уже принятые решения")
    args = ap.parse_args()

    if args.apply:
        apply_decisions(args.force)
        return
    conflicts, collisions, glossary = disputes()
    build(conflicts, collisions, glossary)


if __name__ == "__main__":
    main()
