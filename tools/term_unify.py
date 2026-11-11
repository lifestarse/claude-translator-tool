#!/usr/bin/env python3
"""Единые термины в готовом переводе: правка от позиций термина в оригинале.

Абзац попадает в правку, только если в его оригинале стоит сам термин (поле src),
а в переводе — неканонический вариант (bad_re). Совпадение по одному русскому тексту
недостаточно: «инвентарь» бывает обычным словом, а «магическая башня» — другой
организацией. Термины и решения — в term_unify.json:

    {"terms": [{"key": "mana_burn", "canon": "Сожжение маны", "gender": "ср.",
                "src": "마나\\s?번", "canon_re": "[Сс]ожжени\\w+ маны",
                "bad_re": "[Вв]ыжигани\\w+ маны|[Сс]жигани\\w+ маны",
                "note": "заклинание протагониста; в квадратных скобках — [Сожжение маны]"}]}

Канон выбирай по числам из audit: где у переводчиков уже сложилось большинство,
правок меньше.

    python tools/term_unify.py audit          как переведён каждый термин сейчас
    python tools/term_unify.py tasks          снимок глав + задания gfx/batch_NNN.md
    python glossfix_driver.py --slot 1        агент glossfix правит; приёмка и откат поглавно
    python tools/term_unify.py check          правки только в заданных абзацах, иначе откат
"""
import argparse
import json
import os
import re
import shutil
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from common import (  # noqa: E402
    CHAPTERS_DIR, chapter_path, load_json, marker_count, read_text, ru_path, split_paragraphs,
    validate_translation, write_text,
)

CONFIG = os.path.join(ROOT, "term_unify.json")
GFX_DIR = os.path.join(ROOT, "gfx")
SNAP = os.path.join(ROOT, "fixes", "unify_snapshot")
PLAN = os.path.join(ROOT, "fixes", "unify_plan.json")
BATCH = 30

HEADER = """# Единые термины: пакет {tag:03d}

По каждой главе прочитай Read ТОЛЬКО указанные строки (offset/limit), найди в абзаце
неканоничный вариант термина и замени его каноном инструментом Edit в нужном падеже
и числе, поправив согласование (род и число прилагательных, глаголов, местоимений).
Больше в абзаце ничего не меняй: ни слов, ни порядка, ни пунктуации. Другие абзацы
не трогай. Если по смыслу вариант относится не к этому термину — пропусти абзац.
Регистр — как в каноне (в начале предложения первая буква заглавная).

В конце верни одну строку: `batch {tag:03d} ok | глав N | замен M`.

"""


def terms():
    data = load_json(CONFIG, {}) or {}
    out = data.get("terms", [])
    if not out:
        raise SystemExit("В %s нет терминов — см. пример в начале этого файла." % os.path.basename(CONFIG))
    return out


def accepted():
    out = []
    for f in sorted(os.listdir(CHAPTERS_DIR)):
        if not (f.startswith("chapter_") and f.endswith(".txt")):
            continue
        n = int(f[8:-4])
        if os.path.exists(ru_path(n)):
            ru = split_paragraphs(read_text(ru_path(n)))
            if marker_count(ru) <= marker_count(split_paragraphs(read_text(chapter_path(n)))):
                out.append(n)
    return out


def hits():
    """{key: [(глава, абзац, оригинал, перевод, варианты, есть ли канон)]}"""
    table = terms()
    out = defaultdict(list)
    for n in accepted():
        s, r = split_paragraphs(read_text(chapter_path(n))), split_paragraphs(read_text(ru_path(n)))
        if len(s) != len(r):
            continue
        for i, (a, b) in enumerate(zip(s, r), 1):
            for t in table:
                if not re.search(t["src"], a):
                    continue
                var = [m.group(0) for m in re.finditer(t["bad_re"], b) if not re.match(t["canon_re"], m.group(0))]
                out[t["key"]].append((n, i, a, b, var, bool(re.search(t["canon_re"], b))))
    return out


def audit():
    h = hits()
    for t in terms():
        rows = h.get(t["key"], [])
        variants = Counter(v for r in rows for v in r[4])
        print("%-18s «%s»: с термином %d, с каноном %d, к правке %d"
              % (t["key"], t["canon"], len(rows), sum(r[5] for r in rows), sum(1 for r in rows if r[4])))
        if variants:
            print("      " + "; ".join("%s ×%d" % kv for kv in variants.most_common(6)))


