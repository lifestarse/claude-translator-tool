#!/usr/bin/env python3
"""Build EPUB ebook from translated Russian chapters (ru/chapter_XXXX.txt)."""

import os
import glob
import re
import html
import uuid
import zipfile
from datetime import datetime

import author_numbers
from finalize import TITLE_RE

# Названия сведены с карточкой на RanobeLIB слово в слово. Расходиться им нельзя:
# читатель скачивает файл со страницы тайтла, и другое название на обложке книги
# выглядит как чужая книга. Здесь стояло «4,6 миллиарда лет эволюции» — без
# «симфонии» и с запятой вместо точки, — а в подзаголовке «4.6 Billion Years of
# Symphony» вместо «4.6 Billion Year Symphony of Evolution».
# Значения штампует setup_book.py из BOOK.md.
BOOK_TITLE = "{{BOOK_TITLE}}"
BOOK_SUBTITLE = "{{BOOK_ORIG}} · {{BOOK_EN}}"
# Написание автора сводить с карточкой на RanobeLIB слово в слово.
BOOK_AUTHOR = "{{BOOK_AUTHOR}}"
BOOK_LANG = "ru"
BOOK_ID = f"urn:uuid:{uuid.uuid4()}"
ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_EPUB_PATTERN = os.path.join(ROOT, "book_ru_{min_idx}-{max_idx}.epub")
# Обложка: положи cover.jpg рядом со скриптами. Нет файла — книга без обложки.
COVER_IMAGE_PATH = os.path.join(ROOT, "cover.jpg")

RU_DIR = os.path.join(ROOT, "ru")

CSS_CONTENT = """@charset "utf-8";
body {
    font-family: serif;
    margin: 5% 5%;
    line-height: 1.6;
    text-align: justify;
}
h1, h2, h3 {
    text-align: center;
    font-weight: bold;
    margin-top: 1.5em;
    margin-bottom: 1em;
    line-height: 1.3;
}
p {
    text-indent: 1.5em;
    margin-top: 0;
    margin-bottom: 0.6em;
}
p.divider, hr {
    text-align: center;
    text-indent: 0;
    margin: 1.5em 0;
    border: none;
    font-weight: bold;
}
.titlepage {
    text-align: center;
    margin-top: 15%;
}
.book-title {
    font-size: 2.2em;
    font-weight: bold;
    margin-bottom: 0.4em;
    line-height: 1.2;
}
.book-subtitle {
    font-size: 1.2em;
    font-style: italic;
    color: #555;
    margin-bottom: 2em;
}
.book-author {
    font-size: 1.2em;
    font-weight: bold;
    margin-bottom: 1.5em;
}
.book-meta {
    font-size: 0.95em;
    color: #666;
    margin-top: 3em;
    line-height: 1.6;
}
"""

CONTAINER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

def clean_chapter_title(raw_title, chapter_num):
    """Заголовок главы для оглавления и шапки: «Глава N: Название».

    Прежнее выражение `^[«《].*?[»»\\]\\s,]*` снимало только открывающую кавычку:
    `.*?` ленивый, а класс после него допускает ноль повторений, поэтому совпадал
    один-единственный символ «. В оглавлении оставалось «Эволюция сорока шести
    миллиардов ритмов». Глава 238» — с висящей закрывающей кавычкой.

    Разбор отдан тому же выражению, что чинит заголовки в finalize.py: правило
    должно быть одно, иначе сборка и файлы на диске разъедутся.
    """
    t = raw_title.strip()
    m = TITLE_RE.match(t)
    if m:
        rest = t[m.end():].strip()
        if rest:
            if m.group(2):
                return f"Главы {m.group(1)}-{m.group(2)}: {rest}"
            return f"Глава {m.group(1)}: {rest}"
    return t or f"Глава {chapter_num}"

def strip_chapter_prefix(title):
    """«Глава 44: Путь» -> «Путь». Номер приставляется свой, авторский.

    Оставлять оба нельзя: в заголовке из файла стоит номер страницы источника,
    и рядом с настоящим номером главы получалось бы «Том 4, глава 216. Глава
    474: После войны» — два разных числа про одну главу.
    """
    m = re.match(r"^\s*глав[аы]\s*\d+(?:\s*[-–—]\s*\d+)?\s*[:.]\s*", title, re.I)
    return title[m.end():].strip() if m else title.strip()


def parse_chapter_file(filepath, chapter_num):
    """Parse chapter text file into title and list of paragraphs."""
    with open(filepath, "r", encoding="utf-8") as f:
        text = f.read()

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        return f"Глава {chapter_num}", []

    raw_title = paragraphs[0]
    title = clean_chapter_title(raw_title, chapter_num)
    body_paras = paragraphs[1:]
    
    # Снимался первый абзац текста, если он начинался со слова «Глава» и был
    # короче 60 символов. Проверка на дубликат заголовка при этом отсутствовала,
    # а результат никуда не записывался (title_extra не использовался) — то есть
    # абзац просто исчезал из книги. Число абзацев в файлах сверено с оригиналом
    # построчно, так что молча терять один здесь нельзя. Убирается только точный
    # повтор заголовка.
    if body_paras and body_paras[0].strip() == raw_title.strip():
        body_paras = body_paras[1:]

    return title, body_paras

