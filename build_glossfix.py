#!/usr/bin/env python3
"""Сборка выровненных заданий: привести имена сущностей к глоссарию.

Для перечисленных китайских терминов (решения сверки 02.08.2026) находит
главы, где термин стоит в источнике, а основы канонического имени в переводе
нет, и собирает пакетные задания: по каждому затронутому абзацу — китайский
контекст, каноническое имя и номер строк. Выравнивание 1:1 по абзацам даёт
точное место — регэкспы по русскому тексту не нужны вовсе, что закрывает
класс «шаблон по нарицательному» из аудита termfix-pattern.

    python build_glossfix.py
"""

import io
import json
import os
import re
import sys

from common import ROOT, chapter_path, load_json, read_text, ru_path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

GFX_DIR = os.path.join(ROOT, "gfx")
SIZE = 12

# Термины, по которым текст приводится к глоссарию. Второй раунд 02.08.2026:
# остатки первого прогона плюс два забытых термина (寒石類生物, 飛針) и
# новичок 偉大工程, всплывший после починки зондов.
TARGET_CN = [
    "漿糊", "寒石類生物", "光球", "光團", "恐龍", "偉大工程", "亞特族群",
    "發光大道", "藤蔓", "飛針", "爾什",
]

HEADER = """# Приведение имён к глоссарию: пакет {tag:03d}

Файл главы указан над каждым блоком. Читай ТОЛЬКО указанные диапазоны строк
(Read с offset/limit), правь точечными Edit. Меняется только имя сущности и
согласование с ним. Если термин в абзаце употреблён в бытовом смысле или
речь о другой сущности — пропусти абзац.

"""


def para_bounds(text):
    lines = text.splitlines()
    out, start = [], None
    for n, line in enumerate(lines, 1):
        if line.strip():
            if start is None:
                start = n
        elif start is not None:
            out.append((start, n - 1))
            start = None
    if start is not None:
        out.append((start, len(lines)))
    return out


def main():
    g = load_json(os.path.join(ROOT, "glossary_v2.json"), {})
    entries = {}
    for e in g.get("glossary", []):
        cn = e.get("cn")
        if cn in TARGET_CN and cn not in entries:
            entries[cn] = e["ru"]
    missing = [cn for cn in TARGET_CN if cn not in entries]
    if missing:
        print("[!] нет в глоссарии:", missing)
    all_cns = [e.get("cn") for e in g.get("glossary", []) if e.get("cn")]
    supers = {cn: [c for c in all_cns if c != cn and cn in c] for cn in entries}

    def stems(ru):
        words = re.findall(r"[А-Яа-яЁё]{3,}", ru.replace("ё", "е").replace("Ё", "Е"))
        return [w.lower()[: max(3, len(w) - 3)] for w in words]

    names = sorted(n for n in os.listdir(os.path.join(ROOT, "ru"))
                   if n.startswith("chapter_") and n.endswith(".txt"))
    jobs = {}
    for name in names:
        idx = int(name[8:12])
        try:
            src = read_text(chapter_path(idx))
        except OSError:
            continue
        hits = []
        low = None
        for cn, canon in entries.items():
            if cn not in src or any(s in src for s in supers[cn]):
                continue
            if low is None:
                low = read_text(ru_path(idx)).lower().replace("ё", "е")
            if all(st in low for st in stems(canon)):
                continue
            hits.append((cn, canon))
        if hits:
            jobs[idx] = hits

    os.makedirs(GFX_DIR, exist_ok=True)
    for f in os.listdir(GFX_DIR):
        os.remove(os.path.join(GFX_DIR, f))

    tags, block, cur = [], [], 0
    items = sorted(jobs.items())
    for n, (idx, hits) in enumerate(items, 1):
        src = read_text(chapter_path(idx))
        ru = read_text(ru_path(idx))
        sp, rp = src.split("\n\n"), ru.split("\n\n")
        bounds = para_bounds(ru)
        lines = ["## Глава %04d — файл `ru/chapter_%04d.txt`\n" % (idx, idx)]
        for cn, canon in hits:
            lines.append("Термин `%s` — каноническое имя: **%s**\n" % (cn, canon))
            if len(sp) == len(rp) == len(bounds):
                shown = 0
                for i, para in enumerate(sp):
                    if cn in para and shown < 6:
                        lo, hi = bounds[i]
                        pos = para.find(cn)
                        lines.append("- строки %d–%d; CN: …%s…" % (
                            lo, hi, para[max(0, pos - 20):pos + 28].replace("\n", " ")))
                        shown += 1
            else:
                lines.append("- (выравнивание абзацев не сошлось — найди термин по смыслу)")
            lines.append("")
        block.append("\n".join(lines))
        if len(block) >= SIZE or n == len(items):
            cur += 1
            io.open(os.path.join(GFX_DIR, "batch_%03d.md" % cur), "w",
                    encoding="utf-8", newline="\n").write(
                HEADER.format(tag=cur) + "\n".join(block))
            tags.append({"batch": cur,
                         "chapters": [i for i, _ in items[n - len(block):n]]})
            block = []
    io.open(os.path.join(GFX_DIR, "batch_index.json"), "w",
            encoding="utf-8", newline="\n").write(
        json.dumps(tags, ensure_ascii=False, indent=1))
    print("глав к правке: %d, пакетов: %d" % (len(jobs), len(tags)))


if __name__ == "__main__":
    main()
