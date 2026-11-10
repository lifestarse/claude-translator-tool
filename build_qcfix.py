#!/usr/bin/env python3
"""Build pinpoint repair tasks for defects a term replacement cannot express.

termfix.py covers one shape of damage: an obsolete SPELLING of a term, anywhere
in a chapter. Its task file has no field for a paragraph number, and its agent
is forbidden to rewrite sentences -- and that narrowness is exactly what makes
it safe to run across twelve chapters at once. Two kinds of defect fall outside
it and must not be forced through it:

  * the fix belongs to NAMED paragraphs and nowhere else. A corpus-wide rule on
    "снаряд" would touch 99 chapters, 77 of which never contained 彈頭; a rule
    on "нити" would rewrite seams, webs and muscles across the book.
  * the fix is a rewrite -- a lost comparison to restore, a predicate to move
    with its subject, a calque to unwind.

There is a third case that decides the shape of this script. In chapter 7 the
same phenomenon is named three ways, and paragraphs 50 and 63 must be repaired
while paragraph 58 must NOT be: the word that looks identical there translates
a different Chinese word and is already correct. A mechanism that cannot say
"these paragraphs, not that one" is the wrong mechanism, so the unit here is a
paragraph list plus an explicit untouchable list, not a chapter.

Input is qcfix_tasks.json -- a curated decision, checked against the corpus --
rather than qc_verdicts/ directly. A verdict names one paragraph when the defect
runs through five, and it is evidence, not an order. Every quote in that file is
verified against the actual paragraph here; a task is never written on a guess.

    python build_qcfix.py --check
    python build_qcfix.py --only 7 190 191 258 283 288 620
"""

import argparse
import os
import sys

