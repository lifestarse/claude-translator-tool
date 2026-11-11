#!/usr/bin/env python3
"""Глоссарий названий глав: название арки в оригинале -> одно русское название.

Арка — главы с одинаковым названием в оригинале, у которых меняется только номер
части в скобках: «폭식 사태», «폭식 사태 (2)», … Без этого глоссария каждая часть
получает своё название: «Приступ обжорства», «Инцидент с обжорством (2)»,
«Обжорный кризис (3)». Файл titles_glossary.json можно править руками: поле «ru»
записи — решение, которое побеждает всё остальное.

    python tools/titles_glossary.py build           дополнить файл арками из готовых глав
    python tools/titles_glossary.py check           заголовки против глоссария названий
                                                    и терминов основного глоссария
    python tools/titles_glossary.py check --apply   переписать заголовки по глоссарию названий

Тот же файл читает build_batches.py: задание на главу получает готовую первую строку,
а для новой арки — термины основного глоссария, которые стоят в её названии.
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from common import (  # noqa: E402
    CHAPTERS_DIR, chapter_path, load_json, marker_count, read_text, ru_path, split_paragraphs, write_text,
)

FILE = os.path.join(ROOT, "titles_glossary.json")
GLOSSARY_V2 = os.path.join(ROOT, "glossary_v2.json")
PART_RE = re.compile(r"^(.*?)\s*[(（]\s*(\d+)\s*[)）]\s*$")
# Номер главы в начале заголовка оригинала на распространённых языках.
NUM_PREFIX_RE = re.compile(
    r"^\s*(?:第\s*\d+\s*[章話话回節节]|\d+\s*(?:화|장|회)|"
    r"(?:chapter|chap\.?|ch\.|episode|ep\.?|cap[ií]tulo|chapitre|kapitel|rozdział|bölüm|chương|"
    r"глава|розділ)\s*\d+|\d+)\s*[.:：、)\]\-–—]?\s*", re.I)
RU_TITLE_RE = re.compile(r"^Глав[аы]\s+[\d\-–]+\s*:\s*(.*)$")


def chapter_indexes():
    return sorted(int(f[8:-4]) for f in os.listdir(CHAPTERS_DIR)
                  if f.startswith("chapter_") and f.endswith(".txt"))


def source_title(n):
    """(ключ арки, название в оригинале без номера части, номер части) или (None, None, None)."""
    if not os.path.exists(chapter_path(n)):
        return None, None, None
    line = read_text(chapter_path(n)).split("\n", 1)[0].strip()
    t = NUM_PREFIX_RE.sub("", line, count=1).strip()
    m = PART_RE.match(t)
    base, part = (m.group(1).strip(), int(m.group(2))) if m else (t, None)
    base = base.strip(" -—–")
    if not base:
        return None, None, None
    key = base.replace("’", "'").strip(" .")
    key = re.sub(r"^(?:the|a|an)\s+(?=\w\w)", "", key, flags=re.I).lower()
    return key, base, part


def ru_title(n):
    head = split_paragraphs(read_text(ru_path(n)))[0]
    m = RU_TITLE_RE.match(head)
    t = (m.group(1) if m else head).strip()
    pm = PART_RE.match(t)
    return head, (pm.group(1).strip() if pm else t)


def accepted():
    """Главы с готовым переводом: без служебных маркеров и с тем же числом абзацев."""
    out = []
    for n in chapter_indexes():
        if not os.path.exists(ru_path(n)):
            continue
        src, ru = split_paragraphs(read_text(chapter_path(n))), split_paragraphs(read_text(ru_path(n)))
        if len(src) == len(ru) and marker_count(ru) <= marker_count(src):
            out.append(n)
    return out


def load():
    return load_json(FILE, {"titles": {}}) or {"titles": {}}


def save(data):
    write_text(FILE, json.dumps(data, ensure_ascii=False, indent=1) + "\n")


def lookup(n, data=None):
    """Каноническая первая строка перевода главы n или None, если арка ещё не названа."""
    key, base, part = source_title(n)
    if not key:
        return None
    entry = (data or load())["titles"].get(key)
    if not entry or not entry.get("ru"):
        return None
    return "Глава %d: %s%s" % (n, entry["ru"], " (%d)" % part if part else "")


def build(quiet=False):
    data = load()
    added = 0
    for n in accepted():
        key, base, part = source_title(n)
        if not key:
            continue
        entry = data["titles"].get(key)
        if entry is None:
            _, name = ru_title(n)
            data["titles"][key] = {"src": base, "ru": name, "chapters": [n]}
            added += 1
        elif n not in entry["chapters"]:
            entry["chapters"].append(n)
    save(data)
    if not quiet:
        print("Арок в глоссарии названий: %d (новых: %d) -> %s" % (len(data["titles"]), added, os.path.basename(FILE)))


def stems(ru):
    """Основы слов канона: «Город-подражатель» -> ['гор', 'подражат']."""
    return [w.lower()[:max(3, len(w) - 2)] for w in re.findall(r"[А-Яа-яЁё]{3,}", ru)]


def check(apply=False):
    data = load()
    terms = [(e.get("cn", "").strip(), e.get("ru", "").strip())
             for e in (load_json(GLOSSARY_V2, {}) or {}).get("glossary", [])]
    terms = [(cn, ru) for cn, ru in terms if len(cn) >= 2 and ru and "[" not in ru]
    wrong, issues = [], {}
    for n in accepted():
        head, _ = ru_title(n)
        want = lookup(n, data)
        if want and head != want:
            wrong.append((n, head, want))
            if apply:
                paras = split_paragraphs(read_text(ru_path(n)))
                paras[0] = want
                write_text(ru_path(n), "\n\n".join(paras) + "\n")
        _, base, _ = source_title(n)
        low = (want or head).lower()
        for cn, ru in terms:
            if base and cn in base and not all(s in low for s in stems(ru)):
                issues.setdefault((base, cn), (n, ru, want or head))
    for n, head, want in wrong:
        print("гл.%d: «%s» -> «%s»%s" % (n, head, want, "" if apply else " (не применено)"))
    for (base, cn), (n, ru, head) in issues.items():
        print("термин в названии: гл.%d «%s» содержит %s = «%s», а заголовок «%s»" % (n, base, cn, ru, head))
    print("Заголовков не по глоссарию названий: %d%s; терминов основного глоссария не по канону: %d"
          % (len(wrong), " (исправлено)" if apply else "", len(issues)))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Глоссарий названий глав")
    ap.add_argument("cmd", choices=("build", "check"))
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    build() if a.cmd == "build" else check(a.apply)
