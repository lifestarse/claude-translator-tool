#!/usr/bin/env python3
"""Импорт оригинала из файлов: txt, папка с txt или epub -> chapters/.

`scraper.py` писан под один сайт. Этот скрипт — вход в конвейер для любого
другого источника и любого языка: текст, который у тебя уже есть на диске,
раскладывается в тот же формат, что даёт скрейпер (chapters/chapter_NNNN.txt,
первая строка — заголовок, абзацы через пустую строку).

    python import_text.py book.txt             один файл, режется по заголовкам глав
    python import_text.py raw/                 папка: файл = глава, порядок по имени
    python import_text.py book.epub            epub: документ = глава, порядок книги
    python import_text.py book.txt --dry       показать разбивку, ничего не писать

Заголовок главы узнаётся по правилам языка оригинала из BOOK.md (lang.py):
«第12章», «12화», «Chapter 12», «Capítulo 12» и т. п. Если в заголовках нет
номеров, добавь --add-numbers: главы пронумеруются по порядку.
"""

import argparse
import html
import os
import posixpath
import re
import sys
import zipfile
from html.parser import HTMLParser

from lang import LANG
from common import CHAPTERS_DIR, chapter_path, clean_paragraphs, write_text

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

MAX_TITLE_LEN = 90
MIN_CHAPTER_CHARS = 200     # короче — обложка, оглавление, выходные данные


