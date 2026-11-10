#!/usr/bin/env python3
"""Финальная сквозная проверка корпуса: главы с первой до последней.

Собирает в один прогон всё, чем корпус за проект проверялся по частям:
структурную приёмку verify.py, остатки семи спам-семейств, соответствие
терминологическому канону, авторскую нумерацию, технический мусор и
дубликаты тел. Ничего не чинит — только меряет и докладывает; починка
остаётся конвейерам, у каждого из которых своя приёмка.

    python final_check.py               # полный прогон
    python final_check.py --detail      # полные списки нарушителей
    python final_check.py --skip-terms  # без скана канона (быстро)

Полный отчёт со всеми списками пишется в final_check_report.json.
"""

import argparse
import hashlib
import os
import re
import sys

from common import (
    CHAPTERS_DIR,
    ROOT,
    RU_DIR,
    SPAM_RE,
    SPAM_SPAN_RE,
    chapter_path,
    load_excluded,
    load_json,
    read_text,
    ru_path,
    split_paragraphs,
    validate_translation,
)
from termfix import load_fixes, scan
import author_numbers  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPORT = os.path.join(ROOT, "final_check_report.json")

# Заголовок готовой главы: «Глава 216: …», «Глава 216.1: …» либо служебный.
# Номер в заголовке обязан совпадать с авторским номером из volume_map.json —
# это единственная сверка, связывающая текст главы с тем, под каким номером
# она уйдёт на сайт и в книгу.
TITLE_RE = re.compile(r"^Глава\s+(\d+(?:\.\d+)?)\s*[:：]")
TITLE_SPECIAL_RE = re.compile(
    r"^(Пролог|Эпилог|Интерлюдия|Экстра|Послесловие|Новогоднее письмо|Слово автора)\b")

# Седьмое спам-семейство (обломки джаваскрипта) — единственное, способное
# пережить перевод: латиница с «();» не ловится ни проверкой CJK, ни счётом
# абзацев. Гл. 2299/2300 доказали, что переводчик его сохраняет как есть.
JS_JUNK_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\(\);?|\(\);")


def indexes_in(dirname):
    out = set()
    if not os.path.isdir(dirname):
        return out
    for name in os.listdir(dirname):
        if name.startswith("chapter_") and name.endswith(".txt"):
            out.add(int(name[8:-4]))
    return out


def numbers_equal(title_num, map_num):
    try:
        return float(title_num) == float(map_num)
    except (TypeError, ValueError):
        return str(title_num) == str(map_num)


def glossary_probes():
    """Зонды присутствия по glossary_v2: (cn, ru, [основы слов]).

    Проверка мягкая, по основам: где в исходнике стоит китайский термин, в
    переводе обязана найтись основа каждого значимого слова канона. Ловит
    дрейф имени целиком («Пожиратель» переписан «Глотающим» — основа исчезла
    из всех глав арки), а склонения и порядок слов ей безразличны. Основа —
    слово минус три буквы: русские окончания с суффиксом редко длиннее.
    """
    data = load_json(os.path.join(ROOT, "glossary_v2.json"), {})
    probes = []
    for e in data.get("glossary", []):
        cn, ru = (e.get("cn") or "").strip(), (e.get("ru") or "").strip()
        if not cn or not ru or e.get("proper") is False or e.get("probe") is False:
            continue
        # ё нормализуется («Отверждение» должно находить «отверждённую»),
        # дефисные имена рвутся на части («Разум-дух» склоняется в середине:
        # «Разумов-духов» — основа-префикс с дефисом его не находит).
        words = re.findall(r"[А-Яа-яЁё]{3,}", ru.replace("ё", "е").replace("Ё", "Е"))
        stems = [w.lower()[: max(3, len(w) - 3)] for w in words]
        if stems:
            probes.append((cn, ru, stems))
    # Правило суперстроки: 眼球 не проверяется там, где стоит 眼球觸須 — у
    # составного термина своя запись со своим именем («Глазное щупальце»),
    # и требовать вдобавок «глазное яблоко» значит ловить сам себя.
    cns = [p[0] for p in probes]
    supers = {cn: [c for c in cns if c != cn and cn in c] for cn in cns}
    return [(cn, ru, stems, supers[cn]) for cn, ru, stems in probes]