def build_epub(max_chapter=None):
    files = sorted(glob.glob(os.path.join(RU_DIR, "chapter_*.txt")))
    # Нумерация берётся из author_numbers — той же, по которой разложена папка
    # ru_by_author и выложены главы на сайте. Считать её здесь заново значило бы
    # завести третий вариант правды: имя файла — это позиция страницы в каталоге
    # источника, а не номер главы.
    marks = author_numbers.build()
    mapping, covered = marks["map"], marks["covered"]

    chapters_data = []
    skipped = []
    for fpath in files:
        fname = os.path.basename(fpath)
        idx = int(fname.replace("chapter_", "").replace(".txt", ""))
        if max_chapter is not None and idx > max_chapter:
            continue
        # Глава, целиком лежащая внутри чужой склейки, — её второй кусок,
        # сохранённый источником ещё и отдельно. В книгу она не идёт: иначе один
        # и тот же текст встанет двумя главами подряд.
        if idx in covered:
            skipped.append((idx, f"текст входит в склейку гл.{covered[idx]:04d}"))
            continue
        if idx not in mapping:
            skipped.append((idx, "номер главы не снят с заголовка"))
            continue
        vol, num = mapping[idx]
        title, body_paras = parse_chapter_file(fpath, idx)
        chapters_data.append({
            "index": idx,
            "volume": vol,
            "number": num,
            "filename": f"chapter_{idx:04d}.xhtml",
            "id": f"chapter_{idx:04d}",
            "title": f"Том {vol}, глава {num}. {strip_chapter_prefix(title)}",
            "paragraphs": body_paras
        })

    print(f"Processing {len(chapters_data)} chapter files (out of {len(files)} available in {RU_DIR}/)")
    for idx, why in skipped:
        print(f"  пропущена гл.{idx:04d}: {why}")

    if not chapters_data:
        print("No chapters found!")
        return

    min_idx = chapters_data[0]["index"]
    max_idx = chapters_data[-1]["index"]
    output_epub = DEFAULT_EPUB_PATTERN.format(min_idx=min_idx, max_idx=max_idx)

    # Prepare Zip archive
    with zipfile.ZipFile(output_epub, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1. mimetype MUST be first and uncompressed
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)

        # 2. META-INF/container.xml
        zf.writestr("META-INF/container.xml", CONTAINER_XML)

        # 3. OEBPS/style.css
        zf.writestr("OEBPS/style.css", CSS_CONTENT)

        # Check if cover image exists
        has_cover = os.path.exists(COVER_IMAGE_PATH)
        if has_cover:
            print(f"Including cover image from {COVER_IMAGE_PATH}")
            with open(COVER_IMAGE_PATH, "rb") as cf:
                zf.writestr("OEBPS/cover.jpg", cf.read())

            cover_xhtml = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="ru" lang="ru">
