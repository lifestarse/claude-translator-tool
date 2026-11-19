"""Режим письма: общие пути, замысел из BOOK.md, состояние истории, приёмка главы.

В этом режиме исходника нет: модель сама пишет книгу по замыслу. Главы ложатся
в тот же ru/chapter_NNNN.txt, что и перевод, поэтому сборка EPUB работает как есть.

Состояние истории — в story/:
    outline.json    арки и планы глав (пишет планировщик, write_plan.py)
    bible.json      персонажи, места, понятия — чтобы имена и факты не разъезжались
    summaries.json  краткое содержание каждой написанной главы
    digests.json    итоги дописанных арок и томов по тексту и открытые линии
                    (пишет летописец) — память книги на длинной дистанции

Уровни плана: книга -> тома (только у длинных книг) -> арки -> блоки глав.
Арки планируются на один том, главы — на один блок, и каждый раз по
фактическим итогам уже написанного, поэтому размер любого задания не растёт
с длиной книги.
"""

import os
import re

from common import ROOT, STRAY_TAG_RE, load_json, read_text, ru_path, save_json, split_paragraphs
from setup_book import BOOK_MD, read_book

STORY_DIR = os.path.join(ROOT, "story")
TASK_DIR = os.path.join(ROOT, "write_tasks")
OUTLINE_FILE = os.path.join(STORY_DIR, "outline.json")
BIBLE_FILE = os.path.join(STORY_DIR, "bible.json")
SUMMARIES_FILE = os.path.join(STORY_DIR, "summaries.json")
DIGESTS_FILE = os.path.join(STORY_DIR, "digests.json")

# Модели по ролям. Выбраны слепым сравнением 13 моделей на двух главах
# (write_compare.py): писатель делает основной объём, и здесь решает цена
# главы; план читает вся книга дальше, поэтому планировщик — самая сильная;
# редактор и летописец должны быть не слабее писателя.
WRITER_MODEL = "claude-haiku-5-5"
PLANNER_MODEL = "claude-fable-5-2"
EDITOR_MODEL = "claude-opus-5-5"
CHRONICLER_MODEL = "claude-opus-5-5"

DEFAULT_TOTAL = 100          # глав в книге, если в BOOK.md не указано
DEFAULT_CHARS = 9000         # знаков в главе
DEFAULT_BLOCK = 10           # глав в одном блоке плана
DEFAULT_VOLUME_FROM = 300    # книга от стольких глав делится на тома
MAX_THREADS = 40             # открытых линий в памяти книги
MIN_RATIO, MAX_RATIO = 0.6, 1.7     # допуск объёма главы к целевому
MIN_PARAS = 8
RECENT_SUMMARIES = 30        # столько последних глав уходит в задание пересказом
BIBLE_LIMIT = 180            # записей библии в одном задании
TAIL_CHARS = 1800            # хвост предыдущей главы — чтобы начать с того же места

TITLE_RE = re.compile(r"^Глава (\d+): \S.*$")
MARKDOWN_RE = re.compile(r"^(?:#{1,6} |\*\*[^*]+\*\*$|```|---+$|\* |- \S)", re.M)
# Служебные хвосты и «голос модели» вместо прозы.
META_RE = re.compile(
    r"конец главы|продолжение следует|как (?:ии|языковая модель|искусственный интеллект)"
    r"|^примечание автора|^\(?(?:заметки|примечания) к главе", re.I | re.M)
ENDINGS = (".", "!", "?", "…", "»", "”", "\"", ")", "]", "—")


def rel(path):
    return os.path.relpath(path, ROOT).replace("\\", "/")


def task_path(idx):
    return os.path.join(TASK_DIR, "task_%04d.md" % idx)


def notes_path(idx):
    return os.path.join(STORY_DIR, "notes_%04d.json" % idx)


def review_path(idx):
    return os.path.join(STORY_DIR, "review_%04d.json" % idx)


def read_concept():
    """Замысел книги из BOOK.md: название, BRIEF, секция «Замысел» и её числа."""
    meta = read_book()
    book = open(BOOK_MD, encoding="utf-8").read()
    m = re.search(r"^## Замысел[^\n]*\n(.*?)(?=^## |\Z)", book, re.S | re.M)
    section = re.sub(r"<!--.*?-->", "", m.group(1), flags=re.S).strip() if m else ""

    def number(label, default):
        mm = re.search(r"^-\s*%s[^:\n]*:[ \t]*(\d+)" % label, section, re.M)
        return int(mm.group(1)) if mm else default

    text = "\n".join(line for line in section.splitlines()
                     if not re.match(r"^-\s*(Всего глав|Объём главы|Глав в блоке|Делить на тома)", line)).strip()
    if "SET-ME" in text:
        text = ""
    return {
        "title": meta["title"],
        "brief": meta["brief"],
        "text": text,
        "total": number("Всего глав", DEFAULT_TOTAL),
        "chars": number("Объём главы", DEFAULT_CHARS),
        "block": number("Глав в блоке", DEFAULT_BLOCK),
        "volume_from": number("Делить на тома", DEFAULT_VOLUME_FROM),
    }


