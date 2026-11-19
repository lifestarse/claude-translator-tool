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
# Сколько уже принятых записей показать судье как образец, если файла-образца нет.
STYLE_SAMPLE = 25

# Конструкции именования («назвали X», 叫做X, 'X'라고 불렀다) и стоп-слова
# зависят от языка оригинала и живут в lang.py. Захват в кавычках точнее:
# кавычки дают границы; без них захват нарочно короче.
NAMING_RES = LANG.naming_res()

CONTEXT = 60
MAX_CONTEXTS = 2


REQUIRED = ("cn", "ru", "gender", "category")


def load_known():
    """cn, по которым решение уже есть: принятые, отклонённые и спорные.

    Раньше сюда шли только принятые. Отклонённый или конфликтный термин
    возвращался к судье в каждом следующем окне и после каждого рестарта:
    повторный суд за деньги, а второй вердикт с другим ru превращал термин
    в конфликт, merge выкидывал его, и в следующей волне его судили снова.
    Вердикты читаются прямо из glossary_judged/, а не только из v2: если
    merge не дошёл до записи, решение судьи всё равно уже принято.
    Кривой принятый вердикт (без ru или рода) решением не считается —
    такой термин судья увидит ещё раз.
    """
    known = set()
    for path in (GLOSSARY_V2, ACCUMULATED):
        data = load_json(path, {}) or {}
        for e in data.get("glossary", []):
            known.add(e["cn"])
    v2 = load_json(GLOSSARY_V2, {}) or {}
    for e in (v2.get("rejected") or []) + (v2.get("conflicts") or []):
        if isinstance(e, dict) and e.get("cn"):
            known.add(e["cn"])
    if os.path.isdir(JUDGED_DIR):
        for name in os.listdir(JUDGED_DIR):
            if not name.endswith(".json") or name.startswith("arbitration_"):
                continue
            data = load_json(os.path.join(JUDGED_DIR, name))
            items = data.get("verdicts", []) if isinstance(data, dict) else data
            for v in items if isinstance(items, list) else []:
                if not isinstance(v, dict) or not isinstance(v.get("cn"), str):
                    continue
                if v.get("accept") and not all(
                        isinstance(v.get(f), str) and v[f].strip() for f in REQUIRED):
                    continue
                known.add(v["cn"])
    return known


def translator_leftovers(known):
    """Имена от переводчиков, которые накопитель не принял сам из-за разброса.

    glossary_accumulate.py не выбрасывает такие имена (сквозной персонаж
    живёт дольше любого порога), а откладывает в to_judge. Решает судья.
    """
    acc = load_json(ACCUMULATED, {}) or {}
    out = collections.OrderedDict()
    for e in acc.get("to_judge") or []:
        cn = e.get("cn") if isinstance(e, dict) else None
        if not cn or cn in known:
            continue
        rec = {"cn": cn, "quoted": False, "chapters": [], "contexts": [],
               "proposed_ru": e.get("ru", ""),
               "origin": "предложено переводчиком, накопитель отложил по разбросу"}
        idx = e.get("named_in")
        if isinstance(idx, int) and os.path.exists(chapter_path(idx)):
            text = read_text(chapter_path(idx))
            k = text.find(cn)
            if k >= 0:
                rec["chapters"].append(idx)
                rec["contexts"].append("гл.%d: …%s…" % (idx, text[
                    max(0, k - CONTEXT):k + len(cn) + CONTEXT].replace("\n", " ").strip()))
        out[cn] = rec
    return out


def style_step(n):
    """Пункт задания про образец стиля, или пусто.

    Файл-образец есть не у каждого тайтла: у нового его нет, и судья упирался
    в отсутствующий файл на первом же шаге. Тогда образцом служат уже принятые
    записи глоссария прямо в задании; при пустом глоссарии пункта нет вовсе.
    Возвращает (текст пункта, сдвиг нумерации следующих пунктов).
    """
    if os.path.exists(os.path.join(ROOT, STYLE_REF)):
        return "%d. Прочитай `%s` — образец манеры принятых терминов, держись её.\n" % (
            n, STYLE_REF), 1
    sample = style_sample()
    if not sample:
        return "", 0
    return ("%d. Держись манеры уже принятых терминов:\n%s\n" % (n, sample)), 1