<head>
  <title>Обложка</title>
  <style type="text/css">
    body { margin: 0; padding: 0; text-align: center; background-color: #000; }
    div.cover { height: 100vh; text-align: center; display: flex; justify-content: center; align-items: center; }
    img.cover { max-width: 100%; max-height: 100%; object-fit: contain; }
  </style>
</head>
<body>
  <div class="cover">
    <img class="cover" src="cover.jpg" alt="Обложка"/>
  </div>
</body>
</html>
"""
            zf.writestr("OEBPS/cover.xhtml", cover_xhtml)

        # 4. OEBPS/title.xhtml
        title_xhtml = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="ru" lang="ru">
<head>
  <title>{html.escape(BOOK_TITLE)}</title>
  <link rel="stylesheet" type="text/css" href="style.css"/>
</head>
<body>
  <div class="titlepage">
    <div class="book-title">{html.escape(BOOK_TITLE)}</div>
    <div class="book-subtitle">{html.escape(BOOK_SUBTITLE)}</div>
    <div class="book-author">Автор: {html.escape(BOOK_AUTHOR)}</div>
    <div class="book-meta">
      <p class="no-indent">Русский перевод (Главы {min_idx}–{max_idx})</p>
      <p class="no-indent">Всего глав в данном томе: {len(chapters_data)}</p>
      <p class="no-indent">Дата сборки: {datetime.now().strftime('%d.%m.%Y')}</p>
    </div>
  </div>
</body>
</html>
"""
        zf.writestr("OEBPS/title.xhtml", title_xhtml)

        # 5. Chapter XHTML files
        for ch in chapters_data:
            paras_html = []
            for p in ch["paragraphs"]:
                p_escaped = html.escape(p)
                if p_escaped == "* * *" or p_escaped == "***":
                    paras_html.append('<p class="divider">* * *</p>')
                else:
                    # Convert single newline inside paragraph to <br/> if any
                    p_formatted = p_escaped.replace("\n", "<br/>")
                    paras_html.append(f"<p>{p_formatted}</p>")

            body_content = "\n  ".join(paras_html)
            ch_xhtml = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="ru" lang="ru">
<head>
  <title>{html.escape(ch['title'])}</title>
  <link rel="stylesheet" type="text/css" href="style.css"/>
</head>
<body>
  <h1>{html.escape(ch['title'])}</h1>
  {body_content}
</body>
</html>
"""
            zf.writestr(f"OEBPS/{ch['filename']}", ch_xhtml)

        # 6. OEBPS/nav.xhtml (EPUB 3 Navigation)
        # Плоским списком на восемь томов пользоваться нельзя: в этой книге том
        # начинается с главы 1, и без группировки оглавление выглядит так, будто
        # нумерация сбрасывается посреди книги без причины.
        nav_lines = []
        current_vol = None
        for ch in chapters_data:
            if ch["volume"] != current_vol:
                if current_vol is not None:
                    nav_lines.append("</ol></li>")
                current_vol = ch["volume"]
                nav_lines.append(f'<li><span>Том {current_vol}</span><ol>')
            nav_lines.append(
                f'<li><a href="{ch["filename"]}">Глава {ch["number"]}. '
                f'{html.escape(strip_chapter_prefix(ch["title"].split(". ", 1)[-1]))}</a></li>')
        if current_vol is not None:
            nav_lines.append("</ol></li>")
        nav_items = "\n      ".join(nav_lines)
        nav_xhtml = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="ru" lang="ru">
<head>
  <title>Оглавление</title>
  <link rel="stylesheet" type="text/css" href="style.css"/>
</head>
<body>
  <nav epub:type="toc" id="toc">
    <h1>Оглавление</h1>
    <ol>
      {nav_items}
    </ol>
  </nav>
</body>
</html>
"""
        zf.writestr("OEBPS/nav.xhtml", nav_xhtml)

        # 7. OEBPS/toc.ncx (EPUB 2 NCX)
        ncx_points = []
        for i, ch in enumerate(chapters_data, start=1):
            ncx_points.append(f"""    <navPoint id="navPoint-{i}" playOrder="{i}">
      <navLabel><text>{html.escape(ch['title'])}</text></navLabel>
      <content src="{ch['filename']}"/>
    </navPoint>""")
        ncx_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head>
    <meta name="dtb:uid" content="{BOOK_ID}"/>
    <meta name="dtb:depth" content="1"/>
    <meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/>
  </head>
  <docTitle><text>{html.escape(BOOK_TITLE)}</text></docTitle>
  <navMap>
{chr(10).join(ncx_points)}
  </navMap>
</ncx>
"""
        zf.writestr("OEBPS/toc.ncx", ncx_content)

        # 8. OEBPS/content.opf
        manifest_items = []
        spine_items = []

        if has_cover:
            manifest_items.append('<item id="cover-image" href="cover.jpg" media-type="image/jpeg" properties="cover-image"/>')
            manifest_items.append('<item id="coverpage" href="cover.xhtml" media-type="application/xhtml+xml"/>')
            spine_items.append('<itemref idref="coverpage"/>')

        manifest_items.extend([
            '<item id="style" href="style.css" media-type="text/css"/>',
            '<item id="titlepage" href="title.xhtml" media-type="application/xhtml+xml"/>',
            '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
        ])
        spine_items.append('<itemref idref="titlepage"/>')

        for ch in chapters_data:
            manifest_items.append(f'<item id="{ch["id"]}" href="{ch["filename"]}" media-type="application/xhtml+xml"/>')
            spine_items.append(f'<itemref idref="{ch["id"]}"/>')

        manifest_xml = "\n    ".join(manifest_items)
        spine_xml = "\n    ".join(spine_items)

        cover_meta = '<meta name="cover" content="cover-image"/>' if has_cover else ''
        guide_xml = """  <guide>
    <reference type="cover" title="Обложка" href="cover.xhtml"/>
  </guide>""" if has_cover else ''

        opf_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="BookId">{BOOK_ID}</dc:identifier>
    <dc:title>{html.escape(BOOK_TITLE)}</dc:title>
    <dc:language>{BOOK_LANG}</dc:language>
    <dc:creator>{html.escape(BOOK_AUTHOR)}</dc:creator>
    <dc:description>Русский перевод глав {min_idx}–{max_idx} новеллы {html.escape(BOOK_TITLE)}</dc:description>
    {cover_meta}
    <meta property="dcterms:modified">{datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')}</meta>
  </metadata>
  <manifest>
    {manifest_xml}
  </manifest>
  <spine toc="ncx">
    {spine_xml}
  </spine>
{guide_xml}
</package>
"""
        zf.writestr("OEBPS/content.opf", opf_content)

    size = os.path.getsize(output_epub)
    print(f"Successfully generated {output_epub} ({size / 1024:.1f} KB, {len(chapters_data)} chapters)")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Build EPUB from Russian translated chapters.")
    parser.add_argument("--max-chapter", type=int, default=None, help="Maximum chapter index to include")
    args = parser.parse_args()
    build_epub(max_chapter=args.max_chapter)
