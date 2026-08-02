#!/usr/bin/env python3
"""Merge agent verdicts from glossary_judged/*.json into glossary_v2.json.

Agents return one verdict per candidate. Accepted ones become glossary entries;
rejected ones are kept with their reason so a later run does not re-litigate
them. Conflicts (same Chinese term, two different Russian renderings from two
agents) are NOT silently resolved -- they go in a separate list, because
picking one at random is how terminology drift starts.

    python glossary_merge.py
    python glossary_merge.py --report   # show what was rejected and why
"""

import argparse
import collections
import os
import sys

from common import ROOT, load_glossary, load_json, save_json

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

JUDGED_DIR = os.path.join(ROOT, "glossary_judged")
OUT_FILE = os.path.join(ROOT, "glossary_v2.json")
OVERRIDES_FILE = os.path.join(ROOT, "glossary_overrides.json")
REQUIRED = ("cn", "ru", "gender", "category")


def apply_overrides(entries):
    """Apply the hand-made decisions in glossary_overrides.json.

    Kept as data rather than hand-edits to glossary_v2.json so that re-running
    the merge does not silently revert them.
    """
    ov = load_json(OVERRIDES_FILE, {}) or {}
    drop, rename, add = ov.get("drop", {}), ov.get("rename", {}), ov.get("add", [])

    kept = [e for e in entries if e["cn"] not in drop]
    dropped = len(entries) - len(kept)

    renamed = 0
    for e in kept:
        r = rename.get(e["cn"])
        if r and r.get("ru"):
            e["ru"] = r["ru"]
            e["source"] = "manual-rename"
            renamed += 1

    # Ручная запись бьёт накопленную, а не уступает ей. Раньше `add` применялся
    # только к отсутствующим терминам, и решение, принятое по корпусу, молча
    # проигрывало варианту, который накопитель выбрал механически: 爆甲 сел в
    # глоссарий «Взрывниками» вместо «Взрывобронника», 亞特族群 — «Асгардским
    # родом» вместо «племени Ат». Файл объявлен как решения поверх вердиктов,
    # значит поверх.
    by_cn = {e["cn"]: e for e in kept}
    added = overridden = 0
    for a in add:
        cur = by_cn.get(a["cn"])
        if cur is None:
            kept.append(dict(a))
            by_cn[a["cn"]] = kept[-1]
            added += 1
            continue
        if cur.get("ru") != a.get("ru"):
            overridden += 1
        cur.update({k: v for k, v in a.items() if v not in (None, "")})
        cur["source"] = "manual"

    return kept, {"dropped": dropped, "renamed": renamed,
                  "added": added, "overridden": overridden}


def decided_by_overrides():
    """cn, по которым решение уже принято вручную или арбитром.

    Конфликт (одно cn -> разные ru) считается по вердиктам судей, то есть ДО
    применения overrides. Без этой проверки решение арбитра не могло снять
    конфликт вообще: спорная запись выбрасывалась раньше, чем до неё доходили
    drop / rename / add. Хуже того, `rename` по спорному термину терял его
    молча — переименовывать было уже нечего. Скрипт по-прежнему ничего не
    угадывает сам: он лишь признаёт решение, которое кто-то принял и записал.
    """
    ov = load_json(OVERRIDES_FILE, {}) or {}
    decided = set(ov.get("drop", {})) | set(ov.get("rename", {}))
    decided |= {a["cn"] for a in ov.get("add", [])
                if isinstance(a, dict) and a.get("cn")}
    return decided


def load_verdicts():
    if not os.path.isdir(JUDGED_DIR):
        return [], ["каталог glossary_judged/ не найден"]

    verdicts, problems = [], []
    for name in sorted(os.listdir(JUDGED_DIR)):
        if not name.endswith(".json"):
            continue
        # arbitration_*.json — решения арбитра, их читает build_arbitration.py
        # и кладёт в overrides. Здесь это не вердикты, и жалоба на их формат
        # только пугает оператора.
        if name.startswith("arbitration_"):
            continue
        data = load_json(os.path.join(JUDGED_DIR, name))
        if data is None:
            problems.append("%s: не разобрался JSON" % name)
            continue
        items = data.get("verdicts", data) if isinstance(data, dict) else data
        if not isinstance(items, list):
            problems.append("%s: ожидался список вердиктов" % name)
            continue
        for item in items:
            if not isinstance(item, dict) or "cn" not in item:
                problems.append("%s: запись без поля cn" % name)
                continue
            item["_batch"] = name
            verdicts.append(item)
    return verdicts, problems