def main():
    ap = argparse.ArgumentParser(description="Финальная сквозная проверка корпуса")
    ap.add_argument("--detail", action="store_true")
    ap.add_argument("--skip-terms", action="store_true")
    ap.add_argument("--skip-glossary", action="store_true")
    args = ap.parse_args()

    excluded = load_excluded()
    src_idx = indexes_in(CHAPTERS_DIR)
    ru_idx = indexes_in(RU_DIR)
    n_max = max(src_idx | ru_idx)
    # Разметку считаем сами, а не читаем снимок volume_map.json: его пишет
    # только publish_ranobelib, и до первой выкладки его попросту нет.
    numbering = author_numbers.build()
    vmap = {str(k): [v[0], v[1]] for k, v in numbering["map"].items()}
    covered = {str(k) for k in numbering.get("covered", {})}
    dup_claimed = {i: str(b) for i, b, _l, _o in numbering.get("duplicates", [])}
    fixes = None if args.skip_terms else load_fixes()
    glossary = [] if args.skip_glossary else glossary_probes()

    fails, warns = {}, {}

    def fail(cat, msg):
        fails.setdefault(cat, []).append(msg)

    def warn(cat, msg):
        warns.setdefault(cat, []).append(msg)

    # --- Полнота: каждый номер от 1 до последнего закрыт или исключён ---
    for idx in range(1, n_max + 1):
        if idx not in src_idx:
            fail("полнота", "гл.%04d: нет исходника в chapters/" % idx)
        elif idx in excluded:
            if idx in ru_idx:
                fail("полнота", "гл.%04d: переведена, хотя исключена (%s)"
                     % (idx, excluded[idx]))
        elif idx not in ru_idx:
            fail("полнота", "гл.%04d: нет перевода в ru/" % idx)
    for idx in sorted(ru_idx - src_idx):
        fail("полнота", "гл.%04d: перевод без исходника — выдуман целиком" % idx)

    # --- Поглавный проход ---
    checked = 0
    hashes = {}
    gloss_miss = {}
    for idx in sorted(src_idx & ru_idx):
        if idx in excluded:
            continue
        src = read_text(chapter_path(idx))
        ru = read_text(ru_path(idx))
        checked += 1

        for p in validate_translation(src, ru):
            fail("структура", "гл.%04d: %s" % (idx, p))

        # Спам в исходнике сам по себе в книгу не попадает (книга собирается
        # из ru/), но означает, что его перевод мог осесть в ru — глядеть руками.
        if SPAM_SPAN_RE.search(src) or SPAM_RE.search(src):
            warn("спам в источнике", "гл.%04d" % idx)
        m = JS_JUNK_RE.search(ru)
        if m:
            fail("спам в переводе", "гл.%04d: «%s»" % (idx, m.group(0)))

        if "\r" in ru:
            fail("технический мусор", "гл.%04d: CRLF-переводы строк" % idx)
        if "﻿" in ru:
            fail("технический мусор", "гл.%04d: BOM" % idx)

        paras = split_paragraphs(ru)
        head = paras[0] if paras else ""
        m = TITLE_RE.match(head)
        if m:
            label = (vmap.get(str(idx)) or [None, None])[1]
            if label is None and str(idx) in covered:
                pass    # вторая половина склейки: номер несёт глава-владелец
            elif label is None:
                fail("нумерация", "гл.%04d: нет авторского номера (author_numbers)" % idx)
            elif not numbers_equal(m.group(1), label):
                # Авторские повторы нумерации: в карте дробный номер (216.1),
                # в заголовке — авторский целый. Решение пользователя
                # 02.08.2026: заголовки повторов не переписывать.
                claimed = dup_claimed.get(idx)
                if claimed is None or not numbers_equal(m.group(1), claimed):
                    fail("нумерация", "гл.%04d: заголовок «Глава %s», в карте %s"
                         % (idx, m.group(1), label))
        elif TITLE_SPECIAL_RE.match(head):
            pass
        else:
            warn("заголовок", "гл.%04d: «%s»" % (idx, head[:60]))

        if fixes:
            found = scan(ru, fixes, idx)
            if found:
                hits = "; ".join("%s ← %s" % (f["canon"], ", ".join(h[:3]))
                                 for f, h in found)
                fail("канон терминов", "гл.%04d: %s" % (idx, hits))

        if glossary:
            low = ru.lower().replace("ё", "е")
            for cn, ru_name, stems, supers in glossary:
                if cn in src:
                    if any(sup in src for sup in supers):
                        continue
                    hit_or_miss = gloss_miss.setdefault((cn, ru_name), ([], []))
                    if all(s in low for s in stems):
                        hit_or_miss[0].append(idx)
                    else:
                        hit_or_miss[1].append(idx)

        hashes.setdefault(hashlib.md5(ru.encode("utf-8")).hexdigest(), []).append(idx)

    # Побайтовые дубликаты тел — так была поймана пара 3701/3702 в источнике.
    for group in hashes.values():
        if len(group) > 1:
            fail("дубликаты", "главы %s — тела побайтово совпадают" % group)

    # Дрейф имён против глоссария — предупреждение, не провал: проверка по
    # основам мягкая, а термин в переводе может законно стоять местоимением.
    # Показываются записи, где имя ЖИВЁТ в своей арке (доля промахов <= 0.8),
    # но пропало из заметной части глав — класс «Пожиратель, переписанный
    # Глотающим». Имя, отсутствующее почти везде, — признак того, что
    # китайский термин — бытовое слово (凝固 «застывать»), а не имя: такие
    # уходят только в JSON-отчёт.
    gloss_report = {}
    for (cn, ru_name), (hits, misses) in sorted(
            gloss_miss.items(), key=lambda kv: -len(kv[1][1])):
        m, h = len(misses), len(hits)
        gloss_report["%s (%s)" % (ru_name, cn)] = {"hits": h, "misses": misses}
        if m < 3 or m / max(m + h, 1) > 0.8:
            continue
        warn("глоссарий", "«%s» (%s): нет в %d из %d гл.: %s%s"
             % (ru_name, cn, m, m + h, misses[:8], "…" if m > 8 else ""))

    # --- Сводка ---
    n_fail = sum(len(v) for v in fails.values())
    n_warn = sum(len(v) for v in warns.values())
    print("")
    print("=== ФИНАЛЬНАЯ ПРОВЕРКА ===")
    print("Последний номер главы:   %d" % n_max)
    print("Исключено:               %d" % len(excluded))
    print("Проверено пар CN-RU:     %d" % checked)
    print("Канон терминов:          %s" % ("пропущен (--skip-terms)" if args.skip_terms
                                           else "%d правил" % len(fixes)))
    print("Глоссарий:               %s" % ("пропущен (--skip-glossary)" if args.skip_glossary
                                           else "%d зондов присутствия" % len(glossary)))
    print("ПРОВАЛОВ:                %d" % n_fail)
    print("Предупреждений:          %d" % n_warn)

    cap = None if args.detail else 20
    for title, bucket in (("ПРОВАЛЫ", fails), ("ПРЕДУПРЕЖДЕНИЯ", warns)):
        if not bucket:
            continue
        print("")
        print("=== %s ===" % title)
        for cat in sorted(bucket):
            items = bucket[cat]
            print("[%s] — %d" % (cat, len(items)))
            for msg in items[:cap]:
                print("    %s" % msg)
            if cap and len(items) > cap:
                print("    ... ещё %d (полностью — в отчёте или с --detail)"
                      % (len(items) - cap))

    from common import save_json
    save_json(REPORT, {
        "last_index": n_max,
        "checked_pairs": checked,
        "excluded": sorted(excluded),
        "fails": fails,
        "warns": warns,
        "glossary": gloss_report,
    })
    print("")
    print("Отчёт: final_check_report.json")
    if not n_fail:
        print("КОРПУС ЧИСТ — к сдаче готов.")
    raise SystemExit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