from common import (
    ROOT,
    chapter_path,
    load_json,
    read_text,
    ru_path,
    save_json,
    split_paragraphs,
    write_text,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

TASKS_FILE = os.path.join(ROOT, "qcfix_tasks.json")
FIX_DIR = os.path.join(ROOT, "fixes")
GLOSSARY_V2 = os.path.join(ROOT, "glossary_v2.json")

KIND_RU = {
    "glossary": "термин не по канону",
    "calque": "подстрочник",
    "omission": "выхолащивание",
    "invention": "отсебятина",
}

TEMPLATE = """# Точечная починка: глава {idx:04d}

Файл главы: `{path}`

В главе **{paragraphs} абзацев** — их число меняться не должно.

## Править разрешено ТОЛЬКО эти абзацы

{allowed}

Любой абзац, не названный в этом списке, обязан остаться байт в байт таким,
как он сейчас лежит на диске.{forbidden}

{blocks}
## Как отвечать

Одной строкой: `{idx:04d} ok | абзацев правлено N` либо `{idx:04d} отказ | <почему>`.
"""

BLOCK = """---

### Абзац {n}  (дефект: {kind})

CN: {cn}

RU: {ru}

**Что не так:** {why}
{canon}{keep_out}
"""


def load_terms():
    v2 = load_json(GLOSSARY_V2) or {}
    return {e["cn"]: e for e in v2.get("glossary", [])}


def build_chapter(task, terms, problems):
    """Render one task file, or record why it cannot be built. Returns str|None.

    Every failure here is fatal on purpose. A paragraph number that quietly
    slipped by one sends an agent to rewrite somebody else's text, and the
    result passes verify.py without a murmur -- paragraph counts and volume
    would both still be right.
    """
    idx = task["chapter"]
    src_file, ru_file = chapter_path(idx), ru_path(idx)
    if not os.path.exists(ru_file):
        problems.append("гл.%04d: нет ru/chapter_%04d.txt" % (idx, idx))
        return None
    if not os.path.exists(src_file):
        problems.append("гл.%04d: нет chapters/chapter_%04d.txt" % (idx, idx))
        return None

    src_p = split_paragraphs(read_text(src_file))
    ru_p = split_paragraphs(read_text(ru_file))
    if len(src_p) != len(ru_p):
        problems.append("гл.%04d: абзацев в оригинале %d, в переводе %d — "
                        "номера не совпадают, задание собирать нельзя"
                        % (idx, len(src_p), len(ru_p)))
        return None

    allowed, forbidden, blocks = [], [], []
    for d in task["defects"]:
        paras = d["paras"]
        for n in paras:
            if not (1 <= n <= len(ru_p)):
                problems.append("гл.%04d: абзац %d вне диапазона 1..%d"
                                % (idx, n, len(ru_p)))
                return None
            # The quote is the load-bearing check: it proves this paragraph
            # number still points at the text the decision was made about.
            quote = (d.get("quotes") or {}).get(str(n))
            if not quote:
                problems.append("гл.%04d: для абзаца %d не задана цитата — "
                                "проверить попадание номера нечем" % (idx, n))
                return None
            if quote not in ru_p[n - 1]:
                problems.append(
                    "гл.%04d: цитата «%s» НЕ найдена в абзаце %d. Либо номер "
                    "уехал, либо главу уже правили. Задание не собрано."
                    % (idx, quote, n))
                return None
            allowed.append(n)
            blocks.append(BLOCK.format(
                n=n,
                kind=KIND_RU.get(d["kind"], d["kind"]),
                cn=src_p[n - 1],
                ru=ru_p[n - 1],
                why=d["why"],
                canon=canon_line(d, terms),
                keep_out=keep_out_line(d),
            ))
        for n in d.get("keep_out") or []:
            if not (1 <= n <= len(ru_p)):
                problems.append("гл.%04d: запретный абзац %d вне диапазона"
                                % (idx, n))
                return None
            forbidden.append((n, d.get("keep_out_why", "")))

    clash = sorted(set(allowed) & {n for n, _ in forbidden})
    if clash:
        problems.append("гл.%04d: абзацы %s одновременно разрешены и запрещены"
                        % (idx, clash))
        return None

    forbidden_text = ""
    if forbidden:
        forbidden_text = "\n\n## Эти абзацы НЕ трогать, хотя они выглядят похоже\n"
        for n, why in sorted(set(forbidden)):
            forbidden_text += "\n- **Абзац %d.** %s\n" % (n, why)
        forbidden_text += (
            "\nЭто не перестраховка: там уже правильный текст, и правка его "
            "испортит.\n")

    return TEMPLATE.format(
        idx=idx,
        path="ru/chapter_%04d.txt" % idx,
        paragraphs=len(ru_p),
        allowed="\n".join("- Абзац **%d**" % n for n in sorted(set(allowed))),
        forbidden=forbidden_text,
        blocks="\n".join(blocks),
    )


def canon_line(d, terms):
    if not d.get("canon"):
        return ""
    t = terms.get(d.get("cn") or "", {})
    bits = "**Канон:** %s = %s" % (d.get("cn") or "?", d["canon"])
    if t.get("gender"):
        bits += " | род: %s" % t["gender"]
    return bits + ("\nКанон дан в именительном падеже — ставь в том падеже и "
                   "числе, которых требует предложение.\n")


def keep_out_line(d):
    if not d.get("keep_out_why"):
        return ""
    return "\n**Границы правки:** %s\n" % d["keep_out_why"]


def main():
    ap = argparse.ArgumentParser(description="Сборка точечных заданий на починку")
    ap.add_argument("--only", type=int, nargs="*")
    ap.add_argument("--check", action="store_true",
                    help="только проверить согласованность, ничего не писать")
    ap.add_argument("--redo", action="store_true",
                    help="не пропускать задания, уже помеченные полем applied")
    args = ap.parse_args()

    spec = load_json(TASKS_FILE)
    if not spec:
        print("[!] Нет %s" % TASKS_FILE)
        raise SystemExit(1)

    terms = load_terms()
    tasks = spec["tasks"]

    # A spec entry describes a defect and quotes the text as it looked WHILE
    # broken. Once the repair has landed those quotes no longer match, and the
    # validation below would fail on exactly the chapters where everything went
    # right. `applied` records that the repair is done; the entry stays in the
    # file as the record of what was changed and why.
    if not args.redo:
        done = [t["chapter"] for t in tasks if t.get("applied")]
        tasks = [t for t in tasks if not t.get("applied")]
        if done:
            print("[i] Уже применено (поле applied), пропущено: %s"
                  % ", ".join("%04d" % i for i in done))
            print("    Перепроверить их всё равно: --redo (цитаты описывают "
                  "текст ДО починки, поэтому проверка на исправленной главе "
                  "обязана падать).")

    if args.only:
        keep = set(args.only)
        tasks = [t for t in tasks if t["chapter"] in keep]
        missing = keep - {t["chapter"] for t in tasks}
        already = missing & set(done if not args.redo else [])
        missing -= already
        if already:
            print("[!] Уже применены (applied), для повтора добавь --redo: %s"
                  % ", ".join(str(i) for i in sorted(already)))
        if missing:
            print("[!] В qcfix_tasks.json нет глав: %s"
                  % ", ".join(str(i) for i in sorted(missing)))
        if missing or (already and not tasks):
            raise SystemExit(1)

    problems, prepared, rendered = [], [], {}
    for task in tasks:
        text = build_chapter(task, terms, problems)
        if text is None:
            continue
        idx = task["chapter"]
        rendered[idx] = text
        paras = sorted({n for d in task["defects"] for n in d["paras"]})
        prepared.append({
            "index": idx,
            "fix_file": "fixes/qcfix_%04d.md" % idx,
            "paras": paras,
            "keep_out": sorted({n for d in task["defects"]
                                for n in (d.get("keep_out") or [])}),
            "kinds": sorted({d["kind"] for d in task["defects"]}),
        })

    if problems:
        print("[!] Задания НЕ собраны — сначала разобраться:")
        for p in problems:
            print("    - %s" % p)
        raise SystemExit(1)

    for p in prepared:
        note = ""
        if p["keep_out"]:
            note = "  (не трогать: %s)" % ", ".join(str(n) for n in p["keep_out"])
        print("    гл.%04d: абзацы %s%s"
              % (p["index"], ", ".join(str(n) for n in p["paras"]), note))

    if args.check:
        print("")
        print("[+] Проверка пройдена: %d глав, все цитаты найдены в названных "
              "абзацах. Ничего не записано." % len(prepared))
        return

    os.makedirs(FIX_DIR, exist_ok=True)
    for p in prepared:
        write_text(os.path.join(FIX_DIR, "qcfix_%04d.md" % p["index"]),
                   rendered[p["index"]])
    save_json(os.path.join(FIX_DIR, "qcfix_index.json"), prepared)

    print("")
    print("=== ТОЧЕЧНЫЕ ПОЧИНКИ ===")
    print("Заданий собрано: %d" % len(prepared))
    print("Задания в fixes/. На каждое — агент типа qcfix:")
    print('    "Файл задания: fixes/qcfix_0007.md"')
    print("Главам, которые получают и termfix, и qcfix (258, 283), сначала")
    print("termfix — оба агента переписывают файл целиком.")


if __name__ == "__main__":
    main()
