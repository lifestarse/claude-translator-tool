#!/usr/bin/env python3
"""Авторская нумерация глав: том + номер, снятые с заголовков оригинала.

Единственный источник истины о том, «какая это глава по книге», в отличие от
индекса файла, который есть всего лишь позиция страницы в каталоге сайта.
Им пользуются и раскладка `split_by_author.py`, и выкладка `publish_ranobelib.py`,
чтобы папка на диске и главы на сайте не разъехались.

Три вещи, ради которых модуль вообще существует.

**Номер в заголовке пишется с опечатками.** Помимо честного `第216章` источник
отдаёт `第1把39章` (иероглиф внутри числа), `第106 尖嘯的結晶` (потерян 章),
`178章 召集` (потерян 第), `第357張` (張 вместо 章). Наивный шаблон `第(\\d+)章`
на таких заголовках молчит, и глава выпадает из нумерации вовсе — на переведённом
куске это 5 глав из 745.

**Одиночный сбитый номер чинится по соседям.** У файла 764 стоит `第57章` между
156 и 158 — потеряна первая цифра. Правка безопасна ровно тогда, когда следующий
файл продолжает ряд так, будто на месте сбитого стоит `предыдущий + 1`: тогда
предполагаемое значение подтверждается с двух сторон, а не угадывается.

**Настоящий повтор нумерации чинить нельзя.** Файлы 474–483 и 484–493 — это
двадцать разных глав с разными названиями, и обе десятки заявлены как 216–225.
Сдвинуть вторую десятку на 226–235 значит разойтись с самим источником, который
на файле 494 продолжает счёт с 226. Поэтому повтор не переписывается: первая по
порядку чтения глава держит целый номер, следующая получает дробный (216.1), и
обе попадают в книгу, в правильном порядке и без потерь.
"""

import os
import re

from common import (
    CHAPTERS_DIR,
    ROOT,
    chapter_path,
    load_json,
    read_text,
    split_paragraphs,
)

OVERRIDES = os.path.join(ROOT, "ranobelib_numbers.json")

# Порядок важен: сначала точный шаблон, потом всё более снисходительные.
# Первый сработавший и даёт номер.
NUM_PATTERNS = (
    # 第216章 / 第 3 章 / 第357張 (張 вместо 章 — опечатка источника)
    (re.compile(r"第\s*(\d+)\s*[章張]"), None),
    # 第1把39章 — иероглиф вклинился внутрь числа. Цифры склеиваются: 1+39=139.
    # Проверять после точного шаблона: иначе «第400章16章» прочиталось бы как 40016.
    (re.compile(r"第\s*(\d+)\D{1,2}(\d+)\s*[章張]"), "цифры разорваны иероглифом"),
    # 第106 尖嘯的結晶 — потерян 章.
    (re.compile(r"第\s*(\d+)"), "потерян знак 章"),
    # 178章 召集 — потерян 第.
    (re.compile(r"(?<![\d.])(\d+)\s*[章張]"), "потерян знак 第"),
)

# «第11章+12章» — одна страница каталога на две главы. Вторая часть идёт без 第,
# поэтому обычным шаблоном не ловится. Не заметить её нельзя: вторую главу
# источник хранит ещё и отдельным файлом, так что при наивной разметке её текст
# ушёл бы в книгу дважды.
MERGE_RE = re.compile(r"第\s*(\d+)\s*章\s*[+＋]\s*(\d+)\s*章")


def parse_number(head):
    """(номер, пометка) по заголовку главы. (None, причина) — номера нет."""
    for rx, note in NUM_PATTERNS:
        m = rx.search(head)
        if m:
            digits = "".join(g for g in m.groups() if g)
            return int(digits), note
    return None, "в заголовке нет номера главы"


def head_of(idx):
    return split_paragraphs(read_text(chapter_path(idx)).strip())[0]


def _numeric_key(label):
    """Ключ сортировки: '216' -> (216, 0), '216.1' -> (216, 1)."""
    base, _, frac = str(label).partition(".")
    return int(base), int(frac or 0)


