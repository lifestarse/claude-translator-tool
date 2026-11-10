#!/usr/bin/env python3
"""Затравочный глоссарий: выверенные вручную термины + частотные кандидаты.

Пишет glossary.json (запасной глоссарий для common.load_glossary) и
glossary.md для человека. Выверенные термины и правила родов тайтла
вписываются в PRONOUN_RULES и KNOWN_DICTIONARY ниже — для нового тайтла
они пусты. Частотные n-граммы попадают в файл с пометкой
«Авто-обнаруженный термин» и в промпты не уходят.

    python glossary_builder.py
"""

import argparse
import os
import sys
import re
from collections import Counter, defaultdict

from common import save_json

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding='utf-8')

ROOT = os.path.dirname(os.path.abspath(__file__))
CHAPTERS_DIR = os.path.join(ROOT, "chapters")
GLOSSARY_JSON = os.path.join(ROOT, "glossary.json")
GLOSSARY_MD = os.path.join(ROOT, "glossary.md")

# ============================================================
# Правила согласования в роде (Он / Она / Оно) — заполняются под тайтл.
# Формат:
#   "Имя (原文)": {"ru_gender": "Женский род (Она / Её)",
#                  "rule_desc": "почему и в каких контекстах"},
# ============================================================
PRONOUN_RULES = {}

# Выверенные термины тайтла. Формат:
#   "原文": {"ru": "Перевод", "en": "English", "gender": "Она / Её",
#           "category": "Персонаж", "desc": "кто это"},
KNOWN_DICTIONARY = {}

STOP_WORDS = {
    "這個世界", "又或者", "在絨球", "這些球", "這個球", "似乎是", "而且是",
    "在創造者", "琳的飛行者", "琳讓飛行者", "琳的絨球", "如果是", "不過是"
}

def extract_terms_comprehensive(text):
    all_terms = []
    p1 = re.compile(r'[\u4e00-\u9fa5]{2,8}(?:者|蟲|虫|獸|兽|怪|龍|龙|體|体|族|腦|脑|鳥|鸟|代|球|形|質|质|岩|器|脈|脉|門|门|核|靈|灵|島|岛|蛇|魚|鱼)')
    all_terms.extend(p1.findall(text))
    p2 = re.compile(r'[\u4e00-\u9fa5]{2,4}')
    all_terms.extend(p2.findall(text))
    p3 = re.compile(r'[「"]([\u4e00-\u9fa5]{2,10})[」"]')
    all_terms.extend(p3.findall(text))
    return all_terms

def build_glossary():
    if not os.path.exists(CHAPTERS_DIR):
        raise SystemExit("Нет папки %s — сначала скачай главы (scraper.py)." % CHAPTERS_DIR)

    chapter_files = sorted([f for f in os.listdir(CHAPTERS_DIR) if f.endswith('.txt')])
    term_counts = Counter()
    chapter_occurrences = Counter()

    for fname in chapter_files:
        filepath = os.path.join(CHAPTERS_DIR, fname)
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
                terms = extract_terms_comprehensive(content)
                unique_in_chap = set(terms)
                for t in terms:
                    term_counts[t] += 1
                for t in unique_in_chap:
                    chapter_occurrences[t] += 1
        except Exception:
            pass

    glossary_data = []
    seen_ru = set()

    for cn_term, meta in KNOWN_DICTIONARY.items():
        ru = meta["ru"]
        dedup_key = ru + "|" + meta["category"]
        if dedup_key in seen_ru:
            continue
        seen_ru.add(dedup_key)
        
        glossary_data.append({
            "cn": cn_term,
            "ru": ru,
            "en": meta.get("en", ""),
            "gender": meta.get("gender", "По контексту"),
            "category": meta["category"],
            "description": meta["desc"],
            "total_mentions": term_counts.get(cn_term, 0),
            "chapters_count": chapter_occurrences.get(cn_term, 0)
        })

    frequent_discovered = [
        (term, count) for term, count in term_counts.most_common(500)
        if term not in KNOWN_DICTIONARY and term not in STOP_WORDS
        and len(term) >= 2 and chapter_occurrences[term] >= 5 and count >= 10
        and not re.match(r'^(這個|那個|什麽|不是|已經|可以|因為|所以|但是|而且|或者|如果|雖然|然後|還是|就是|自己|沒有|知道|覺得|不過|能夠|為什|到底|只是|真的|也許|難道|只能)', term)
    ]

    for term, count in frequent_discovered:
        glossary_data.append({
            "cn": term,
            "ru": f"[ПЕРЕВОД_НУЖЕН: {term}]",
            "en": "",
            "gender": "По контексту сущности",
            "category": "Авто-обнаруженный термин",
            "description": f"Встречается {count} раз в {chapter_occurrences[term]} главах",
            "total_mentions": count,
            "chapters_count": chapter_occurrences[term]
        })

    # Save JSON (атомарно: это источник для common.load_glossary)
    save_json(GLOSSARY_JSON, {"pronoun_rules": PRONOUN_RULES, "glossary": glossary_data})

    # Save Markdown
    with open(GLOSSARY_MD, 'w', encoding='utf-8') as f:
        f.write("# 📖 Глоссарий и правила родов (Он / Она / Оно)\n\n")
        f.write(f"> **Обработано глав:** {len(chapter_files)}\n\n")
        f.write("---\n\n")

        f.write("## ⚖️ Правила согласования родов и местоимений (Он / Она / Оно)\n\n")
        f.write("| Сущность / Категория | Род и местоимения в русском | Контекстное правило |\n")
        f.write("|---|---|---|\n")
        for entity_name, rule_info in PRONOUN_RULES.items():
            f.write(f"| **{entity_name}** | `{rule_info['ru_gender']}` | {rule_info['rule_desc']} |\n")
        f.write("\n---\n\n")

        categories = defaultdict(list)
        for item in glossary_data:
            categories[item["category"]].append(item)

        for cat, items in categories.items():
            f.write(f"## {cat}\n\n")
            f.write("| Термин (Кит.) | Перевод (Рус.) | Род / Местоимение | English | Описание | Упоминания |\n")
            f.write("|---|---|---|---|---|---|\n")
            for item in items:
                mentions = f"{item['total_mentions']} ({item['chapters_count']} глав)"
                f.write(f"| **{item['cn']}** | {item['ru']} | `{item.get('gender', 'По контексту')}` | {item.get('en', '')} | {item['description']} | {mentions} |\n")
            f.write("\n")

    print(f"[+] Glossary & Pronoun Rules updated: {len(glossary_data)} terms saved in `{os.path.basename(GLOSSARY_MD)}` and `{os.path.basename(GLOSSARY_JSON)}`.")

if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args()
    build_glossary()
