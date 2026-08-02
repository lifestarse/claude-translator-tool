#!/usr/bin/env python3
"""Сборка ПАКЕТНЫХ заданий termfix: много глав одному агенту.

Поглавные задания оплачивают тяжёлый префикс (системный промпт + определение
агента, ~25k токенов) на каждой главе, хотя работы в главе — полтора абзаца.
Пакет платит его один раз на полтора десятка глав.

Пакет безопасен ровно потому, что задание абзацное: текст правимых абзацев
лежит прямо в задании, агенту не нужно читать главы целиком, и контекст не
раздувается — то, из-за чего пачки были бы вредны при поглавном чтении.

    python build_termfix_batches.py --size 15
"""

import argparse
import os
import sys

import termfix
from common import ROOT, read_text, ru_path, save_json, write_text

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

FIX_DIR = os.path.join(ROOT, "fixes")

HEADER = """# Пакетная правка терминологии: глав {n}

Ниже — несколько глав. В каждой перечислены затронутые абзацы с номерами строк
и таблица замен. Главы ЦЕЛИКОМ не читай: по каждому абзацу сделай Read нужного
диапазона строк (offset/limit), затем точечный Edit.

Порядок обязателен: Read перед Edit того же файла.

## Чего делать нельзя

Ничего, кроме перечисленных замен и согласования с ними (род, число, падеж
зависимых слов). Формулировки, порядок слов, пунктуацию и разбиение на абзацы
не трогать. Число абзацев в каждой главе меняться не должно.

---
"""

CHAPTER_BLOCK = """
## Глава {idx:04d} — `{path}`

Замены:

{table}

Абзацы:

{snippets}

---
"""


def main():
    ap = argparse.ArgumentParser(description="Пакетные задания termfix")
    ap.add_argument("--size", type=int, default=15, help="глав в пакете")
    args = ap.parse_args()

    fixes = termfix.load_fixes()
    names = sorted(n for n in os.listdir(os.path.join(ROOT, "ru"))
                   if n.startswith("chapter_") and n.endswith(".txt"))

    jobs = []
    for name in names:
        idx = int(name[8:12])
        text = read_text(ru_path(idx))
        found = termfix.scan(text, fixes, idx)
        if not found:
            continue
        jobs.append({
            "idx": idx,
            "table": termfix.table_for(found),
            "snippets": termfix.snippets_for(text, found),
        })

    os.makedirs(FIX_DIR, exist_ok=True)
    for old in os.listdir(FIX_DIR):
        if old.startswith("batch_"):
            os.remove(os.path.join(FIX_DIR, old))

    prepared = []
    for n in range(0, len(jobs), args.size):
        chunk = jobs[n:n + args.size]
        tag = n // args.size + 1
        body = HEADER.format(n=len(chunk))
        for j in chunk:
            body += CHAPTER_BLOCK.format(
                idx=j["idx"], path="ru/chapter_%04d.txt" % j["idx"],
                table=j["table"], snippets=j["snippets"])
        write_text(os.path.join(FIX_DIR, "batch_%03d.md" % tag), body)
        prepared.append({"batch": tag, "file": "fixes/batch_%03d.md" % tag,
                         "chapters": [j["idx"] for j in chunk]})

    save_json(os.path.join(FIX_DIR, "batch_index.json"), prepared)
    print("[+] Глав к правке: %d, пакетов: %d (по %d)"
          % (len(jobs), len(prepared), args.size))


if __name__ == "__main__":
    main()
