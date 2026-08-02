#!/usr/bin/env python3
"""
Comprehensive Glossary & Pronoun Rules Builder for "4.6 Billion Year Symphony of Evolution" (进化的四十六亿重奏)
Includes Contextual Pronoun Rules (Он / Она / Оно) for seamless Russian translation.
"""

import os
import sys
import json
import re
from collections import Counter, defaultdict

sys.stdout.reconfigure(encoding='utf-8')

CHAPTERS_DIR = "chapters"
GLOSSARY_JSON = "glossary.json"
GLOSSARY_MD = "glossary.md"

# ============================================================
# GRAMMATICAL PRONOUN & GENDER AGREEMENT RULES (Он / Она / Оно)
# ============================================================

PRONOUN_RULES = {
    "Линь (琳)": {
        "ru_gender": "Женский род (Она / Её) / Средний род (Оно — Сознание)",
        "rule_desc": "В тексте Линь согласуется в женском роде («Линь сделала», «Её разум»), так как имя задано девочкой Шалинь. При описании как коллективного разума допустим средний род («Сознание Линь развивалось»)."
    },
    "Шалинь (莎琳)": {
        "ru_gender": "Женский род (Она / Её)",
        "rule_desc": "Загадочная девочка-создатель."
    },
    "Юниты и существа на -тель / -ник / -чик (Летун, Кислотник, Дробитель, Левиафан)": {
        "ru_gender": "Мужской род (Он / Его)",
        "rule_desc": "Примеры: Летун (Он), Кислотник (Он), Дробитель (Он), Левиафан (Он), Снайпер (Он)."
    },
    "Клетки и биологические структуры (Стволовая клетка, Амёба, Мембрана)": {
        "ru_gender": "Женский род (Она / Её)",
        "rule_desc": "Примеры: Стволовая клетка (Она), Амёба (Она), Базовая пластина (Она), Мембрана (Она)."
    },
    "Органы и элементы управления (Ядро, Глазное яблоко, Сознание, Тело)": {
        "ru_gender": "Средний род (Оно / Его)",
        "rule_desc": "Примеры: Клеточное ядро (Оно), Сознание (Оно), Тело (Оно), Существо (Оно)."
    },
    "Расы драконов и разумных созданий (Изумрудный дракон, Скелетный дракон)": {
        "ru_gender": "Мужской род (Он / Его) для индивида, Множественное (Они) для вида",
        "rule_desc": "Лидеры драконов Сусуми (Он) и Так (Он) всегда согласуются в мужском роде."
    }
}

