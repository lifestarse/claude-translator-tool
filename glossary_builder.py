#!/usr/bin/env python3
"""
Ручной глоссарий тайтла и правила родов (Он / Она / Оно) -> glossary.json / glossary.md.

Всё, что лежит в KNOWN_DICTIONARY, common.load_glossary() читает как
выверенное вручную, и glossary_merge.py ставит это ПОВЕРХ вердиктов судей,
а glossary_extract.py исключает из кандидатов. Поэтому словарь тайтл-зависим
и держится пустым, пока человек не выверит записи именно для этой книги.
Словарь прошлой книги («进化的四十六亿重奏»: 琳→Линь, 虛空→Пустота, 眼球→…)
отсюда убран: 虛空 и 眼球 встречаются и в новой, и старые переводы молча уехали
бы в задания переводчику. Пустой словарь — рабочее состояние: в glossary.json
попадут только авто-обнаруженные n-граммы, а их load_glossary отбрасывает.

Конвейер этот скрипт не вызывает; запускать только руками.
"""

import argparse
import json
import os
import sys
import re
from collections import Counter, defaultdict

from lang import LANG
from common import save_json

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding='utf-8')

ROOT = os.path.dirname(os.path.abspath(__file__))
CHAPTERS_DIR = os.path.join(ROOT, "chapters")
GLOSSARY_JSON = os.path.join(ROOT, "glossary.json")
GLOSSARY_MD = os.path.join(ROOT, "glossary.md")
BOOK_FILE = os.path.join(ROOT, "BOOK.md")
CATALOG_FILE = os.path.join(ROOT, "catalog.json")

# ============================================================
# Правила согласования в роде (Он / Она / Оно) — заполняются под тайтл.
# Формат:
#   "Имя (原文)": {"ru_gender": "Женский род (Она / Её)",
#                  "rule_desc": "почему и в каких контекстах"},
# ============================================================
PRONOUN_RULES = {}

# Формат: {"Сущность (中文)": {"ru_gender": "Мужской род (Он / Его)",
#                               "rule_desc": "когда и почему"}}
# Заполняется вручную под текущий тайтл.
PRONOUN_RULES = {}

# Формат: {"中文": {"ru": "...", "en": "...", "gender": "Он / Его",
#                    "category": "...", "desc": "..."}}
# Только записи, выверенные человеком для ТЕКУЩЕГО тайтла (см. docstring).
KNOWN_DICTIONARY = {}

# Ложные «термины» авто-обнаружения, специфичные для тайтла.
STOP_WORDS = set()

def book_title():
    """Русское название из BOOK.md, иначе китайское; иначе заглушка."""
    try:
        text = open(BOOK_FILE, encoding='utf-8').read()
    except OSError:
        return "тайтл не задан (BOOK.md)"
    for key in ("Русское", "Оригинал"):
        m = re.search(r"^-\s*" + key + r"[^:]*:\s*(.+)$", text, re.M)
        if m and m.group(1).strip() and "SET-ME" not in m.group(1):
            return m.group(1).strip()
    return "тайтл не задан (BOOK.md)"

def catalog_size():
    """Сколько глав в каталоге; None, если каталога нет."""
    try:
        data = json.load(open(CATALOG_FILE, encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return len(data) if isinstance(data, list) else None

def extract_terms_comprehensive(text):
    if LANG.code != "zh":
        # Для остальных языков кандидатов даёт профиль языка (lang.py).
        return list(LANG.iter_candidates(text))
    all_terms = []
    p1 = re.compile(r'[\u4e00-\u9fa5]{2,8}(?:者|蟲|虫|獸|兽|怪|龍|龙|體|体|族|腦|脑|鳥|鸟|代|球|形|質|质|岩|器|脈|脉|門|门|核|靈|灵|島|岛|蛇|魚|鱼)')
    all_terms.extend(p1.findall(text))
    p2 = re.compile(r'[\u4e00-\u9fa5]{2,4}')
    all_terms.extend(p2.findall(text))
    p3 = re.compile(r'[「“"]([\u4e00-\u9fa5]{2,10})[」”"]')
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
        f.write(f"# 📖 Полный Глоссарий и Правила Родов (Он / Она / Оно): «{book_title()}»\n\n")
        total = catalog_size()
        f.write(f"> **Обработано глав:** {len(chapter_files)} / {total if total else '?'}\n\n")
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