def require_concept():
    c = read_concept()
    if not c["title"] or not c["brief"]:
        raise SystemExit("Заполни в BOOK.md «Русское» название и BRIEF — без них писать не о чем.")
    if not c["text"]:
        raise SystemExit("Заполни в BOOK.md секцию «## Замысел»: мир, герои, конфликт, "
                         "куда идёт история и чем кончается.")
    return c


def concept_block(c):
    return "# Книга: «%s»\n\n%s\n\n## Замысел автора\n\n%s\n" % (c["title"], c["brief"], c["text"])


def load_outline():
    data = load_json(OUTLINE_FILE, None) or {}
    data.setdefault("volumes", [])
    data.setdefault("arcs", [])
    data.setdefault("chapters", {})
    return data


def load_bible():
    return load_json(BIBLE_FILE, None) or {}


def load_summaries():
    return load_json(SUMMARIES_FILE, None) or {}


def written(idx, summaries=None):
    """Глава готова: текст на диске и её итог учтён в истории."""
    summaries = load_summaries() if summaries is None else summaries
    return os.path.exists(ru_path(idx)) and str(idx) in summaries


def arc_of(outline, idx):
    for a in outline["arcs"]:
        if a["from"] <= idx <= a["to"]:
            return a
    return None


def volume_of(outline, idx):
    for v in outline["volumes"]:
        if v["from"] <= idx <= v["to"]:
            return v
    return None


def load_digests():
    data = load_json(DIGESTS_FILE, None) or {}
    data.setdefault("arcs", {})
    data.setdefault("volumes", {})
    data.setdefault("threads", [])
    return data


def _span_line(kind, item, digests, current):
    """Строка карты: у дописанного — итог по тексту, у остального — план."""
    done = digests.get(str(item["n"]))
    if done:
        mark, text = " (написано, итог по тексту)", done
    else:
        mark = " ← сейчас" if current and item["n"] == current["n"] else " (план)"
        text = item["summary"]
    return "- %s %d «%s», гл. %d–%d%s: %s" % (
        kind, item["n"], item["title"], item["from"], item["to"], mark, text)


def story_map(outline, digests=None, idx=None):
    """Карта книги для задания. У длинной книги — все тома одной строкой и арки
    только текущего тома: арки чужих томов в задание не идут, иначе оно росло бы
    вместе с книгой. idx=None — вся карта целиком (для --show)."""
    digests = digests or load_digests()
    vol = volume_of(outline, idx) if idx else None
    arc = arc_of(outline, idx) if idx else None
    lines = []
    if outline["volumes"]:
        lines.append("Тома книги:")
        lines += [_span_line("Том", v, digests["volumes"], vol) for v in outline["volumes"]]
        lines += ["", "Арки текущего тома:" if vol else "Арки:"]
    arcs = [a for a in outline["arcs"]
            if not vol or vol["from"] <= a["from"] <= vol["to"]]
    lines += [_span_line("Арка", a, digests["arcs"], arc) for a in arcs]
    if not arcs:
        lines.append("(арки ещё не спланированы)")
    return "\n".join(lines)


def threads_block(digests=None):
    digests = digests or load_digests()
    return "\n".join("- %s" % t for t in digests["threads"]) \
        or "(пока нет — ни одна арка ещё не дописана)"


def bible_block(bible, idx=None, focus=""):
    """Записи библии для задания. Большая библия режется: всё, что названо в
    плане главы, плюс недавно встречавшееся."""
    names = list(bible)
    if len(names) > BIBLE_LIMIT:
        def rank(name):
            e = bible[name]
            return (name in focus, e.get("last", e.get("first", 0)))
        names = sorted(names, key=rank, reverse=True)[:BIBLE_LIMIT]
    names.sort(key=lambda n: (bible[n].get("kind", ""), n))
    lines = []
    for n in names:
        e = bible[n]
        gender = (" [%s]" % e["gender"]) if e.get("gender") else ""
        lines.append("- **%s** (%s)%s — %s" % (n, e.get("kind", "?"), gender, e.get("desc", "")))
    return "\n".join(lines) or "(пока пусто — это начало книги)"


