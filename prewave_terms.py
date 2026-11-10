#!/usr/bin/env python3
"""Fix the terminology of a chapter range BEFORE translating it.

Wave 1 established the failure this exists to prevent. Translators run in
parallel and cannot see each other, so a name the glossary does not cover gets
invented independently by every agent that meets it: 白膏 came back as four
different words, 光菌 as three. Re-translating does not converge -- it produces
a fifth variant, because the second agent is no better informed than the first.
Accumulating terms after a wave is therefore always one wave too late.

The signal is that this author announces his coinages. Every divergent term
sampled from wave 1 was introduced by a naming construction: 琳給這群發光細胞
起了個名字叫光菌, 取名為‘培育者’, 稱之為‘巨牙’. So the range can be mined for
its own vocabulary before a single translator is dispatched.

Quoted strings alone are too noisy -- this author also quotes for emphasis, and
a 30-chapter range yields ~100 quoted fragments against ~45 named ones, the
surplus being words like 免費 "free" and 前輩 "senior".

    python prewave_terms.py --range 40 69
    # then one judge agent on the emitted task file, then glossary_merge.py

Output: glossary_batches/wave_LO_HI.json and a judge task file next to it.
Verdicts go to glossary_judged/, where glossary_merge.py already looks.
"""

import argparse
import collections
import os
import sys