KNOWN_DICTIONARY = {
    # Core Characters
    "琳": {"ru": "Линь", "en": "Lin", "gender": "Она / Её", "category": "Главный герой", "desc": "Коллективный разум, эволюционирующий от клетки до космической цивилизации"},
    "莎琳": {"ru": "Шалинь", "en": "Shalin", "gender": "Она / Её", "category": "Персонаж / Создатель", "desc": "Загадочное существо (девочка-лоли), создавшее Линь"},
    "蘇蘇米": {"ru": "Сусуми", "en": "Susumi", "gender": "Он / Его", "category": "Персонаж", "desc": "Лидер изумрудных драконов"},
    "苏苏米": {"ru": "Сусуми", "en": "Susumi", "gender": "Он / Его", "category": "Персонаж", "desc": "Лидер изумрудных драконов (упрощ.)"},
    "塔克": {"ru": "Так", "en": "Tak/Tucker", "gender": "Он / Его", "category": "Персонаж", "desc": "Вождь серых скелетных драконов"},
    "瑪雅": {"ru": "Майя", "en": "Maya", "gender": "Он / Его", "category": "Персонаж", "desc": "Лидер мозговых червей"},
    "玛雅": {"ru": "Майя", "en": "Maya", "gender": "Он / Его", "category": "Персонаж", "desc": "Лидер мозговых червей (упрощ.)"},
    "姆克圖": {"ru": "Мукту", "en": "Muktu", "gender": "Он / Его", "category": "Персонаж", "desc": "Мозговой червь, осторожный и мудрый"},
    "姆克图": {"ru": "Мукту", "en": "Muktu", "gender": "Он / Его", "category": "Персонаж", "desc": "Мозговой червь (упрощ.)"},
    "塔蘭圖拉": {"ru": "Тарантула", "en": "Tarantula", "gender": "Он / Его", "category": "Персонаж", "desc": "Учёный из расы Пустотников"},

    # Lin's Units
    "飛行者": {"ru": "Летун", "en": "Flyer", "gender": "Он / Его", "category": "Юнит Линь", "desc": "Летающий разведчик/боевой юнит"},
    "飞行者": {"ru": "Летун", "en": "Flyer", "gender": "Он / Его", "category": "Юнит Линь", "desc": "Летающий юнит (упрощ.)"},
    "注酸者": {"ru": "Кислотник", "en": "Acid Injector", "gender": "Он / Его", "category": "Юнит Линь", "desc": "Юнит, впрыскивающий кислоту"},
    "粉碎者": {"ru": "Дробитель", "en": "Crusher", "gender": "Он / Его", "category": "Юнит Линь", "desc": "Крупный юнит для разрушения"},
    "爆裂者": {"ru": "Взрывник", "en": "Exploder", "gender": "Он / Его", "category": "Юнит Линь", "desc": "Самоподрывающийся юнит"},
    "浮游者": {"ru": "Плавун", "en": "Floater", "gender": "Он / Его", "category": "Юнит Линь", "desc": "Плавающий/парящий юнит"},
    "隱形者": {"ru": "Невидимка", "en": "Stealth Unit", "gender": "Он / Его", "category": "Юнит Линь", "desc": "Маскирующийся юнит"},
    "利齒者": {"ru": "Зубастик", "en": "Tooth Unit", "gender": "Он / Его", "category": "Юнит Линь", "desc": "Юнит с прочными зубами"},
    "轟炸者": {"ru": "Бомбардировщик", "en": "Bomber", "gender": "Он / Его", "category": "Юнит Линь", "desc": "Юнит для дальних атак"},
    "運輸者": {"ru": "Транспортник", "en": "Transporter", "gender": "Он / Его", "category": "Юнит Линь", "desc": "Грузовой юнит"},
    "收割者": {"ru": "Жнец", "en": "Reaper", "gender": "Он / Его", "category": "Юнит Линь", "desc": "Юнит для сбора ресурсов"},
    "觀察者": {"ru": "Наблюдатель", "en": "Observer", "gender": "Он / Его", "category": "Юнит Линь", "desc": "Разведывательный юнит"},
    "吞噬者": {"ru": "Пожиратель", "en": "Devourer", "gender": "Он / Его", "category": "Юнит Линь", "desc": "Поглощающий юнит"},
    "噬菌者": {"ru": "Бактериофаг", "en": "Bacteriophage", "gender": "Он / Его", "category": "Юнит Линь", "desc": "Юнит против бактерий"},
    "狙擊者": {"ru": "Снайпер", "en": "Sniper", "gender": "Он / Его", "category": "Юнит Линь", "desc": "Юнит для точечных атак"},
    "刺殺者": {"ru": "Убийца", "en": "Assassin", "gender": "Он / Его", "category": "Юнит Линь", "desc": "Юнит для скрытных атак"},
    "割岩者": {"ru": "Камнерез", "en": "Rock Cutter", "gender": "Он / Его", "category": "Юнит Линь", "desc": "Юнит для работы с камнем"},
    "暢遊者": {"ru": "Пловец", "en": "Swimmer", "gender": "Он / Его", "category": "Юнит Линь", "desc": "Подводный юнит"},
    "探索者": {"ru": "Разведчик", "en": "Explorer", "gender": "Он / Его", "category": "Юнит Линь", "desc": "Юнит для исследований"},
    "獵食者": {"ru": "Хищник", "en": "Predator", "gender": "Он / Его", "category": "Юнит Линь", "desc": "Охотничий юнит"},
    "裂地者": {"ru": "Землеруб", "en": "Ground Splitter", "gender": "Он / Его", "category": "Юнит Линь", "desc": "Юнит для раскалывания пород"},
    "開拓者": {"ru": "Пионер", "en": "Pioneer", "gender": "Он / Его", "category": "Юнит Линь", "desc": "Юнит-первопроходец"},
    "星測者": {"ru": "Звездомер", "en": "Star Measurer", "gender": "Он / Его", "category": "Юнит Линь", "desc": "Юнит для навигации"},
    "預言者": {"ru": "Пророк", "en": "Prophet", "gender": "Он / Его", "category": "Юнит Линь", "desc": "Юнит с предвидением"},
    "靈魂追尋者": {"ru": "Искатель душ", "en": "Soul Seeker", "gender": "Он / Его", "category": "Юнит Линь", "desc": "Юнит обнаружения разума"},

    # Biological Structures & Organs
    "幹細胞": {"ru": "Стволовая клетка", "en": "Stem Cell", "gender": "Она / Её", "category": "Биология", "desc": "Базовая клетка"},
    "細胞核": {"ru": "Клеточное ядро", "en": "Cell Nucleus", "gender": "Оно / Его", "category": "Биология", "desc": "Ядро клетки"},
    "細胞膜": {"ru": "Клеточная мембрана", "en": "Cell Membrane", "gender": "Она / Её", "category": "Биология", "desc": "Внешняя оболочка"},
    "基盤": {"ru": "Базовая пластина", "en": "Base Plate", "gender": "Она / Её", "category": "Биология", "desc": "Опорная структура"},
    "錐形針": {"ru": "Конусный шип", "en": "Cone Needle", "gender": "Он / Его", "category": "Орган / Оружие", "desc": "Атакующий орган"},
    "利維坦": {"ru": "Левиафан", "en": "Leviathan", "gender": "Он / Его", "category": "Юнит Линь / Корабль", "desc": "Космический био-корабль"},
    "眼球": {"ru": "Глазное яблоко", "en": "Eyeball", "gender": "Оно / Его", "category": "Орган", "desc": "Зрительный орган"},

    # Species & Nations
    "翡翠龍": {"ru": "Изумрудный дракон", "en": "Emerald Dragon", "gender": "Он / Его (индивид) / Они (вид)", "category": "Вид / Цивилизация", "desc": "Разумные рептилии"},
    "白骸龍": {"ru": "Белый скелетный дракон", "en": "White Dragon", "gender": "Он / Его", "category": "Вид", "desc": "Подвид драконов"},
    "灰骸龍": {"ru": "Серый скелетный дракон", "en": "Grey Dragon", "gender": "Он / Его", "category": "Вид", "desc": "Подвид драконов"},
    "腦袋蟲": {"ru": "Мозговой червь", "en": "Brain Worm", "gender": "Он / Его", "category": "Вид / Колония", "desc": "Черви с развитым мозгом"},
    "腦怪": {"ru": "Мозговик", "en": "Brain Monster", "gender": "Он / Его", "category": "Вид", "desc": "Существа с большим мозгом"},
    "阿米巴蟲": {"ru": "Амёба", "en": "Amoeba", "gender": "Она / Её", "category": "Организм", "desc": "Одноклеточный хищник"},
    "虛民": {"ru": "Пустотник", "en": "Void Citizen", "gender": "Он / Его (индивид)", "category": "Цивилизация", "desc": "Звёздная цивилизация"},
    "腦靈": {"ru": "Разум-дух", "en": "Brain Spirit", "gender": "Он / Его", "category": "Вид / Сущность", "desc": "Нематериальное существо"},
    "創造者": {"ru": "Создатель", "en": "Creator", "gender": "Он / Его", "category": "Древнее существо", "desc": "Гигантское древнее существо"},
    "阿特蘭": {"ru": "Атлан", "en": "Atlan", "gender": "Он / Его", "category": "Цивилизация", "desc": "Гуманоидная раса"},
    "蒸騰者": {"ru": "Испаритель", "en": "Vaporizer", "gender": "Он / Его", "category": "Космическая цивилизация", "desc": "Космическая фракция"},

    # Locations
    "爾什": {"ru": "Эрш", "en": "Ersh", "gender": "Она / Её (планета)", "category": "Планета / Живое существо", "desc": "Живая планета"},
    "絨球": {"ru": "Пушистый Шар", "en": "Fluffball", "gender": "Он / Его (шар)", "category": "Планета", "desc": "Родная планета Линь"},
    "瑪爾斯": {"ru": "Марс (Малс)", "en": "Mars", "gender": "Он / Его", "category": "Планета / Локация", "desc": "Планета Атланов"},
    "虛空": {"ru": "Пустота", "en": "Void", "gender": "Она / Её", "category": "Космос / Концепт", "desc": "Межзвёздное пространство"},
    "凝固虛空": {"ru": "Застывшая Пустота", "en": "Solidified Void", "gender": "Она / Её", "category": "Измерение", "desc": "Параллельное пространство"},
    "虛空巴士": {"ru": "Пустотный Автобус", "en": "Void Bus", "gender": "Он / Его", "category": "Гигантское существо", "desc": "Космические существа-перевозчики"}
}

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
        return

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

    # Save JSON
    with open(GLOSSARY_JSON, 'w', encoding='utf-8') as f:
        json.dump({"pronoun_rules": PRONOUN_RULES, "glossary": glossary_data}, f, ensure_ascii=False, indent=2)

    # Save Markdown
    with open(GLOSSARY_MD, 'w', encoding='utf-8') as f:
        f.write("# 📖 Полный Глоссарий и Правила Родов (Он / Она / Оно): «Симфония Эволюции 4.6 Млрд Лет»\n\n")
        f.write(f"> **Обработано глав:** {len(chapter_files)} / 4885\n\n")
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

    print(f"[+] Glossary & Pronoun Rules updated: {len(glossary_data)} terms saved in `{GLOSSARY_MD}` and `{GLOSSARY_JSON}`.")

if __name__ == "__main__":
    build_glossary()