def para_lines(text):
    bounds, start = [], None
    lines = text.split("\n")
    for i, line in enumerate(lines, 1):
        if line.strip():
            start = i if start is None else start
        elif start is not None:
            bounds.append((start, i - 1))
            start = None
    if start is not None:
        bounds.append((start, len(lines)))
    return bounds


def tasks():
    by_key = {t["key"]: t for t in terms()}
    targets = defaultdict(list)
    for key, rows in hits().items():
        for n, i, a, b, var, _ in rows:
            if var:
                targets[n].append((i, by_key[key], a, var))
    if not targets:
        print("Править нечего.")
        return
    os.makedirs(SNAP, exist_ok=True)
    os.makedirs(GFX_DIR, exist_ok=True)
    for n in targets:
        snap = os.path.join(SNAP, "chapter_%04d.txt" % n)
        if not os.path.exists(snap):
            shutil.copyfile(ru_path(n), snap)
    for old in os.listdir(GFX_DIR):
        if old.startswith("batch_"):
            os.remove(os.path.join(GFX_DIR, old))
    blocks, cur, size = [], [], 0
    for n in sorted(targets):
        bounds = para_lines(read_text(ru_path(n)))
        lines = ["## Глава %04d — файл `ru/chapter_%04d.txt`\n" % (n, n)]
        for i, t, a, var in sorted(targets[n], key=lambda x: x[0]):
            lo, hi = bounds[i - 1]
            lines.append("- абзац %d, строки %d–%d (Read offset=%d limit=%d)\n"
                         "  термин: канон **%s** (%s) — %s\n  оригинал: %s\n  неканонично сейчас: %s\n"
                         % (i, lo, hi, lo, hi - lo + 1, t["canon"], t.get("gender", ""), t.get("note", ""),
                            a[:260], ", ".join("«%s»" % v for v in dict.fromkeys(var))))
        cur.append((n, "\n".join(lines)))
        size += len(targets[n])
        if size >= BATCH:
            blocks.append(cur)
            cur, size = [], 0
    if cur:
        blocks.append(cur)
    index = []
    for tag, block in enumerate(blocks, 1):
        write_text(os.path.join(GFX_DIR, "batch_%03d.md" % tag),
                   HEADER.format(tag=tag) + "\n".join(text for _, text in block))
        index.append({"batch": tag, "chapters": [n for n, _ in block]})
    write_text(os.path.join(GFX_DIR, "batch_index.json"), json.dumps(index, ensure_ascii=False, indent=1))
    write_text(PLAN, json.dumps({str(n): sorted({x[0] for x in v}) for n, v in targets.items()}, indent=1))
    print("Абзацев к правке: %d в %d главах, пакетов: %d. Дальше: python glossfix_driver.py --slot 1"
          % (sum(len(v) for v in targets.values()), len(targets), len(blocks)))


def check():
    plan = load_json(PLAN, {}) or {}
    changed_total = rolled = 0
    for key_n, allowed in sorted(plan.items(), key=lambda kv: int(kv[0])):
        n = int(key_n)
        snap_path = os.path.join(SNAP, "chapter_%04d.txt" % n)
        if not os.path.exists(snap_path):
            continue
        snap, now = read_text(snap_path), read_text(ru_path(n))
        a, b = split_paragraphs(snap), split_paragraphs(now)
        why = None
        if len(a) != len(b):
            why = "число абзацев %d -> %d" % (len(a), len(b))
        else:
            changed = [i for i, (x, y) in enumerate(zip(a, b), 1) if x != y]
            extra = [i for i in changed if i not in allowed]
            if extra:
                why = "правка вне заданных абзацев: %s" % extra[:5]
            elif changed:
                probs = validate_translation(read_text(chapter_path(n)), now)
                if probs:
                    why = "приёмка: %s" % "; ".join(probs[:2])
            changed_total += len(changed)
        if why:
            write_text(ru_path(n), snap)
            rolled += 1
            print("гл.%04d ОТКАТ — %s" % (n, why))
    left = sum(1 for rows in hits().values() for r in rows if r[4] and str(r[0]) in plan)
    print("Изменено абзацев: %d, откатов глав: %d, осталось неканоничных абзацев: %d"
          % (changed_total, rolled, left))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Единые термины по выравниванию с оригиналом")
    ap.add_argument("cmd", choices=("audit", "tasks", "check"))
    {"audit": audit, "tasks": tasks, "check": check}[ap.parse_args().cmd]()