from lang import LANG
from common import (
    CHAPTERS_DIR,
    ROOT,
    chapter_path,
    load_json,
    read_text,
    save_json,
    write_text,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BATCH_DIR = os.path.join(ROOT, "glossary_batches")
JUDGED_DIR = os.path.join(ROOT, "glossary_judged")
GLOSSARY_V2 = os.path.join(ROOT, "glossary_v2.json")
ACCUMULATED = os.path.join(ROOT, "glossary_accumulated.json")
STYLE_REF = "glossary_style_reference.md"

# Конструкции именования («назвали X», 叫做X, 'X'라고 불렀다) и стоп-слова
# зависят от языка оригинала и живут в lang.py. Захват в кавычках точнее:
# кавычки дают границы; без них захват нарочно короче.
NAMING_RES = LANG.naming_res()

CONTEXT = 60
MAX_CONTEXTS = 2


def load_known():
    known = set()
    for path in (GLOSSARY_V2, ACCUMULATED):
        data = load_json(path, {}) or {}
        for e in data.get("glossary", []):
            known.add(e["cn"])
    return known


def taken_names():
    data = load_json(GLOSSARY_V2, {}) or {}
    return sorted({e["ru"] for e in data.get("glossary", []) if e.get("ru")})


def clean(term):
    """Обрезать захват до имени или отвергнуть — правила в lang.py."""
    return LANG.clean_term(term)


def harvest(lo, hi):
    """Candidates named inside [lo, hi], with where and how they were named."""
    found = collections.OrderedDict()
    for idx in range(lo, hi + 1):
        path = chapter_path(idx)
        if not os.path.exists(path):
            continue
        text = read_text(path)
        for rx, quoted in NAMING_RES:
            for m in rx.finditer(text):
                term = clean(m.group(1))
                if not term:
                    continue
                rec = found.setdefault(term, {
                    "cn": term, "quoted": False, "chapters": [], "contexts": []
                })
                rec["quoted"] = rec["quoted"] or quoted
                if idx not in rec["chapters"]:
                    rec["chapters"].append(idx)
                if len(rec["contexts"]) < MAX_CONTEXTS:
                    s = max(0, m.start() - CONTEXT)
                    e = min(len(text), m.end() + CONTEXT)
                    rec["contexts"].append(
                        "гл.%d: …%s…" % (idx, text[s:e].replace("\n", " ").strip())
                    )
    return found


def corpus_docfreq(terms):
    """How many chapters in the whole book use each term.

    A coinage introduced here and used three arcs later is exactly what has to
    be pinned now; a one-off that never returns is not worth a glossary line.
    """
    df = collections.Counter()
    for name in sorted(os.listdir(CHAPTERS_DIR)):
        if not (name.startswith("chapter_") and name.endswith(".txt")):
            continue
        text = read_text(os.path.join(CHAPTERS_DIR, name))
        for t in terms:
            if t in text:
                df[t] += 1
    return df


JUDGE_TEMPLATE = """# Задание: вынести вердикт по терминам глав {lo}–{hi}

Это термины, которые книга **вводит сама** конструкциями именования
(«назвали X», «по имени X», «известный как X») в главах {lo}–{hi}. Их надо перевести
до того, как главы уйдут переводчикам, иначе каждый переводчик придумает
своё название и терминология разъедется.

## Что сделать

1. Прочитай `{style_ref}` — там правила и принятые решения, держись их манеры.
2. Прочитай `{batch_file}` — список кандидатов с контекстами.
3. По каждому вынеси вердикт.
4. Запиши результат в `{out_file}` в формате:

```json
{{"verdicts": [
  {{"cn": "光導爆彈", "accept": true, "ru": "Светонаводящаяся бомба",
    "gender": "Она / Её", "category": "Оружие", "description": "…"}},
  {{"cn": "光合作用", "accept": false, "reason": "обычная лексика: фотосинтез"}}
]}}
```

Верни ОДНУ строку отчёта: `волна {lo}-{hi} | принято N | отклонено M`.

## Правила

**Отклоняй обычную лексику.** Конструкция именования ловит не только коинажи:
`光合作用` «фотосинтез», `共生` «симбиоз», `壓力` «давление`, `森林` «лес» —
это словарные слова, автор просто вводит понятие для героя, у которого нет
языка. Такому в глоссарии не место: переводчик и сам знает эти слова, а лишняя
запись только раздувает задание. Признак коинажа — у слова **нет** готового
русского соответствия, и переводчики разойдутся в вариантах.

**Частота — обратный признак.** У каждого кандидата указано, в скольких главах
книги он встречается (`docfreq`, `spread`). Чем выше это число, тем вероятнее,
что перед тобой обычное слово: по-настоящему редкие имена собственные сидят в
своих главах и отсутствуют в остальных. Кандидат из 300 глав почти наверняка
словарный, кандидат из 5 — почти наверняка коинаж. Это подсказка, а не правило:
решает смысл.

**Род обязателен** для принятых — по нему согласуются прилагательные и глаголы.

**Имя должно быть коротким.** Термин встретится сотни раз; подстрочник вроде
«Щупальце глазного яблока» в прозе не читается. Один-два слова.

**Не занимай уже занятые имена.** Ниже список русских названий, которые уже
раздали другим терминам. Если твой кандидат просится в одно из них — придумай
другое, иначе читатель увидит два разных существа под одним именем.

{taken}
"""


def main():
    ap = argparse.ArgumentParser(description="Фиксация терминов диапазона до перевода")
    ap.add_argument("--range", type=int, nargs=2, required=True, metavar=("LO", "HI"))
    ap.add_argument("--min-docfreq", type=int, default=2)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    lo, hi = args.range
    known = load_known()
    print("[+] В глоссарии уже: %d терминов" % len(known))

    found = harvest(lo, hi)
    print("[+] Конструкций именования в главах %d–%d: %d" % (lo, hi, len(found)))

    fresh = {k: v for k, v in found.items() if k not in known}
    print("    из них новых: %d" % len(fresh))
    if not fresh:
        print("Новых терминов в диапазоне нет — можно переводить как есть.")
        return

    print("[+] Считаю встречаемость по всему корпусу...", flush=True)
    df = corpus_docfreq(set(fresh))

    total = len([n for n in os.listdir(CHAPTERS_DIR)
                 if n.startswith("chapter_") and n.endswith(".txt")])
    cands, thin = [], []
    for term, rec in fresh.items():
        rec["docfreq"] = df.get(term, 0)
        rec["spread"] = "%.1f%% глав книги" % (100.0 * rec["docfreq"] / max(total, 1))
        if rec["docfreq"] < args.min_docfreq:
            thin.append(rec)
            continue
        cands.append(rec)
    # Ascending: the rare end is where the coinages are, and the judge reads
    # top-down. Sorting by frequency would put 激動 "excitement" first.
    cands.sort(key=lambda r: (r["docfreq"], r["cn"]))

    print("    к суду: %d, отсеяно как разовые: %d" % (len(cands), len(thin)))
    if not cands:
        print("Ничего не набралось.")
        return

    print("")
    print("Кандидаты (глав в книге / названо в главах):")
    for r in cands[:30]:
        print("   %-10s %4d  гл. %s%s" % (
            r["cn"], r["docfreq"],
            ", ".join(str(c) for c in r["chapters"][:3]),
            "" if r["quoted"] else "   [без кавычек]",
        ))
    if len(cands) > 30:
        print("   … ещё %d" % (len(cands) - 30))

    if args.dry_run:
        print("")
        print("(пробный прогон, файлы не собраны)")
        return

    os.makedirs(BATCH_DIR, exist_ok=True)
    os.makedirs(JUDGED_DIR, exist_ok=True)
    tag = "wave_%04d_%04d" % (lo, hi)
    batch_file = os.path.join(BATCH_DIR, tag + ".json")
    save_json(batch_file, {
        "range": [lo, hi],
        "note": "Термины, которые книга вводит конструкциями именования в этом диапазоне.",
        "candidates": cands,
    })

    taken = taken_names()
    task_file = os.path.join(BATCH_DIR, "judge_" + tag + ".md")
    write_text(task_file, JUDGE_TEMPLATE.format(
        lo=lo, hi=hi,
        style_ref=STYLE_REF,
        batch_file="glossary_batches/%s.json" % tag,
        out_file="glossary_judged/%s.json" % tag,
        taken="\n".join("- " + n for n in taken),
    ))

    print("")
    print("Собрано:")
    print("    %s  (%d кандидатов)" % (batch_file, len(cands)))
    print("    %s  <- задание судье" % task_file)
    print("")
    print("Дальше: агент на judge_%s.md, затем glossary_merge.py, затем build_batches.py" % tag)


if __name__ == "__main__":
    main()
