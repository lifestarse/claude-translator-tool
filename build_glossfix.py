#!/usr/bin/env python3
"""Сборка выровненных заданий: привести имена сущностей к глоссарию.

Для перечисленных китайских терминов находит главы, где термин стоит в источнике, а основы канонического имени в переводе
нет, и собирает пакетные задания: по каждому затронутому абзацу — китайский
контекст, каноническое имя и номер строк. Выравнивание 1:1 по абзацам даёт
точное место — регэкспы по русскому тексту не нужны вовсе, что закрывает
класс «шаблон по нарицательному» из аудита termfix-pattern.

    python build_glossfix.py --cn 血氣強化波 符文之輝
    python build_glossfix.py --from-drift      # всё, что glossjudge признал «дрейфом»

Встроенного списка нет: он был у прошлой книги (漿糊, 寒石類生物, 光球, …) и на
новой давал 0 пакетов или, хуже, правку по случайно совпавшим cn.
"""

import argparse
import glob
import io
import json
import os
import re
import sys

from common import ROOT, chapter_path, load_json, read_text, ru_path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

GFX_DIR = os.path.join(ROOT, "gfx")
DRIFT_DIR = os.path.join(ROOT, "drift")
SIZE = 12

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


def drift_targets():
    """cn с вердиктом «дрейф» или «смешанное» из drift/verdict_*.json.

    «Смешанное» тоже идёт в правку: шумные абзацы агент glossfix пропускает
    сам по правилу 1, а дрейфовые иначе так и остались бы с чужим именем.
    """
    out = []
    for path in sorted(glob.glob(os.path.join(DRIFT_DIR, "verdict_*.json"))):
        data = load_json(path, {}) or {}
        for v in data.get("verdicts", []) if isinstance(data, dict) else []:
            if not isinstance(v, dict):
                continue
            if v.get("verdict") in ("дрейф", "смешанное") and v.get("cn") \
                    and v["cn"] not in out:
                out.append(v["cn"])
    return out


def main():
    ap = argparse.ArgumentParser(description="Пакеты приведения имён к глоссарию")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--cn", nargs="+", metavar="中文", help="термины для правки")
    src.add_argument("--from-drift", action="store_true",
                     help="взять термины из drift/verdict_*.json (дрейф, смешанное)")
    ap.add_argument("--notes", metavar="JSON",
                    help="{中文: «границы правки»} — что именно менять и что не трогать "
                         "(составные слова, бытовое значение); уходит агенту под термином")
    ap.add_argument("--phrase-misses", action="store_true",
                    help="только абзацы, где основы канона есть, а самой фразы нет — "
                         "дочистка после прохода, проверявшего основы врозь")
    args = ap.parse_args()
    notes = load_json(args.notes, {}) if args.notes else {}

    targets = args.cn or drift_targets()
    if not targets:
        print("Терминов к правке нет: в drift/verdict_*.json ни одного «дрейфа».")
        print("Сначала build_drift_judge.py и drift_judge_driver.py, либо --cn.")
        raise SystemExit(1)
    print("терминов к правке: %d — %s" % (len(targets), " ".join(targets)))

    g = load_json(os.path.join(ROOT, "glossary_v2.json"), {})
    entries = {}
    for e in g.get("glossary", []):
        cn = e.get("cn")
        if cn in targets and cn not in entries:
            entries[cn] = e["ru"]
    missing = [cn for cn in targets if cn not in entries]
    if missing:
        print("[!] нет в глоссарии:", missing)
    all_cns = [e.get("cn") for e in g.get("glossary", []) if e.get("cn")]
    supers = {cn: [c for c in all_cns if c != cn and cn in c] for cn in entries}

    def stems(ru):
        words = re.findall(r"[А-Яа-яЁё]{3,}", ru.replace("ё", "е").replace("Ё", "Е"))
        return [w.lower()[: max(3, len(w) - 3)] for w in words]

    def phrase(ru):
        # Слова канона подряд, каждое — основа плюс любое окончание. Основы
        # врозь по абзацу пропускали «ци крови уровня элиты» за «элитную ци
        # крови» и «прабабушку-наставницу» за «бабушку-наставницу»: 521 абзац
        # на 89 терминов остался со старым именем после полного прохода.
        words = re.findall(r"[А-Яа-яЁёA-Za-z0-9]+",
                           ru.replace("ё", "е").replace("Ё", "Е").lower())
        parts = [re.escape(w[: max(3, len(w) - 3)] if len(w) >= 4 else w) + r"\w*"
                 for w in words]
        return re.compile(r"(?<!\w)" + r"[\s\-«»„“\"']+".join(parts)) if parts else None

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
        sp = rp = None
        for cn, canon in entries.items():
            if cn not in src:
                continue
            if rp is None:
                sp = src.split("\n\n")
                rp = read_text(ru_path(idx)).split("\n\n")
            st = stems(canon)
            ph = phrase(canon)
            if len(sp) == len(rp):
                # По абзацу, а не по главе: глава, где канон стоит хоть раз,
                # раньше выпадала целиком — и вместе с ней все абзацы, где имя
                # так и осталось старым («Зарождающейся души» в гл. 395 — пять
                # мест после того, как первая правка вписала канон).
                # Составной термин (元嬰境 для 元嬰) снимает абзац, а не главу:
                # иначе одно 元嬰境 в главе прятало все голые 元嬰 рядом.
                todo = [i for i, para in enumerate(sp) if cn in para
                        and not any(s in para for s in supers[cn])
                        and not (ph and ph.search(rp[i].lower().replace("ё", "е")))
                        and (not args.phrase_misses or all(
                            s in rp[i].lower().replace("ё", "е") for s in st))]
                if not todo:
                    continue
            else:
                low = "\n\n".join(rp).lower().replace("ё", "е")
                if any(s in src for s in supers[cn]) or all(s in low for s in st):
                    continue
                todo = []
            hits.append((cn, canon, todo))
        if hits:
            jobs[idx] = hits

    os.makedirs(GFX_DIR, exist_ok=True)
    for f in os.listdir(GFX_DIR):
        if f != ".gitkeep":
            os.remove(os.path.join(GFX_DIR, f))

    tags, block, cur = [], [], 0
    items = sorted(jobs.items())
    for n, (idx, hits) in enumerate(items, 1):
        src = read_text(chapter_path(idx))
        ru = read_text(ru_path(idx))
        sp, rp = src.split("\n\n"), ru.split("\n\n")
        bounds = para_bounds(ru)
        lines = ["## Глава %04d — файл `ru/chapter_%04d.txt`\n" % (idx, idx)]
        for cn, canon, todo in hits:
            lines.append("Термин `%s` — каноническое имя: **%s**\n" % (cn, canon))
            if notes.get(cn):
                lines.append("Границы правки: %s\n" % notes[cn])
            if todo and len(sp) == len(rp) == len(bounds):
                # Все абзацы без канона, без прежнего потолка в шесть: хвост
                # сверх него молча оставался со старым именем.
                for i in todo:
                    lo, hi = bounds[i]
                    pos = sp[i].find(cn)
                    lines.append("- строки %d–%d; CN: …%s…" % (
                        lo, hi, sp[i][max(0, pos - 20):pos + 28].replace("\n", " ")))
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
