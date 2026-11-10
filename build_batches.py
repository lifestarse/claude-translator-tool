#!/usr/bin/env python3
"""Prepare self-contained translation task files for subagents.

Each task file holds the per-chapter material one agent needs: the glossary
terms that actually occur in that chapter, the expected paragraph count, and
the source text. The translation rules are NOT here -- they live in the
translator agent's system prompt, so they are not re-sent per chapter. The
orchestrator spawns an agent with a one-line prompt naming the task file.

That indirection is the point. If the source text went into the agent's spawn
prompt, it would also sit in the orchestrator's context -- 200 chapters of
Chinese would blow the orchestrator out long before the session budget ran out.
Putting it in a file keeps the orchestrator's cost per chapter at ~150 tokens.

    python build_batches.py --count 20        # next 20 untranslated chapters
    python build_batches.py --only 2 3 4
"""

import argparse
import os
import re
import sys

from common import (
    RU_DIR,
    ROOT,
    chapter_path,
    load_excluded,
    load_glossary,
    load_json,
    read_text,
    relevant_terms,
    ru_path,
    save_json,
    split_paragraphs,
    write_text,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BATCH_DIR = os.path.join(ROOT, "batches")
GLOSSARY_V2 = os.path.join(ROOT, "glossary_v2.json")
MANIFEST = os.path.join(ROOT, "manifest.json")

# The translation rules live in the translator agent's system prompt
# (.claude/agents/translator.md), not here. Repeating them in every task file
# would re-send ~1000 tokens on every turn of every agent, for 4885 chapters.
TEMPLATE = """# Задание: перевести главу {idx:04d}

Записать перевод в: `{out_path}`

В исходнике **{paragraphs} абзацев**. Каждый пронумерован маркером `[N]`.

**Сохрани маркеры в переводе.** Каждый абзац перевода начинается со своего
номера в квадратных скобках, тем же номером, что в оригинале. Один номер —
один абзац: не дели помеченный фрагмент на два и не сливай два в один,
даже если внутри есть смена мысли или реплики. Маркеры будут убраны
автоматически после проверки.

{glossary_block}
## ИСХОДНЫЙ ТЕКСТ

{source}
"""


def normalise(text):
    """Collapse the site's indent markers.

    Source paragraphs carry U+3000 ideographic spaces left over from the
    site's layout. Agents read them as paragraph boundaries and split the
    paragraph in two -- confirmed as the cause in 3 of 6 sampled splits.
    """
    return re.sub(r"[　\s]{2,}", " ", text).strip()


def load_terms():
    """glossary_v2.json if the agent-built glossary exists, else the curated 55."""
    v2 = load_json(GLOSSARY_V2)
    if v2 and v2.get("glossary"):
        return v2["glossary"], v2.get("pronoun_rules", {}), "glossary_v2.json"
    curated, _auto, rules = load_glossary()
    return curated, rules, "glossary.json (только 55 выверенных)"


def glossary_block(terms, pronoun_rules):
    if not terms:
        return "## Глоссарий\n\nДля этой главы совпадений нет — переводи имена по смыслу и держись одного варианта.\n"
    lines = ["## Глоссарий — термины, встречающиеся в ЭТОЙ главе", ""]
    for t in terms:
        bits = ["**%s** = %s" % (t["cn"], t["ru"])]
        if t.get("gender"):
            bits.append("род: %s" % t["gender"])
        if t.get("description"):
            bits.append(t["description"])
        lines.append("- " + " | ".join(bits))
    if pronoun_rules:
        lines += ["", "### Согласование в роде", ""]
        for name in sorted(pronoun_rules):
            rule = pronoun_rules[name]
            lines.append("- %s — %s" % (name, rule.get("ru_gender", "")))
    lines.append("")
    return "\n".join(lines)


def pending_chapters(limit):
    """Untranslated chapters in reading order (catalog position), minus exclusions."""
    manifest = load_json(MANIFEST)
    if manifest and manifest.get("entries"):
        order = [e["index"] for e in manifest["entries"] if e.get("downloaded")]
    else:
        order = sorted(
            int(f[8:-4])
            for f in os.listdir(os.path.join(ROOT, "chapters"))
            if f.startswith("chapter_") and f.endswith(".txt")
        )
    excluded = load_excluded()
    todo = [i for i in order if i not in excluded and not os.path.exists(ru_path(i))]
    return todo[:limit] if limit else todo


def main():
    ap = argparse.ArgumentParser(description="Сборка заданий на перевод для субагентов")
    ap.add_argument("--count", type=int, default=20)
    ap.add_argument("--only", type=int, nargs="*")
    ap.add_argument("--clean", action="store_true", help="очистить batches/ перед сборкой")
    args = ap.parse_args()

    terms, pronoun_rules, source_name = load_terms()
    print("[+] Глоссарий: %s, терминов %d" % (source_name, len(terms)))

    targets = sorted(args.only) if args.only else pending_chapters(args.count)
    if not targets:
        print("[+] Непереведённых глав не осталось.")
        return

    os.makedirs(BATCH_DIR, exist_ok=True)
    os.makedirs(RU_DIR, exist_ok=True)
    if args.clean:
        for old in os.listdir(BATCH_DIR):
            if old != ".gitkeep":
                os.remove(os.path.join(BATCH_DIR, old))

    # --only bypasses pending_chapters(), so re-check here. Otherwise an
    # explicit request quietly reintroduces a chapter whose file holds another
    # chapter's text.
    excluded = load_excluded()
    prepared = []
    for idx in targets:
        if idx in excluded:
            print("    гл.%04d — ИСКЛЮЧЕНА (%s), задание не собрано" % (idx, excluded[idx]))
            continue
        src_file = chapter_path(idx)
        if not os.path.exists(src_file):
            print("    гл.%04d — исходника нет, пропускаю" % idx)
            continue
        raw = read_text(src_file).strip()
        paras = [normalise(p) for p in split_paragraphs(raw)]
        paragraphs = len(paras)
        source = "\n\n".join("[%d] %s" % (n, p) for n, p in enumerate(paras, 1))
        hits = relevant_terms(terms, raw)

        task_path = os.path.join(BATCH_DIR, "task_%04d.md" % idx)
        write_text(
            task_path,
            TEMPLATE.format(
                idx=idx,
                out_path="ru/chapter_%04d.txt" % idx,
                paragraphs=paragraphs,
                glossary_block=glossary_block(hits, pronoun_rules),
                source=source,
            ),
        )
        prepared.append(
            {
                "index": idx,
                "task_file": "batches/task_%04d.md" % idx,
                "out_file": "ru/chapter_%04d.txt" % idx,
                "paragraphs": paragraphs,
                "src_chars": len(source),
                "terms": len(hits),
            }
        )

    save_json(os.path.join(BATCH_DIR, "index.json"), prepared)

    total_chars = sum(p["src_chars"] for p in prepared)
    print("[+] Подготовлено заданий: %d" % len(prepared))
    print("    главы: %s%s" % (
        ", ".join(str(p["index"]) for p in prepared[:12]),
        " ..." if len(prepared) > 12 else "",
    ))
    print("    исходников суммарно: %d символов, терминов в среднем %.1f на главу"
          % (total_chars, sum(p["terms"] for p in prepared) / max(len(prepared), 1)))
    print("")
    print("Дальше: на каждое задание запускается агент с промптом вида")
    print('    "Прочитай batches/task_0002.md и выполни задание целиком."')


if __name__ == "__main__":
    main()
