"""Как найти термин в оригинале и каноническое имя в переводе.

Общая часть glossary_audit.py и term_align.py. Правила выверены на аудите
«Охотника из другого мира» (гл. 263–586), где каждое из них однажды подвело:

* Ключ в оригинале. Латиница — по словам: «Knights’ Oath» и «Knights' Oath» —
  один ключ (апострофы любые), «Item Box» и «item box» — тоже (у многословного
  первая буква каждого слова любого регистра). Однословный ключ — строго как
  есть: «Create», «Anger», «Greed» — названия навыков, а «create», «anger» —
  обычные слова, и регистронезависимый поиск дал 74 ложных места на одном
  «Create». Письменности без пробелов и регистра (китайская, японская,
  корейская, тайская) ищутся подстрокой — корейские частицы прилипают к имени.

* Каноническое имя в переводе — цепочка основ в том же порядке. Перестановка
  слов не считается: «Рыцарская клятва» — не «Клятва Рыцаря», хотя основы
  обоих слов на месте. Основа учитывает падежи: прилагательное теряет окончание
  из двух букв (Корейская → Корейской), слово с беглой гласной — суффикс
  (Замок → Замка, камень → камни, Саженец → Саженца), остальное — конечную
  гласную (Клятва → Клятвы). Слово на согласную остаётся целым: «Санмок» должен
  отличаться от ошибочного «Сангмок».
"""

import os
import re

from common import CHAPTERS_DIR, RU_DIR, chapter_path, read_text, ru_path, split_paragraphs
from lang import LANG

ADJ_END = re.compile(r"(?:ый|ий|ой|ая|яя|ое|ее|ые|ие)$")
FLEETING = re.compile(r"(?:ок|ёк|ек|ец)$")
WORD = re.compile(r"[А-ЯЁа-яёA-Za-z0-9]+(?:-[А-ЯЁа-яёA-Za-z0-9]+)*")
GENDER = {"м": "Он / Его", "ж": "Она / Её", "ср": "Оно / Его", "мн": "Они / Их"}


def stem_of(word):
    """Основа русского слова для поиска в любом падеже."""
    low = word.lower()
    if len(word) <= 3:
        return word
    if len(word) >= 5 and ADJ_END.search(low):
        return word[:-2]
    if len(word) >= 5 and low.endswith(("ень", "ель")):
        return word[:-3]
    if len(word) >= 5 and FLEETING.search(low):
        return word[:-2]
    if low[-1] in "аяоеёьйыиую":
        return word[:-1]
    return word


def canon_rx(form):
    """Каноническое имя как цепочка основ; None — в имени нет слов."""
    form = re.sub(r"\s*\(.*?\)", "", form).split("/")[0].split(";")[0]
    words = WORD.findall(form)
    if not words:
        return None
    # Знаки, которые есть в самом имени («День, когда падёт…»), тоже разделители.
    extra = "".join(sorted(set(re.findall(r"[,.:;!?—–]", form))))
    sep = r"[\s«»\"„“()\[\]№#" + re.escape(extra) + r"-]+"
    return re.compile(r"(?<![\w-])" + sep.join(re.escape(stem_of(w)) + r"[\w-]*" for w in words), re.I)


def source_key_rx(cn):
    """Ключ глоссария в тексте оригинала (см. правила в начале модуля)."""
    if LANG.script_re is not None:
        return re.compile(re.escape(cn))
    words = cn.split()
    parts = []
    for w in words:
        pat = re.sub(r"['’]", "['’]", re.escape(w))
        if len(words) >= 2 and w[:1].isalpha():
            pat = "[%s%s]" % (w[0].upper(), w[0].lower()) + pat[1:]
        parts.append(pat)
    return re.compile(r"(?<![A-Za-z])" + r"\s+".join(parts) + r"(?![a-z])")


def translated_chapters(lo=None, hi=None):
    """Номера глав, у которых есть и оригинал, и перевод."""
    out = []
    if not os.path.isdir(RU_DIR):
        return out
    for name in os.listdir(RU_DIR):
        m = re.fullmatch(r"chapter_(\d+)\.txt", name)
        if not m:
            continue
        n = int(m.group(1))
        if (lo is None or n >= lo) and (hi is None or n <= hi) and os.path.exists(chapter_path(n)):
            out.append(n)
    return sorted(out)


def aligned(n):
    """(абзацы оригинала, абзацы перевода) или None, если 1:1 не держится."""
    src = split_paragraphs(read_text(chapter_path(n)))
    ru = split_paragraphs(read_text(ru_path(n)))
    return (src, ru) if len(src) == len(ru) else None


def gender_label(code, fallback=""):
    """«м» → «Он / Его»; уже развёрнутая форма и пустота проходят как есть."""
    return GENDER.get(code, code) or fallback


__all__ = ["stem_of", "canon_rx", "source_key_rx", "translated_chapters", "aligned", "gender_label",
           "CHAPTERS_DIR"]