def main():
    ap = argparse.ArgumentParser(description="Слияние вердиктов агентов в glossary_v2.json")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()

    verdicts, problems = load_verdicts()
    if problems:
        print("[!] Проблемы при чтении вердиктов:")
        for p in problems:
            print("    - %s" % p)
    if not verdicts:
        print("Вердиктов нет — сначала запусти агентов на glossary_batches/.")
        raise SystemExit(1)

    curated, _auto, pronoun_rules = load_glossary()
    curated_by_cn = {t["cn"]: t for t in curated}

    accepted, rejected, malformed = {}, [], []
    for v in verdicts:
        if not v.get("accept", False):
            rejected.append(
                {"cn": v["cn"], "reason": v.get("reason", "без причины"), "batch": v["_batch"]}
            )
            continue
        missing = [f for f in REQUIRED if not str(v.get(f, "")).strip()]
        if missing:
            malformed.append({"cn": v["cn"], "missing": missing, "batch": v["_batch"]})
            continue
        accepted.setdefault(v["cn"], []).append(v)

    # Same term rendered differently by different agents -- surface, do not guess.
    decided = decided_by_overrides()
    conflicts = []
    final = []
    for cn, group in sorted(accepted.items()):
        variants = {g["ru"].strip() for g in group}
        # Спор, по которому решение уже записано, конфликтом не считается.
        # Запись при этом обязана дожить до apply_overrides: там её перебьёт
        # add, переименует rename или снимет drop. Взятый ниже group[0] —
        # заготовка под замену, а не выбор наугад.
        if len(variants) > 1 and cn not in decided:
            conflicts.append(
                {
                    "cn": cn,
                    "variants": [
                        {"ru": g["ru"], "gender": g.get("gender"), "batch": g["_batch"]}
                        for g in group
                    ],
                }
            )
            continue
        g = group[0]
        final.append(
            {
                "cn": cn,
                "ru": g["ru"].strip(),
                "gender": g.get("gender", "").strip(),
                "category": g.get("category", "").strip(),
                "description": g.get("description", "").strip(),
                "source": "agent",
            }
        )

    # Terms the translators surfaced and glossary_accumulate.py cleared. These
    # cover what the statistical extractor structurally cannot see -- compound
    # unit names like 錐形細胞, whose head 細胞 is far too common to survive the
    # dispersion filter.
    accumulated = load_json(os.path.join(ROOT, "glossary_accumulated.json"), {}) or {}
    seen_cn = {e["cn"] for e in final}
    from_translators = [e for e in accumulated.get("glossary", []) if e["cn"] not in seen_cn]
    final.extend(from_translators)

    # Hand-curated entries win over anything an agent proposed for the same term.
    final = [e for e in final if e["cn"] not in curated_by_cn]
    for t in curated:
        final.append(
            {
                "cn": t["cn"],
                "ru": t["ru"],
                "gender": t.get("gender", ""),
                "category": t.get("category", ""),
                "description": t.get("description", ""),
                "source": "curated",
            }
        )
    final, ov_stats = apply_overrides(final)
    final.sort(key=lambda e: (-len(e["cn"]), e["cn"]))

    # Two distinct Chinese terms rendered by the same Russian name. The reverse
    # direction (one cn, several ru) was checked above; this one was not, and
    # it slipped 7 collisions through -- four of them the same entity that the
    # n-gram extractor had split into fragments. Two entities sharing a name in
    # the translation is a defect the reader sees.
    # Synonyms in the source legitimately share one Russian rendering: 阿米巴蟲
    # and 變形蟲 are both simply "амёба". Forcing them apart would invent a
    # distinction the book does not make, so documented pairs are exempt.
    ov_raw = load_json(OVERRIDES_FILE, {}) or {}
    allowed_shared = {
        ru: set(spec.get("terms", []))
        for ru, spec in (ov_raw.get("allow_shared_ru", {}) or {}).items()
    }

    by_ru = {}
    for e in final:
        by_ru.setdefault(e["ru"], []).append(e)
    ru_collisions = []
    for ru, group in sorted(by_ru.items()):
        if len(group) < 2:
            continue
        if {x["cn"] for x in group} <= allowed_shared.get(ru, set()):
            continue  # documented synonyms, not a defect
        ru_collisions.append(
            {"ru": ru, "terms": [{"cn": x["cn"], "category": x["category"]} for x in group]}
        )

    save_json(
        OUT_FILE,
        {
            "note": (
                "Глоссарий v2: 55 выверенных вручную + принятое агентами. "
                "Конфликты не разрешены автоматически — смотри поле conflicts."
            ),
            "pronoun_rules": pronoun_rules,
            "counts": {
                "total": len(final),
                "curated": len(curated),
                "from_agents": len(final) - len(curated),
                "rejected": len(rejected),
                "conflicts": len(conflicts),
                "ru_collisions": len(ru_collisions),
                "malformed": len(malformed),
                "overrides": ov_stats,
            },
            "conflicts": conflicts,
            "ru_collisions": ru_collisions,
            "rejected": rejected,
            "malformed": malformed,
            "glossary": final,
        },
    )

    print("=== ГЛОССАРИЙ v2 ===")
    print("Всего терминов      : %d" % len(final))
    print("  выверенных вручную: %d" % len(curated))
    print("  принято агентами  : %d" % (len(final) - len(curated)))
    print("Отклонено агентами  : %d" % len(rejected))
    print("Ручные правки       : снято %d, переименовано %d, добавлено %d, перебито %d"
          % (ov_stats["dropped"], ov_stats["renamed"],
             ov_stats["added"], ov_stats["overridden"]))
    print("Конфликтов (одно cn -> разные ru): %d" % len(conflicts))
    print("Коллизий (разные cn -> одно ru) : %d" % len(ru_collisions))
    print("Кривых записей      : %d" % len(malformed))

    if ru_collisions:
        print("")
        print("[!] Одинаковое русское имя у разных терминов — читатель увидит их как одно:")
        for c in ru_collisions[:10]:
            print("    «%s» <- %s" % (c["ru"], ", ".join(t["cn"] for t in c["terms"])))
        print("    Решается через glossary_overrides.json (drop / rename / add).")

    if conflicts:
        print("")
        print("[!] Конфликты требуют решения вручную (в glossary_v2.json -> conflicts):")
        for c in conflicts[:10]:
            print("    %s -> %s" % (c["cn"], " / ".join(v["ru"] for v in c["variants"])))

    if args.report and rejected:
        print("")
        print("Отклонено (первые 40):")
        for r in rejected[:40]:
            print("    %-8s %s" % (r["cn"], r["reason"]))

    raise SystemExit(1 if conflicts or malformed or ru_collisions else 0)


if __name__ == "__main__":
    main()