def style_sample():
    data = load_json(GLOSSARY_V2, {}) or {}
    rows = [e for e in data.get("glossary", []) if e.get("cn") and e.get("ru")]
    return "\n".join("   - %s → %s%s" % (
        e["cn"], e["ru"], " (%s)" % e["gender"] if e.get("gender") else "")
        for e in rows[:STYLE_SAMPLE])


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

{style_step}{n1}. Прочитай `{batch_file}` — список кандидатов с контекстами.
{n2}. По каждому вынеси вердикт.
{n3}. Запиши результат в `{out_file}` в формате:

```json
{{"verdicts": [
  {{"cn": "<термин>", "accept": true, "ru": "<русское имя>",
    "gender": "Она / Её", "category": "<техника / место / организация / персонаж / …>",
    "description": "…"}},
  {{"cn": "醫學", "accept": false, "reason": "обычная лексика: медицина"}}
]}}
```

Верни ОДНУ строку отчёта: `волна {lo}-{hi} | принято N | отклонено M`.

## Правила

**Отклоняй обычную лексику.** Конструкция именования ловит не только коинажи:
`醫學` «медицина», `希望` «надежда», `激動` «волнение» — это словарные
слова, автор просто называет понятие. Такому в глоссарии не место: переводчик
и сам знает эти слова, а лишняя запись только раздувает задание. Признак коинажа — у слова **нет** готового
русского соответствия, и переводчики разойдутся в вариантах.

**Частота — обратный признак.** У каждого кандидата указано, в скольких главах
книги он встречается (`docfreq`, `spread`). Чем выше это число, тем вероятнее,
что перед тобой обычное слово: по-настоящему редкие имена собственные сидят в
своих главах и отсутствуют в остальных. Кандидат из 300 глав почти наверняка
словарный, кандидат из 5 — почти наверняка коинаж. Это подсказка, а не правило:
решает смысл.

**Род обязателен** для принятых — по нему согласуются прилагательные и глаголы.

**Имя должно быть коротким.** Термин встретится сотни раз; подстрочник в четыре
слова в прозе не читается. Один-два слова.

**Кандидаты с `proposed_ru`** пришли от переводчиков: это имя уже стоит в готовых
главах. Если оно годится — бери его, иначе главы придётся править.

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
    tag = "wave_%04d_%04d" % (lo, hi)
    task_file = os.path.join(BATCH_DIR, "judge_" + tag + ".md")
    batch_file = os.path.join(BATCH_DIR, tag + ".json")
    # autorun решает, звать ли судью, по одному наличию judge_<tag>.md. Задание
    # от прошлого запуска на том же окне иначе пережило бы выход «новых нет»
    # ниже, и судья пересудил бы старый список, перезаписав свой же вердикт.
    if not args.dry_run:
        for stale in (task_file, batch_file):
            if os.path.exists(stale):
                os.remove(stale)
    known = load_known()
    print("[+] В глоссарии уже: %d терминов" % len(known))

    found = harvest(lo, hi)
    print("[+] Конструкций именования в главах %d–%d: %d" % (lo, hi, len(found)))

    fresh = {k: v for k, v in found.items() if k not in known}
    print("    из них новых: %d" % len(fresh))
    leftovers = translator_leftovers(known)
    for cn, rec in leftovers.items():
        fresh.setdefault(cn, rec)
    if leftovers:
        print("    + отложено накопителем (от переводчиков): %d" % len(leftovers))
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
        if rec["docfreq"] < args.min_docfreq and not rec.get("proposed_ru"):
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
    save_json(batch_file, {
        "range": [lo, hi],
        "note": "Термины, которые книга вводит конструкциями именования в этом диапазоне.",
        "candidates": cands,
    })

    # Вердикт прошлого суда по этому же окну не перезаписывается: load_known
    # уже исключил решённое, и новый список — только добавка (рестарт окна,
    # отложенные накопителем). Записанный поверх файл стёр бы прежние вердикты,
    # а merge собирает v2 из них заново — принятые термины пропали бы из v2.
    out_name, n = tag, 1
    while os.path.exists(os.path.join(JUDGED_DIR, out_name + ".json")):
        n += 1
        out_name = "%s_r%d" % (tag, n)

    taken = taken_names()
    step, shift = style_step(1)
    write_text(task_file, JUDGE_TEMPLATE.format(
        lo=lo, hi=hi,
        style_step=step, n1=1 + shift, n2=2 + shift, n3=3 + shift,
        batch_file="glossary_batches/%s.json" % tag,
        out_file="glossary_judged/%s.json" % out_name,
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