def decode(raw):
    for enc in ("utf-8-sig", "utf-16", "gb18030", "big5", "euc-kr", "shift_jis", "cp1251"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def paragraphs_of(text):
    """Абзацы: по пустым строкам, если они есть, иначе строка = абзац."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("　", " ")
    blocks = [b for b in re.split(r"\n\s*\n", text) if b.strip()]
    lines = [ln for ln in text.split("\n") if ln.strip()]
    # Пустых строк почти нет — значит абзац кончается переводом строки.
    if len(blocks) * 3 < len(lines):
        blocks = lines
    return [" ".join(b.split()) for b in blocks if b.strip()]


def is_heading(par):
    if len(par) > MAX_TITLE_LEN:
        return False
    n, note = LANG.parse_number(par)
    return n is not None and note is None


def split_single(text):
    """Один большой файл -> [(заголовок, [абзацы])] по заголовкам глав."""
    chapters, front = [], []
    for par in paragraphs_of(text):
        if is_heading(par):
            chapters.append((par, []))
        elif chapters:
            chapters[-1][1].append(par)
        else:
            front.append(par)
    return chapters, front


class _Text(HTMLParser):
    BLOCK = {"p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr",
             "blockquote", "section", "hr"}
    SKIP = {"script", "style", "head"}

    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.parts, self.buf, self.skip = [], [], 0

    def flush(self):
        s = " ".join("".join(self.buf).split())
        if s:
            self.parts.append(s)
        self.buf = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.flush()

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK:
            self.flush()

    def handle_data(self, data):
        if not self.skip:
            self.buf.append(data)


def html_paragraphs(markup):
    p = _Text()
    p.feed(markup)
    p.flush()
    return p.parts


def read_epub(path):
    """[(заголовок, [абзацы])] в порядке spine."""
    out = []
    with zipfile.ZipFile(path) as z:
        container = decode(z.read("META-INF/container.xml"))
        m = re.search(r'full-path="([^"]+)"', container)
        if not m:
            raise SystemExit("В epub нет ссылки на OPF (META-INF/container.xml).")
        opf_path = m.group(1)
        opf = decode(z.read(opf_path))
        base = posixpath.dirname(opf_path)
        items = {}
        for tag in re.findall(r"<item\b[^>]*>", opf):
            i = re.search(r'\bid="([^"]+)"', tag)
            h = re.search(r'\bhref="([^"]+)"', tag)
            if i and h:
                items[i.group(1)] = html.unescape(h.group(1))
        for ref in re.findall(r'<itemref\b[^>]*\bidref="([^"]+)"', opf):
            href = items.get(ref)
            if not href:
                continue
            name = posixpath.normpath(posixpath.join(base, href.split("#")[0]))
            try:
                pars = html_paragraphs(decode(z.read(name)))
            except KeyError:
                continue
            if pars:
                out.append((pars[0], pars[1:]))
    return out


def natural_key(name):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def read_dir(path):
    out = []
    for name in sorted(os.listdir(path), key=natural_key):
        full = os.path.join(path, name)
        if not os.path.isfile(full) or not name.lower().endswith((".txt", ".md")):
            continue
        with open(full, "rb") as f:
            pars = paragraphs_of(decode(f.read()))
        if pars:
            out.append((pars[0], pars[1:]))
    return out


def numbered(title, n):
    """Заголовок с номером в записи, которую понимает профиль языка."""
    if LANG.code == "zh":
        return "第%d章 %s" % (n, title)
    if LANG.code == "ja":
        return "第%d話 %s" % (n, title)
    if LANG.code == "ko":
        return "%d화 %s" % (n, title)
    return "%d. %s" % (n, title)


def main():
    ap = argparse.ArgumentParser(description="Импорт оригинала из txt / папки / epub")
    ap.add_argument("source", help="файл .txt, файл .epub или папка с .txt")
    ap.add_argument("--start", type=int, default=1, help="индекс первой главы (по умолчанию 1)")
    ap.add_argument("--add-numbers", action="store_true",
                    help="пронумеровать заголовки по порядку (если в них нет номеров)")
    ap.add_argument("--keep-short", action="store_true",
                    help="не отбрасывать куски короче %d знаков" % MIN_CHAPTER_CHARS)
    ap.add_argument("--overwrite", action="store_true", help="перезаписывать существующие главы")
    ap.add_argument("--dry", action="store_true", help="показать разбивку, ничего не писать")
    args = ap.parse_args()

    src = args.source
    front = []
    if os.path.isdir(src):
        chapters = read_dir(src)
    elif src.lower().endswith(".epub"):
        chapters = read_epub(src)
    elif os.path.isfile(src):
        with open(src, "rb") as f:
            chapters, front = split_single(decode(f.read()))
        if not chapters:
            raise SystemExit(
                "В файле не нашлось ни одного заголовка главы для языка «%s» (%s).\n"
                "Проверь «Язык оригинала» в BOOK.md, либо разрежь текст на файлы "
                "по главам и подай папку." % (LANG.name, LANG.code))
    else:
        raise SystemExit("Нет такого файла или папки: %s" % src)

    if not args.keep_short:
        kept = [(t, b) for t, b in chapters
                if sum(len(p) for p in b) >= MIN_CHAPTER_CHARS]
        dropped = len(chapters) - len(kept)
        if dropped:
            print("[i] Отброшено коротких кусков (обложка, оглавление): %d "
                  "(оставить: --keep-short)" % dropped)
        chapters = kept
    if not chapters:
        raise SystemExit("Нечего импортировать.")

    unnumbered = sum(1 for t, _ in chapters if LANG.parse_number(t)[0] is None)
    if args.add_numbers:
        chapters = [(numbered(t, n) if LANG.parse_number(t)[0] is None else t, b)
                    for n, (t, b) in enumerate(chapters, 1)]
    elif unnumbered:
        print("[!] Заголовков без номера главы: %d из %d. Без номера глава не "
              "получит места в книге (тома, выкладка, epub). Запусти с --add-numbers."
              % (unnumbered, len(chapters)))
    if front:
        print("[i] Текст до первого заголовка (%d абз.) пропущен: «%s…»"
              % (len(front), front[0][:50]))

    written = skipped = 0
    for idx, (title, body) in enumerate(chapters, args.start):
        body = clean_paragraphs(body)
        chars = sum(len(p) for p in body)
        if args.dry:
            if written < 15 or idx == args.start + len(chapters) - 1:
                print("  %04d | %5d зн. | %3d абз. | %s" % (idx, chars, len(body), title[:60]))
            elif written == 15:
                print("  ...")
            written += 1
            continue
        path = chapter_path(idx)
        if os.path.exists(path) and not args.overwrite:
            skipped += 1
            continue
        write_text(path, "\n\n".join([title] + body) + "\n")
        written += 1

    if args.dry:
        print("Пробный прогон: глав %d, на диск ничего не записано." % written)
    else:
        os.makedirs(CHAPTERS_DIR, exist_ok=True)
        print("Записано глав: %d -> chapters/%s" % (
            written, " (уже были, пропущено: %d; перезаписать: --overwrite)" % skipped
            if skipped else ""))
        print("Дальше: python build_batches.py")


if __name__ == "__main__":
    main()
