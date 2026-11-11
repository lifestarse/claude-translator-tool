#!/usr/bin/env python3
"""Strip paragraph markers from translations, repairing splits along the way.

Translators are asked to keep the [N] marker on every paragraph. That turns the
dominant failure mode into something mechanical: when an agent splits one source
paragraph in two, both halves carry the same number, so they can simply be
rejoined. Before numbering, a split was invisible until verify.py counted the
paragraphs, and the only remedy was re-translating the whole chapter.

    python finalize.py --dry-run
    python finalize.py
"""

import argparse
import os
import re
import sys

from common import (
    RU_DIR,
    chapter_path,
    read_text,
    ru_path,
    split_paragraphs,
    write_text,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

MARKER_RE = re.compile(r"^\s*\[(\d+)\]\s*")
# Заголовок обязан читаться как «Глава N: Название». Правило есть в задании
# переводчика, но 295 глав из 499 его нарушили: агенты тащили в перевод
# служебный префикс 《進化的四十六億重奏》, причём каждый переводил название книги
# по-своему — «Эволюция четырёх с половиной миллиардов сознаний», «Эволюция
# сорока шести сотен миллионов сознаний» и ещё десятки вариантов. Правилом в
# промпте это не лечится: слишком соблазнительно перевести первую строку
# целиком. Приведение к форме здесь надёжнее, потому что оно не зависит от
# того, послушался агент или нет.
# Разделитель после номера — не только двоеточие: попадаются «Глава 31 Конец»
# через пробел и «глава 92 — Боевые» через тире.
TITLE_RE = re.compile(
    r"^.*?глав[аы]\s*(\d+)(?:\s*[-–—]\s*(\d+))?\s*(?:[:.—–]+\s*|\s+)", re.I)
# Tool-call serialisation that leaked into three files of the first wave.
JUNK_RE = re.compile(r"</?(?:antml:)?(?:content|invoke|parameter|function_calls)\b[^>]*>")


def normalize_title(text):
    """(текст, менялось ли) — первый абзац к виду «Глава N: Название».

    Идемпотентно: у правильного заголовка совпадение снимает «Глава N: » и
    собирает его обратно тем же самым.
    """
    paras = split_paragraphs(text)
    if not paras:
        return text, False
    head = paras[0].strip()
    m = TITLE_RE.match(head)
    if not m:
        return text, False
    rest = head[m.end():].strip()
    if not rest:
        return text, False          # одно только «Глава N» — трогать нечего
    if m.group(2):
        fixed = "Главы %s-%s: %s" % (m.group(1), m.group(2), rest)
    else:
        fixed = "Глава %s: %s" % (m.group(1), rest)
    if fixed == head:
        return text, False
    paras[0] = fixed
    return "\n\n".join(paras), True


def finalize_one(idx):
    """Return (status, detail, text) without writing anything."""
    path = ru_path(idx)
    if not os.path.exists(path):
        return "skip", "нет перевода", None

    raw = read_text(path)
    paras = split_paragraphs(raw)
    # Агент иногда разделяет абзацы одним переводом строки вместо пустой
    # строки: тогда вся глава читается как один абзац [1] и бракуется как
    # «пропущены абзацы [2, 3, 4, 5, 6]». Если маркеров в начале строк заметно
    # больше, чем в начале абзацев, — разделяем по строкам с маркером.
    line_marks = len(re.findall(r"(?m)^[ \t]*\[\d+\][ \t]", raw))
    if line_marks > 3 and sum(1 for p in paras if MARKER_RE.match(p)) * 2 < line_marks:
        raw = re.sub(r"\n(?=[ \t]*\[\d+\][ \t])", "\n\n", raw)
        paras = split_paragraphs(raw)
    # Служебные маркеры стоят почти на каждом абзаце; единичные «[1] …» —
    # текст самой книги, и готовую главу с ними разбирать заново нельзя.
    if sum(1 for p in paras if MARKER_RE.match(p)) * 2 <= len(paras):
        return "skip", "без маркеров (старый формат)", None

    src_count = len(split_paragraphs(read_text(chapter_path(idx)).strip()))

    # Agents reliably number the body but sometimes leave the title bare --
    # it reads as a heading, not as a numbered fragment. A leading unmarked
    # paragraph can only be paragraph 1, but adopt it as such only when no
    # other paragraph claims 1, so a genuinely dropped title still fails loudly
    # instead of silently absorbing the real first paragraph.
    has_first = any(
        MARKER_RE.match(p) and int(MARKER_RE.match(p).group(1)) == 1
        for p in paras
    )

    merged, order, unmarked = {}, [], 0
    for p in paras:
        p = JUNK_RE.sub("", p).strip()
        if not p:
            continue
        m = MARKER_RE.match(p)
        if not m:
            # No number: attach to the previous paragraph rather than invent one.
            if order:
                merged[order[-1]] += " " + p
            elif not has_first:
                merged[1] = p
                order.append(1)
                has_first = True
            else:
                unmarked += 1
            continue
        n = int(m.group(1))
        body = p[m.end():].strip()
        if n in merged:
            merged[n] += " " + body  # the agent split this one -- rejoin it
        else:
            merged[n] = body
            order.append(n)

    if not order:
        return "fail", "маркеры есть, но ни один не разобрался", None

    rejoined = len(paras) - len(order)
    expected = list(range(1, src_count + 1))
    missing = [n for n in expected if n not in merged]
    extra = [n for n in order if n > src_count]

    text = "\n\n".join(merged[n] for n in sorted(merged))
    text, _ = normalize_title(text)
    if missing or extra:
        return "fail", "пропущены абзацы %s, лишние номера %s" % (missing[:5], extra[:5]), text
    if unmarked:
        # Абзац без номера перед первым маркером, когда [1] уже занят: куда его
        # приклеить, неизвестно, а молча выбросить — потерять текст.
        return "fail", "абзацев без маркера в начале главы: %d" % unmarked, text
    return "ok", "склеено разрывов: %d" % rejoined, text


def main():
    ap = argparse.ArgumentParser(description="Снятие маркеров и починка разрывов")
    ap.add_argument("--dry-run", action="store_true")
    # Диапазон нужен, когда драйвер работает параллельно: читать главу в момент,
    # когда её пишет агент, значит рискнуть записать обратно обрезанный файл.
    ap.add_argument("--range", type=int, nargs=2, metavar=("LO", "HI"))
    args = ap.parse_args()

    indexes = sorted(
        int(f[8:-4]) for f in os.listdir(RU_DIR)
        if f.startswith("chapter_") and f.endswith(".txt")
    )
    if args.range:
        lo, hi = args.range
        indexes = [i for i in indexes if lo <= i <= hi]

    ok = repaired = skipped = failed = titles = 0
    for idx in indexes:
        status, detail, text = finalize_one(idx)
        if status == "skip":
            # Уже финализированные главы маркеров не имеют, но заголовок у них
            # мог остаться кривым: приведение к форме появилось позже перевода.
            current = read_text(ru_path(idx))
            fixed, changed = normalize_title(current)
            if changed:
                titles += 1
                print("[загол] гл.%04d: %s" % (idx, split_paragraphs(fixed)[0][:60]))
                if not args.dry_run:
                    write_text(ru_path(idx), fixed + "\n")
            skipped += 1
            continue
        if status == "fail":
            failed += 1
            print("[БРАК] гл.%04d: %s" % (idx, detail))
            continue
        ok += 1
        if "склеено разрывов: 0" not in detail:
            repaired += 1
            print("[чинка] гл.%04d: %s" % (idx, detail))
        if not args.dry_run:
            write_text(ru_path(idx), text + "\n")

    print("")
    print("=== ФИНАЛИЗАЦИЯ ===")
    print("Обработано  : %d" % ok)
    print("Из них с починкой разрывов: %d" % repaired)
    print("Пропущено (без маркеров)  : %d" % skipped)
    print("Заголовков приведено к форме: %d" % titles)
    print("Брак        : %d" % failed)
    if args.dry_run:
        print("")
        print("(пробный прогон, файлы не изменены)")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
