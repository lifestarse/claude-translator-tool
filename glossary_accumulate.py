#!/usr/bin/env python3
"""Fold terms discovered by translator agents back into the glossary.

Translators end their report with `новые термины: 中文=Русский, ...` -- proper
nouns they had to render themselves because the glossary did not cover them.
This catches a whole class the statistical extractor cannot see: compositional
unit names like 錐形細胞 / 基礎細胞 / 紫細胞, where the head 細胞 is far too
common to survive the dispersion filter and the specific compounds never clear
the frequency threshold.

The same reports also contain plain vocabulary (病毒 "virus", 感染 "infection").
Accepting those would refill the glossary with exactly the noise the rebuild
removed, so every proposal is re-tested against the corpus.

    # append agent report lines to new_terms_raw.txt, then:
    python glossary_accumulate.py --dry-run
    python glossary_accumulate.py
"""

import argparse
import collections
import os
import re
import sys

from lang import LANG
from common import (
    CHAPTERS_DIR,
    ROOT,
    load_json,
    read_text,
    save_json,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

RAW_FILE = os.path.join(ROOT, "new_terms_raw.txt")
ACCUMULATED_FILE = os.path.join(ROOT, "glossary_accumulated.json")
GLOSSARY_V2 = os.path.join(ROOT, "glossary_v2.json")

# The extractor's 0.25 ceiling is useless here. It works there because
# candidates have already passed freq/docfreq thresholds and fragment removal;
# applied to raw agent proposals it accepts 病毒 "virus" (4% of chapters) and
# 感染 "infection" (7%) -- topical in a book about evolving cells, but still
# ordinary nouns.
MAX_SPREAD = 0.03

# The load-bearing test is orthographic, not statistical. Agents are asked for
# proper nouns, and Russian capitalises them; every junk proposal in the pilot
# came back lowercase (вирус, заражение, странная клетка) and every good one
# capitalised (Хранитель, Слизь, Фиолетовая клетка). Writing it lowercase is
# the agent judging it a common noun, so take it at its word.
MIN_LEN = 2
PAIR_RE = re.compile(r"(" + LANG.term_pattern(2, 8) + r")\s*=\s*([^,;\n]+)")


CHAPTER_RE = re.compile(r"^\s*(\d{3,4})\s")


def parse_reports(text):
    """(глава, 中文, Русский) из строк отчётов.

    Номер главы нужен не для отчётности. При последовательном переводе он
    задаёт старшинство: кто назвал термин первым, тот его и назвал. Раньше
    порядок терялся, все варианты сваливались в одно множество, и термин,
    названный по-разному, объявлялся неразрешимым конфликтом.
    """
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line or "новые термины" not in line:
            continue
        m = CHAPTER_RE.match(line)
        idx = int(m.group(1)) if m else 10 ** 6
        tail = line.split("новые термины", 1)[1]
        if "нет" in tail[:8]:
            continue
        for cn, ru in PAIR_RE.findall(tail):
            # «Юй Шу Шу (Клеевар)» — скобка это пояснение агента, а не часть
            # имени. Без обрезки она попадала в глоссарий и оттуда в задания,
            # и следующий переводчик писал пояснение прямо в текст главы.
            ru = ru.split("(")[0].strip().rstrip(".")
            if ru:
                rows.append((idx, cn.strip(), ru))
    return rows


def corpus_spread(terms):
    """One pass over the corpus: how many chapters contain each term."""
    docfreq = collections.Counter()
    total = 0
    for name in sorted(os.listdir(CHAPTERS_DIR)):
        if not (name.startswith("chapter_") and name.endswith(".txt")):
            continue
        total += 1
        text = read_text(os.path.join(CHAPTERS_DIR, name))
        for t in terms:
            if t in text:
                docfreq[t] += 1
    return docfreq, total


def pick_canonical(ordered):
    """(канон, разошлось ли) для одного термина. `ordered` — [(глава, вариант)].

    Прежнее правило объявляло неразрешимым конфликтом любой термин, чьи
    варианты расходились сильнее регистра, и в глоссарий такой термин не
    попадал никогда. При последовательном переводе это захлопывающаяся
    ловушка: пока термина нет в глоссарии, его нет и в задании, поэтому
    следующая глава называет его заново — и расхождение, из-за которого он
    отвергнут, становится глубже с каждой главой. `爆甲` набрал так тринадцать
    написаний, народ `亞特族群` — пятнадцать.

    Старшинство есть у того, кто назвал первым: он единственный, кто называл
    вслепую, все остальные уже могли бы свериться. Поэтому канон — первый
    вариант с заглавной буквы. Строчная у первого — не спор о названии, а
    суждение «это нарицательное»; такой термин отсеется дальше по общему
    правилу, как и раньше.
    """
    variants = [v for _idx, v in ordered]
    drifted = len({v.lower() for v in variants}) > 1
    for idx, v in ordered:
        if v[:1].isupper():
            return v, drifted, idx
    return ordered[0][1], drifted, ordered[0][0]


def main():
    ap = argparse.ArgumentParser(description="Накопление терминов от переводчиков")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(RAW_FILE):
        print("Нет %s — сложи туда строки отчётов агентов." % os.path.basename(RAW_FILE))
        raise SystemExit(1)

    rows = parse_reports(read_text(RAW_FILE))
    if not rows:
        print("В отчётах не нашлось ни одной пары вида 中文=Русский.")
        raise SystemExit(0)

    # По главам: старшинство решает, какой вариант станет каноном.
    proposals = collections.defaultdict(list)
    for idx, cn, ru in sorted(rows):
        if ru not in [v for _i, v in proposals[cn]]:
            proposals[cn].append((idx, ru))
    print("[+] Предложено терминов: %d (из %d упоминаний)" % (len(proposals), len(rows)))

    known = set()
    v2 = load_json(GLOSSARY_V2, {}) or {}
    for e in v2.get("glossary", []):
        known.add(e["cn"])
    already = load_json(ACCUMULATED_FILE, {}) or {}
    for e in already.get("glossary", []):
        known.add(e["cn"])

    fresh = {cn: v for cn, v in proposals.items() if cn not in known}
    print("    уже в глоссарии: %d, новых: %d" % (len(proposals) - len(fresh), len(fresh)))
    if not fresh:
        print("Нечего добавлять.")
        raise SystemExit(0)

    print("[+] Считаю разброс по корпусу...", flush=True)
    docfreq, total = corpus_spread(set(fresh))

    accepted, rejected, conflicts = [], [], []
    for cn, variants in sorted(fresh.items()):
        if len(cn) < MIN_LEN:
            rejected.append({"cn": cn, "reason": "слишком короткий"})
            continue
        ru, drifted, named_in = pick_canonical(variants)
        if drifted:
            # Расхождение больше не отменяет термин: оно только помечает главы,
            # где имя написано иначе, — это работа для termfix.py, а не повод
            # оставить преемников без глоссарной записи.
            conflicts.append({"cn": cn, "canon": ru,
                              "variants": [v for _i, v in variants],
                              "первым назвал": "гл.%04d" % named_in})
        if LANG.has_source_script(ru):
            rejected.append({"cn": cn, "ru": ru, "reason": "русский вариант не переведён (остался язык оригинала)"})
            continue
        if not ru[:1].isupper():
            rejected.append({
                "cn": cn, "ru": ru,
                "reason": "со строчной буквы — агент передал это как нарицательное, а не имя",
            })
            continue
        df = docfreq.get(cn, 0)
        spread = df / max(total, 1)
        if spread >= MAX_SPREAD:
            rejected.append({
                "cn": cn, "ru": ru,
                "reason": "обычная лексика: встречается в %d главах из %d (%.0f%%)"
                          % (df, total, spread * 100),
            })
            continue
        accepted.append({
            "cn": cn, "ru": ru,
            "gender": "",
            "category": "Найдено переводчиком",
            "description": "Встречается в %d главах" % df,
            "docfreq": df,
            "source": "translator",
        })

    print("")
    print("=== НАКОПЛЕНИЕ ===")
    print("Принято    : %d" % len(accepted))
    print("Отклонено  : %d" % len(rejected))
    print("Конфликтов : %d" % len(conflicts))

    if accepted:
        print("")
        print("Принятые:")
        for e in accepted[:25]:
            print("   %-10s -> %-28s (глав: %d)" % (e["cn"], e["ru"], e["docfreq"]))
    if rejected:
        print("")
        print("Отклонённые:")
        for e in rejected[:15]:
            print("   %-10s -> %-24s %s" % (e["cn"], e.get("ru", "")[:24], e["reason"]))
    if conflicts:
        print("")
        print("[!] Названы по-разному. Канон взят у того, кто назвал первым;")
        print("    остальные написания — работа для termfix.py:")
        for c in conflicts:
            print("   %-10s -> %-24s (%s) из %s"
                  % (c["cn"], c["canon"], c["первым назвал"],
                     " / ".join(v for v in c["variants"] if v != c["canon"])))

    if args.dry_run:
        print("")
        print("(пробный прогон, ничего не записано)")
        raise SystemExit(0)

    merged = list(already.get("glossary", [])) + accepted
    save_json(ACCUMULATED_FILE, {
        "note": "Термины, найденные агентами-переводчиками и прошедшие проверку разброса. "
                "Подмешивается в glossary_v2.json при слиянии.",
        "count": len(merged),
        "rejected_last_run": rejected,
        "conflicts": conflicts,
        "glossary": merged,
    })
    print("")
    print("Записано в glossary_accumulated.json: всего %d терминов." % len(merged))
    print("Запусти glossary_merge.py, чтобы они попали в glossary_v2.json.")
    raise SystemExit(1 if conflicts else 0)


if __name__ == "__main__":
    main()