def summaries_block(summaries, upto, recent=RECENT_SUMMARIES):
    """Пересказ последних глав до `upto` (не включая). Раньше — только арки."""
    lo = max(1, upto - recent)
    lines = ["- Гл. %d: %s" % (i, summaries[str(i)])
             for i in range(lo, upto) if str(i) in summaries]
    return "\n".join(lines) or "(написанных глав ещё нет)"


def chapter_tail(idx):
    """Конец главы idx целыми абзацами, не длиннее TAIL_CHARS."""
    if not os.path.exists(ru_path(idx)):
        return ""
    tail, size = [], 0
    for p in reversed(split_paragraphs(read_text(ru_path(idx)))[1:]):
        if tail and size + len(p) > TAIL_CHARS:
            break
        tail.append(p)
        size += len(p)
    return "\n\n".join(reversed(tail))


def validate_chapter(idx, target_chars, path=None):
    """Формальная приёмка написанной главы. Пустой список — принята.

    path — проверить текст не из ru/ (сравнение моделей, write_compare.py)."""
    path = path or ru_path(idx)
    if not os.path.exists(path):
        return ["файл главы не записан"]
    text = read_text(path).strip()
    paras = split_paragraphs(text)
    if not paras:
        return ["файл главы пуст"]
    problems = []
    m = TITLE_RE.match(paras[0])
    if not m or "\n" in paras[0]:
        problems.append("первая строка не в формате «Глава %d: Название»" % idx)
    elif int(m.group(1)) != idx:
        problems.append("в заголовке номер %s, а глава %d" % (m.group(1), idx))
    body = paras[1:]
    size = sum(len(p) for p in body)
    lo, hi = int(target_chars * MIN_RATIO), int(target_chars * MAX_RATIO)
    if size < lo:
        problems.append("глава слишком короткая: %d знаков при норме от %d" % (size, lo))
    if size > hi:
        problems.append("глава слишком длинная: %d знаков при норме до %d" % (size, hi))
    if len(body) < MIN_PARAS:
        problems.append("абзацев всего %d — это не глава, а конспект" % len(body))
    if STRAY_TAG_RE.search(text):
        problems.append("в тексте служебные теги вызова инструмента")
    if MARKDOWN_RE.search("\n".join(body)):
        problems.append("в тексте markdown-разметка (заголовки, списки, жирный)")
    m = META_RE.search("\n".join(body))
    if m:
        problems.append("служебная фраза вместо прозы: «%s»" % m.group(0).strip())
    if body and not body[-1].rstrip().endswith(ENDINGS):
        problems.append("глава оборвана на полуслове")
    if len(set(body)) < len(body) * 0.9:
        problems.append("абзацы повторяются — текст зациклился")
    return problems


def validate_notes(idx):
    """Заметки писателя после главы. -> (заметки или None, проблемы)."""
    data = load_json(notes_path(idx), None)
    if not isinstance(data, dict):
        return None, ["заметки %s не записаны или не JSON" % rel(notes_path(idx))]
    summary = data.get("summary")
    if not isinstance(summary, str) or len(summary.strip()) < 40:
        return None, ["в заметках нет внятного summary"]
    for key in ("new", "changed"):
        items = data.get(key) or []
        if not isinstance(items, list) or any(
                not isinstance(e, dict) or not str(e.get("name") or "").strip() for e in items):
            return None, ["поле %s в заметках — не список записей с name" % key]
        data[key] = items
    return data, []


def apply_notes(idx, notes):
    """Занести итог главы в историю: пересказ и библию."""
    summaries = load_summaries()
    summaries[str(idx)] = " ".join(notes["summary"].split())
    save_json(SUMMARIES_FILE, summaries)

    bible = load_bible()
    for e in notes["new"]:
        name = str(e["name"]).strip()
        entry = bible.setdefault(name, {"first": idx})
        for key in ("kind", "gender", "desc"):
            if str(e.get(key) or "").strip():
                entry.setdefault(key, str(e[key]).strip())
    for e in notes["changed"]:
        name = str(e["name"]).strip()
        if name in bible and str(e.get("desc") or "").strip():
            bible[name]["desc"] = str(e["desc"]).strip()
    text = read_text(ru_path(idx))
    for name, entry in bible.items():
        if name in text:
            entry["last"] = idx
    save_json(BIBLE_FILE, bible)