def build(indexes=None):
    """Авторская нумерация всего корпуса.

    Возвращает словарь с полями:
      map        {индекс файла: (том, номер-строкой)}
      volumes    {том: [индексы по порядку чтения]}
      titles     {индекс: заголовок оригинала}
      unnumbered [(индекс, причина)] — номер снять не удалось
      repairs    [(индекс, было, стало, почему)]
      duplicates [(индекс, базовый номер, выданный номер, кто занял базовый)]
      covered    {индекс: владелец склейки} — текст уже входит в другую главу
      jumps      [(индекс, предыдущий, текущий)] — дырки в нумерации
    """
    if indexes is None:
        indexes = sorted(
            int(f[8:12]) for f in os.listdir(CHAPTERS_DIR)
            if f.startswith("chapter_") and f.endswith(".txt")
        )

    titles, raw = {}, {}
    unnumbered, notes = [], {}
    spans = {}
    for i in indexes:
        head = head_of(i)
        titles[i] = head
        n, note = parse_number(head)
        if n is None:
            unnumbered.append((i, note))
            continue
        raw[i] = n
        if note:
            notes[i] = note
        merged = MERGE_RE.search(head)
        if merged:
            spans[i] = (int(merged.group(1)), int(merged.group(2)))

    ordered = [i for i in indexes if i in raw]

    # --- том: счёт перезапускается с единицы на каждом томе ------------------
    vol_of, vol, prev = {}, 0, None
    for i in ordered:
        n = raw[i]
        if prev is None or (n == 1 and prev != 1):
            vol += 1
        vol_of[i] = vol
        prev = n

    # --- починка одиночных сбитых номеров -----------------------------------
    # Условие подтверждения с двух сторон: сосед слева даёт p, сосед справа
    # стоит на p+2. Тогда между ними обязано быть p+1, и другого варианта нет.
    # Блочный повтор (474-493) этого условия не проходит и остаётся как есть.
    repairs = []
    for pos, i in enumerate(ordered):
        if pos == 0 or pos + 1 >= len(ordered):
            continue
        left, right = ordered[pos - 1], ordered[pos + 1]
        if vol_of[left] != vol_of[i] or vol_of[right] != vol_of[i]:
            continue
        want = raw[left] + 1
        if raw[i] == want or raw[right] != want + 1:
            continue
        repairs.append((i, raw[i], want, "соседи %d и %d требуют номер %d"
                        % (left, right, want)))
        raw[i] = want

    # --- ручные поправки ----------------------------------------------------
    # Идут после автоматики и до разбора повторов: они для того и заведены,
    # чтобы разобранный вручную случай перестал считаться повтором.
    manual = (load_json(OVERRIDES, {}) or {}).get("overrides", {})
    for key, pair in manual.items():
        i = int(key)
        if i in raw:
            vol_of[i], raw[i] = int(pair[0]), int(pair[1])

    # --- склейки ------------------------------------------------------------
    covered = {}
    for owner, (lo, hi) in spans.items():
        for i in ordered:
            if i != owner and vol_of[i] == vol_of[owner] and lo <= raw[i] <= hi:
                covered[i] = owner

    # --- повторы номера -----------------------------------------------------
    # Первая по порядку чтения глава держит целый номер, остальные получают
    # дробный. Пара том-номер обязана указывать ровно на одну главу: иначе на
    # сайте две разные главы неотличимы, а это чинится только руками.
    taken, duplicates, mapping = {}, [], {}
    for i in ordered:
        if i in covered:
            continue
        key = (vol_of[i], raw[i])
        if key not in taken:
            taken[key] = i
            mapping[i] = (vol_of[i], str(raw[i]))
            continue
        k = 1
        while (vol_of[i], "%d.%d" % (raw[i], k)) in {(v, n) for v, n in mapping.values()}:
            k += 1
        label = "%d.%d" % (raw[i], k)
        mapping[i] = (vol_of[i], label)
        duplicates.append((i, raw[i], label, taken[key]))

    # --- дырки в нумерации --------------------------------------------------
    jumps, prev = [], None
    for i in ordered:
        if i in covered:
            continue
        if prev is not None and vol_of[i] == vol_of[prev] and raw[i] > raw[prev] + 1:
            jumps.append((i, raw[prev], raw[i]))
        prev = i

    volumes = {}
    for i in ordered:
        if i in mapping:
            volumes.setdefault(vol_of[i], []).append(i)

    # Склейки: {индекс файла-владельца: (первый номер, последний)}. Нужны тем,
    # кто показывает главу читателю: под номером 11 лежит текст глав 11 и 12, и
    # без пометки в названии книга выглядит так, будто двенадцатая потерялась.
    span_range = {owner: (lo, hi) for owner, (lo, hi) in spans.items() if owner in mapping}

    return {
        "map": mapping,
        "spans": span_range,
        "volumes": volumes,
        "titles": titles,
        "notes": notes,
        "unnumbered": unnumbered,
        "repairs": repairs,
        "duplicates": duplicates,
        "covered": covered,
        "jumps": jumps,
        "numeric_key": _numeric_key,
    }


def report(data):
    m = data["map"]
    vols = sorted(data["volumes"])
    print("Файлов размечено : %d, томов: %d" % (len(m), len(vols)))
    print("Номер не снят    : %d" % len(data["unnumbered"]))
    for i, why in data["unnumbered"][:10]:
        print("    гл.%04d — %s" % (i, why))
    print("Заголовок с опечаткой, номер восстановлен: %d" % len(data["notes"]))
    for i, note in list(data["notes"].items())[:10]:
        print("    гл.%04d — %s -> %s" % (i, note, m.get(i, ("?", "?"))[1]))
    print("Починено по соседям: %d" % len(data["repairs"]))
    for i, was, now, why in data["repairs"][:10]:
        print("    гл.%04d: %d -> %d (%s)" % (i, was, now, why))
    print("Повторов номера  : %d" % len(data["duplicates"]))
    for i, base, label, owner in data["duplicates"][:10]:
        print("    гл.%04d: номер %d занят гл.%04d, выдан %s" % (i, base, owner, label))
    print("Внутри склеек    : %d" % len(data["covered"]))
    for i, owner in list(data["covered"].items())[:10]:
        print("    гл.%04d входит в склейку гл.%04d" % (i, owner))
    print("Дырок в нумерации: %d" % len(data["jumps"]))


if __name__ == "__main__":
    report(build())
