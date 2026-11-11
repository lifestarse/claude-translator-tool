#!/usr/bin/env python3
"""Публичная читалка перевода: статический сайт со страницей книги, главами и файлами.

Берёт из ru/ главы, прошедшие приёмку, подряд с первой (глава, которую
переводчик пишет прямо сейчас, ещё с маркерами и на сайт не попадает).
Пересобирает страницы и файлы для скачивания (EPUB, FB2, TXT, HTML, PDF),
только если набор глав изменился. autorun.py зовёт её после каждой главы.

Настройки — раздел «Публичная читалка» в BOOK.md: папка сайта (её отдаёт
веб-сервер), путь на сайте, слаг файлов, теги, аннотация. Обложка — cover.jpg.

    python build_site.py            собрать, если есть изменения
    python build_site.py --force    собрать заново
    python build_site.py --no-pdf   без PDF (Chromium не нужен)
"""

import argparse
import base64
import fcntl
import glob
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

from common import CHAPTERS_DIR, ROOT, chapter_path, read_text, ru_path, split_paragraphs, validate_translation
from lang import LANG

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BOOK = read_text(os.path.join(ROOT, "BOOK.md"))


def book_field(label, default=""):
    m = re.search(r"- %s[^:\n]*:\s*(.+)" % label, BOOK)
    value = m.group(1).strip() if m else ""
    return default if not value or value.startswith(("SET-ME", "<!--")) else value


def book_synopsis():
    m = re.search(r"### Аннотация[^\n]*\n(.+?)(?=\n## |\Z)", BOOK, re.S)
    text = re.sub(r"<!--.*?-->", "", m.group(1), flags=re.S) if m else ""
    paras = [" ".join(p.split()) for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paras:
        m = re.search(r"## BRIEF[^\n]*\n(.+?)\n\n", BOOK, re.S)
        paras = [" ".join(m.group(1).split())] if m else []
    return paras


def find_chrome():
    """Chromium для PDF: CHROME_PATH, кэш Playwright или системный браузер."""
    if os.environ.get("CHROME_PATH") and os.path.exists(os.environ["CHROME_PATH"]):
        return os.environ["CHROME_PATH"]
    found = sorted(glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome")))
    if found:
        return found[-1]
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        if shutil.which(name):
            return shutil.which(name)
    return ""


def esc(s):
    return html.escape(s, quote=True)


SITE = book_field("Папка сайта")
BASE = book_field("Путь на сайте", "/book").rstrip("/")
SLUG = book_field("Слаг файлов", "book")
TITLE = book_field("Русское", "Перевод")
ORIG = book_field("Оригинал")
EN = book_field("Английское")
AUTHOR = book_field("Автор")
TOTAL = int(re.sub(r"\D", "", book_field("Всего глав в оригинале", "0")) or 0)
COVER = os.path.join(ROOT, "cover.jpg")
# Шрифты читалки: woff2 (латиница + кириллица, начертание 400) и fonts.css с
# @font-face, пути в нём относительно style.css. Браузер качает только выбранный.
FONTS_DIR = os.path.join(ROOT, "fonts")
CHROME = find_chrome()
TAGS = [t.strip() for t in book_field("Теги").split(",") if t.strip()]
SYNOPSIS = book_synopsis()
SITE_NAME = book_field("Название сайта", "Читалка")
if not TOTAL and os.path.isdir(CHAPTERS_DIR):
    TOTAL = len([f for f in os.listdir(CHAPTERS_DIR) if f.startswith("chapter_") and f.endswith(".txt")])
TITLE_RE = re.compile(r"^Глав[аы]\s*(\d+(?:-\d+)?)\s*[:.]\s*(.*)$")


def collect():
    """[(номер, название, [абзацы])] — подряд с первой главы, только принятые."""
    out = []
    indexes = sorted(int(f[8:-4]) for f in os.listdir(CHAPTERS_DIR)
                     if f.startswith("chapter_") and f.endswith(".txt"))
    n = indexes[0] if indexes else 1
    while os.path.exists(chapter_path(n)) and os.path.exists(ru_path(n)):
        text = read_text(ru_path(n))
        if validate_translation(read_text(chapter_path(n)), text):
            break
        paras = split_paragraphs(text)
        m = TITLE_RE.match(paras[0])
        name = m.group(2).strip() if m else paras[0]
        out.append((n, name, paras[1:]))
        n += 1
    return out


def fingerprint(chapters):
    h = hashlib.sha1()
    for n, name, paras in chapters:
        h.update(("%d\n%s\n%s\n" % (n, name, "\n".join(paras))).encode("utf-8"))
    for path in (COVER, os.path.abspath(__file__), os.path.join(FONTS_DIR, "fonts.css")):
        if os.path.exists(path):
            h.update(open(path, "rb").read())
    return h.hexdigest()


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    with os.fdopen(fd, "wb") as f:
        f.write(data.encode("utf-8") if isinstance(data, str) else data)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# Оформление
# --------------------------------------------------------------------------

# Шрифты для чтения: ключ, подпись в панели «Aa», CSS-стек. Первый — по умолчанию.
# «Системный» ничего не качает: засечки устройства.
# Шрифт интерфейса остаётся системным: system-ui стоит в его стеке раньше Roboto,
# иначе @font-face «Roboto» отсюда перекрыл бы системный и Android качал бы его зря.
READ_FONTS = [
    ("literata", "Literata", "Literata,Georgia,serif"),
    ("ptserif", "PT Serif", "'PT Serif',Georgia,serif"),
    ("lora", "Lora", "Lora,Georgia,serif"),
    ("merri", "Merriweather", "Merriweather,Georgia,serif"),
    ("roboto", "Roboto", "Roboto,Arial,sans-serif"),
    ("opensans", "Open Sans", "'Open Sans',Arial,sans-serif"),
    ("ptsans", "PT Sans", "'PT Sans',Arial,sans-serif"),
    ("nunito", "Nunito Sans", "'Nunito Sans',Arial,sans-serif"),
    ("sys", "Системный", "Georgia,'Times New Roman',serif"),
]


def font_css():
    """@font-face из fonts/fonts.css и классы body.f-<ключ> для выбора шрифта."""
    path = os.path.join(FONTS_DIR, "fonts.css")
    faces = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
    return faces + "".join("body.f-%s{--ff:%s}\n" % (k, stack) for k, _, stack in READ_FONTS)


def font_buttons():
    """Кнопки выбора шрифта; каждая подписана своим шрифтом."""
    return "".join('<button data-ff="%s" style="font-family:%s" onclick="R.ff(\'%s\')"%s>%s</button>'
                   % (k, stack, k, ' class="wide"' if k == "sys" else "", esc(label))
                   for k, label, stack in READ_FONTS)


CSS = r"""
:root{--accent:#ff5e2c;--accent-2:#ff8a3d;--ink:#1d1d1f;--ink-2:#55555c;--muted:#8e8e96;
  --bg:#f5f5f7;--card:#fff;--line:#e8e8ed;--r:14px}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 -apple-system,"Segoe UI",system-ui,Roboto,"Helvetica Neue",Arial,sans-serif}
a{color:inherit;text-decoration:none}
.top{position:sticky;top:0;z-index:20;background:rgba(255,255,255,.92);backdrop-filter:saturate(1.6) blur(14px);border-bottom:1px solid var(--line)}
.top .in{max-width:1080px;margin:auto;display:flex;align-items:center;gap:14px;height:56px;padding:0 18px}
.logo{font-weight:800;font-size:19px;letter-spacing:-.3px;display:flex;align-items:center;gap:8px}
.logo i{width:28px;height:28px;border-radius:8px;background:linear-gradient(135deg,var(--accent),var(--accent-2));display:inline-flex;align-items:center;justify-content:center;color:#fff;font-style:normal;font-size:15px}
.wrap{max-width:1080px;margin:0 auto;padding:22px 18px 60px}
.hero{background:var(--card);border-radius:var(--r);padding:26px;display:flex;gap:28px;box-shadow:0 1px 2px rgba(0,0,0,.04)}
.cover{flex:none;width:230px}.cover img{width:100%;border-radius:10px;box-shadow:0 10px 30px rgba(0,0,0,.18);display:block}
.info{min-width:0;flex:1}
.info h1{font-size:28px;line-height:1.2;margin:2px 0 8px;letter-spacing:-.4px}
.orig{color:var(--muted);font-size:14px;margin-bottom:14px}
.tags{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:16px}
.tags span{background:#fff4ee;color:#d9481a;border-radius:20px;padding:4px 12px;font-size:13px;font-weight:600}
.stats{display:flex;gap:26px;margin:4px 0 20px;flex-wrap:wrap}
.stats b{display:block;font-size:20px}.stats small{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.5px}
.bar{height:6px;background:var(--line);border-radius:6px;overflow:hidden;margin:-8px 0 20px;max-width:420px}
.bar i{display:block;height:100%;background:linear-gradient(90deg,var(--accent),var(--accent-2))}
.btns{display:flex;gap:12px;flex-wrap:wrap}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;height:46px;padding:0 28px;border-radius:24px;font-weight:700;font-size:15px;border:0;cursor:pointer;font-family:inherit}
.btn.main{background:linear-gradient(135deg,var(--accent),var(--accent-2));color:#fff;box-shadow:0 6px 18px rgba(255,94,44,.35)}
.btn.sec{background:#fff;color:var(--ink);border:1.5px solid var(--line)}
.btn:hover{filter:brightness(1.05)}
.sec-card{background:var(--card);border-radius:var(--r);padding:22px 26px;margin-top:18px}
.sec-card h2{font-size:19px;margin:0 0 14px;display:flex;align-items:center;justify-content:space-between}
.syn p{margin:0 0 10px;color:var(--ink-2)}
.dl{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px}
.dl a{border:1.5px solid var(--line);border-radius:12px;padding:12px 14px;display:block;transition:.15s}
.dl a:hover{border-color:var(--accent);transform:translateY(-1px)}
.dl b{display:block;font-size:16px}.dl small{color:var(--muted)}
.toc{display:grid;grid-template-columns:repeat(2,1fr);gap:0 28px}
.toc a{display:flex;gap:12px;padding:12px 4px;border-bottom:1px solid var(--line);min-width:0}
.toc a:hover .nm{color:var(--accent)}
.toc .no{color:var(--muted);width:34px;flex:none;text-align:right;font-variant-numeric:tabular-nums}
.toc .nm{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.toc a.soon{opacity:.45;pointer-events:none}
.sort{font-size:13px;font-weight:600;color:var(--accent);cursor:pointer;background:none;border:0;font-family:inherit}
footer{color:var(--muted);font-size:12px;text-align:center;margin-top:30px}
@media(max-width:720px){.hero{flex-direction:column;align-items:center;text-align:center;padding:20px}.cover{width:180px}
 .tags,.stats,.btns{justify-content:center}.bar{margin-left:auto;margin-right:auto}.toc{grid-template-columns:1fr}.info h1{font-size:22px}}

/* --- reader --- */
body.rd{--paper:#fff;--text:#262626;--sub:#8a8a8a;--rline:#ececec;background:var(--paper);color:var(--text);transition:background .2s,color .2s}
body.rd.t-cream{--paper:#f6efe0;--text:#3b2f22;--sub:#9a8a74;--rline:#e6dcc6}
body.rd.t-green{--paper:#dceedd;--text:#24372a;--sub:#6f8a75;--rline:#c5dcc7}
body.rd.t-dark{--paper:#18191b;--text:#c9c9cb;--sub:#77787c;--rline:#2a2b2e}
body.rd.t-dark .top,body.rd.t-dark .panel,body.rd.t-dark .drawer{background:rgba(30,31,34,.95);border-color:#2a2b2e;color:#ddd}
body.rd .top{background:color-mix(in srgb,var(--paper) 90%,transparent);border-color:var(--rline)}
.rtop .in{max-width:860px}.rtop .ttl{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-weight:600;font-size:14px;color:var(--sub)}
.ib{width:38px;height:38px;border-radius:50%;border:0;background:transparent;color:inherit;font-size:18px;cursor:pointer;display:inline-flex;align-items:center;justify-content:center}
.ib:hover{background:rgba(127,127,127,.15)}
.page{max-width:var(--w,720px);margin:0 auto;padding:30px 22px 40px}
.page h1{font-size:1.45em;line-height:1.3;margin:10px 0 6px;font-family:var(--hf,inherit)}
.page .meta{color:var(--sub);font-size:13px;margin-bottom:30px}
.txt{font-size:var(--fs,19px);line-height:var(--lh,1.85);font-family:var(--ff,Literata,Georgia,serif)}
.txt p{margin:0 0 1em;text-indent:0}
.txt p.sb{text-align:center;color:var(--sub);letter-spacing:.4em;margin:1.6em 0}
.nav{max-width:var(--w,720px);margin:0 auto;padding:10px 22px 70px;display:flex;gap:12px}
.nav a{flex:1;text-align:center;border:1.5px solid var(--rline);border-radius:24px;height:48px;display:flex;align-items:center;justify-content:center;font-weight:700;color:var(--text)}
.nav a.next{background:linear-gradient(135deg,var(--accent),var(--accent-2));color:#fff;border:0}
.nav a.off{opacity:.35;pointer-events:none}
.panel{position:fixed;right:16px;top:64px;z-index:30;width:300px;background:#fff;border:1px solid var(--line);border-radius:14px;padding:16px;box-shadow:0 14px 40px rgba(0,0,0,.18);display:none;font-size:14px;max-height:calc(100vh - 80px);max-height:calc(100dvh - 80px);overflow-y:auto}
.panel.on{display:block}.panel .row{margin-bottom:14px}.panel .lab{color:var(--muted);font-size:12px;margin-bottom:6px;text-transform:uppercase;letter-spacing:.4px}
.sw{display:flex;gap:10px}.sw button{width:38px;height:38px;border-radius:50%;cursor:pointer;border:2px solid transparent}
.sw button.on{border-color:var(--accent)}
.seg{display:flex;border:1px solid var(--line);border-radius:10px;overflow:hidden}
.seg button{flex:1;height:36px;border:0;background:transparent;cursor:pointer;font:inherit;color:inherit}
.seg button.on{background:var(--accent);color:#fff}
.fonts{display:grid;grid-template-columns:1fr 1fr;gap:6px}
.fonts button{height:40px;padding:0 6px;border:1px solid var(--line);border-radius:10px;background:transparent;color:inherit;cursor:pointer;font-size:16px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.fonts button.on{border-color:var(--accent);box-shadow:inset 0 0 0 1px var(--accent);color:var(--accent)}
.fonts .wide{grid-column:1/-1}
.drawer{position:fixed;left:0;top:0;bottom:0;z-index:40;width:min(360px,88vw);background:#fff;transform:translateX(-102%);transition:transform .25s,visibility .25s;visibility:hidden;display:flex;flex-direction:column}
.drawer.on{transform:none;visibility:visible;box-shadow:0 0 40px rgba(0,0,0,.25)}.drawer h3{margin:0;padding:18px 20px;border-bottom:1px solid var(--rline,var(--line));font-size:16px}
.drawer .lst{overflow:auto;flex:1}.drawer .lst a{display:flex;gap:10px;padding:11px 20px;border-bottom:1px solid var(--rline,var(--line));font-size:14px}
.drawer .lst a.cur{color:var(--accent);font-weight:700}
.drawer .lst .no{color:var(--muted);width:30px;text-align:right;flex:none}
.shade{position:fixed;inset:0;background:rgba(0,0,0,.35);z-index:35;display:none}.shade.on{display:block}
.prog{position:fixed;left:0;top:0;height:3px;background:var(--accent);z-index:50;width:0}
/* --- тёмная тема страницы книги (общая с читалкой) --- */
body.t-dark{--bg:#121214;--card:#1c1c1f;--ink:#e8e8ea;--ink-2:#b4b4ba;--muted:#86868e;--line:#2d2d33}
body.t-dark:not(.rd) .top{background:rgba(24,24,27,.92);border-color:#2d2d33}
body.t-dark .tags span{background:#3b2219;color:#ff9e70}
body.t-dark .btn.sec{background:#232327;color:var(--ink);border-color:#38383f}
body.t-dark .hero,body.t-dark .sec-card{box-shadow:none}
.top .theme{margin-left:auto;font-size:20px;color:var(--ink)}
.theme .sun,body.t-dark .theme .moon{display:none}body.t-dark .theme .sun{display:inline}
"""

READER_JS = r"""
(function(){
var K='hunter-reader',S=JSON.parse(localStorage.getItem(K)||'{}'),b=document.body,r=document.documentElement;
var FONTS=__FONT_KEYS__,OLDF={serif:'literata',sans:'roboto'};
function fk(){var k=OLDF[S.ff]||S.ff;return FONTS.indexOf(k)>=0?k:FONTS[0]}
function apply(){var t=S.t||(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'white');b.classList.remove('t-cream','t-green','t-dark');if(t!=='white')b.classList.add('t-'+t);
 r.style.setProperty('--fs',(S.fs||19)+'px');r.style.setProperty('--w',(S.w||720)+'px');
 var cf=fk();FONTS.forEach(function(k){b.classList.toggle('f-'+k,k===cf)});
 r.style.setProperty('--lh',S.lh||1.85);
 document.querySelectorAll('[data-t]').forEach(function(e){e.classList.toggle('on',t===e.dataset.t)});
 document.querySelectorAll('[data-ff]').forEach(function(e){e.classList.toggle('on',cf===e.dataset.ff)});
 document.querySelectorAll('[data-w]').forEach(function(e){e.classList.toggle('on',String(S.w||720)===e.dataset.w)});
 var f=document.getElementById('fsv');if(f)f.textContent=S.fs||19;}
function save(){localStorage.setItem(K,JSON.stringify(S));apply();}
apply();
window.R={t:function(v){S.t=v;save()},ff:function(v){S.ff=v;save()},w:function(v){S.w=+v;save()},
 fs:function(d){S.fs=Math.max(14,Math.min(30,(S.fs||19)+d));save()},
 lh:function(d){S.lh=Math.max(1.4,Math.min(2.4,Math.round(((S.lh||1.85)+d)*100)/100));save()},
 panel:function(){document.getElementById('panel').classList.toggle('on')},
 toc:function(o){document.getElementById('drawer').classList.toggle('on',o);document.getElementById('shade').classList.toggle('on',o);
  if(o){var c=document.querySelector('.drawer .cur');if(c)c.scrollIntoView({block:'center'})}}};
var ch=b.dataset.ch;if(ch){localStorage.setItem('hunter-last',ch);}
document.addEventListener('keydown',function(e){if(e.target.tagName==='INPUT')return;
 if(e.key==='ArrowLeft'){var a=document.querySelector('a.prev:not(.off)');if(a)location=a.href}
 if(e.key==='ArrowRight'){var n=document.querySelector('a.next:not(.off)');if(n)location=n.href}
 if(e.key==='Escape'){R.toc(false);document.getElementById('panel').classList.remove('on')}});
var p=document.getElementById('prog');if(p)addEventListener('scroll',function(){var h=r.scrollHeight-innerHeight;p.style.width=(h>0?scrollY/h*100:0)+'%'},{passive:true});
})();
"""

READER_JS = READER_JS.replace("__FONT_KEYS__", json.dumps([k for k, _, _ in READ_FONTS]))

# Тема включается до отрисовки страницы — без белой вспышки; выбор общий с читалкой,
# без выбора — тема устройства.
THEME_INIT_JS = ("(function(){try{var S=JSON.parse(localStorage.getItem('hunter-reader')||'{}'),b=document.body,"
                 "t=S.t||(matchMedia('(prefers-color-scheme: dark)').matches?'dark':''),"
                 "f={serif:'literata',sans:'roboto'}[S.ff]||S.ff;"
                 "if(t&&t!=='white')b.classList.add('t-'+t);if(f)b.classList.add('f-'+f)}catch(e){}})();")

INDEX_JS = r"""
window.T=function(){var K='hunter-reader',S=JSON.parse(localStorage.getItem(K)||'{}'),d=document.body.classList.toggle('t-dark');S.t=d?'dark':'white';localStorage.setItem(K,JSON.stringify(S));};
(function(){var l=localStorage.getItem('hunter-last'),c=document.getElementById('cont');
if(l&&c){c.href='ch/'+l+'.html';c.style.display='';c.querySelector('span').textContent='Продолжить · гл. '+l;}
var s=document.getElementById('sort'),t=document.getElementById('toc');
if(s)s.onclick=function(){var a=[].slice.call(t.children).reverse();a.forEach(function(x){t.appendChild(x)});
 s.textContent=s.textContent.indexOf('↓')>=0?'Сначала новые ↑':'Сначала первые ↓'};})();
"""


def head(title, desc, extra=""):
    return ("<!doctype html><html lang=\"ru\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1,viewport-fit=cover\">"
            "<title>%s</title><meta name=\"description\" content=\"%s\">"
            "<meta property=\"og:title\" content=\"%s\"><meta property=\"og:image\" content=\"%s/cover.jpg\">"
            "<link rel=\"icon\" href=\"%s/cover.jpg\"><link rel=\"stylesheet\" href=\"%s/style.css?v=%s\">%s</head>"
            % (esc(title), esc(desc), esc(title), BASE, BASE, BASE, VERSION, extra))


def is_break(p):
    return bool(re.fullmatch(r"[*＊·.…\s]+", p)) and len(p) <= 12


def chapter_page(ch, chapters, idx):
    n, name, paras = ch
    prev_n = chapters[idx - 1][0] if idx > 0 else None
    next_n = chapters[idx + 1][0] if idx + 1 < len(chapters) else None
    body = "\n".join('<p class="sb">%s</p>' % esc(p) if is_break(p) else "<p>%s</p>" % esc(p)
                     for p in paras)
    words = sum(len(p.split()) for p in paras)
    lst = "".join('<a href="%d.html"%s><span class="no">%d</span><span>%s</span></a>'
                  % (c[0], ' class="cur"' if c[0] == n else "", c[0], esc(c[1])) for c in chapters)
    swatch = "".join('<button data-t="%s" style="background:%s" onclick="R.t(\'%s\')" title="%s"></button>'
                     % (k, col, k, lab) for k, col, lab in (
                         ("white", "#fff;border-color:#ddd", "Светлая"), ("cream", "#f6efe0", "Бумага"),
                         ("green", "#dceedd", "Зелёная"), ("dark", "#18191b", "Тёмная")))
    nav = ('<a class="prev%s" href="%s">← Назад</a><a href="%s/">Оглавление</a><a class="next%s" href="%s">Далее →</a>'
           % ("" if prev_n else " off", "%d.html" % prev_n if prev_n else "#", BASE,
              "" if next_n else " off", "%d.html" % next_n if next_n else "#"))
    t = "Глава %d. %s" % (n, name)
    return (head("%s — %s" % (t, TITLE), "%s. Читать онлайн на русском." % t) +
            '<body class="rd" data-ch="%d"><script>%s</script><div class="prog" id="prog"></div>' % (n, THEME_INIT_JS.replace("hunter-", SLUG + "-")) +
            '<header class="top rtop"><div class="in">'
            '<button class="ib" onclick="R.toc(true)" title="Оглавление">☰</button>'
            '<a class="ib" href="%s/" title="К книге">⌂</a>' % BASE +
            '<span class="ttl">%s</span>' % esc(TITLE) +
            '<button class="ib" onclick="R.panel()" title="Настройки">Aa</button></div></header>'
            '<div class="panel" id="panel">'
            '<div class="row"><div class="lab">Тема</div><div class="sw">%s</div></div>' % swatch +
            '<div class="row"><div class="lab">Размер шрифта · <span id="fsv"></span></div><div class="seg">'
            '<button onclick="R.fs(-1)">A−</button><button onclick="R.fs(1)">A+</button></div></div>'
            '<div class="row"><div class="lab">Межстрочный</div><div class="seg">'
            '<button onclick="R.lh(-.1)">−</button><button onclick="R.lh(.1)">+</button></div></div>'
            '<div class="row"><div class="lab">Шрифт</div><div class="fonts">' +
            font_buttons() +
            '</div></div>'
            '<div class="row" style="margin:0"><div class="lab">Ширина</div><div class="seg">'
            '<button data-w="600" onclick="R.w(600)">Узко</button><button data-w="720" onclick="R.w(720)">Средне</button>'
            '<button data-w="900" onclick="R.w(900)">Широко</button></div></div></div>'
            '<div class="shade" id="shade" onclick="R.toc(false)"></div>'
            '<aside class="drawer" id="drawer"><h3>Оглавление · %d глав</h3><div class="lst">%s</div></aside>'
            % (len(chapters), lst) +
            '<main class="page"><h1>%s</h1><div class="meta">%s · ≈%d мин чтения</div>'
            % (esc(t), esc(TITLE), max(1, words // 200)) +
            '<article class="txt">%s</article></main>' % body +
            '<nav class="nav">%s</nav>' % nav +
            '<script src="%s/reader.js?v=%s"></script></body></html>' % (BASE, VERSION))


def index_page(chapters, files):
    done = len(chapters)
    pct = 100.0 * done / TOTAL
    toc = "".join('<a href="ch/%d.html"><span class="no">%d</span><span class="nm">%s</span></a>'
                  % (n, n, esc(name)) for n, name, _ in chapters)
    toc += "".join('<a class="soon"><span class="no">%d</span><span class="nm">переводится…</span></a>' % n
                   for n in range(done + 1, min(done + 4, TOTAL + 1)))
    dl = "".join('<a href="files/%s" download><b>%s</b><small>%s · %s</small></a>'
                 % (fn, label, note, size) for fn, label, note, size in files)
    stamp = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    first = '<a class="btn main" href="ch/1.html">📖 Читать</a>' if chapters else ""
    return (head(TITLE + " — читать онлайн", " ".join(re.sub("<[^>]+>", " ", s) for s in SYNOPSIS)[:300]) +
            '<body><script>%s</script><header class="top"><div class="in"><a class="logo" href="/"><i>⚡</i>%s</a><button class="ib theme" onclick="T()" title="Тёмная / светлая тема"><span class="moon">☾</span><span class="sun">☀</span></button></div></header>' % (THEME_INIT_JS.replace("hunter-", SLUG + "-"), esc(SITE_NAME)) +
            ''
            '<div class="wrap"><section class="hero"><div class="cover"><img src="cover.jpg" alt="Обложка"></div>'
            '<div class="info"><h1>%s</h1><div class="orig">%s · %s</div>' % (esc(TITLE), esc(ORIG), esc(EN)) +
            '<div class="tags">%s</div>' % "".join("<span>%s</span>" % esc(t) for t in TAGS) +
            '<div class="stats"><div><b>%d / %d</b><small>Глав переведено</small></div>'
            '<div><b>%s</b><small>Автор</small></div><div><b>RU</b><small>Перевод</small></div></div>'
            % (done, TOTAL, esc(AUTHOR)) +
            '<div class="bar"><i style="width:%.1f%%"></i></div>' % pct +
            '<div class="btns">%s<a class="btn sec" id="cont" style="display:none" href="#">▶ <span></span></a>'
            '<a class="btn sec" href="#downloads">⬇ Скачать</a></div></div></section>' % first +
            '<section class="sec-card syn"><h2>Аннотация</h2>%s</section>'
            % "".join("<p>%s</p>" % esc(s) for s in SYNOPSIS) +
            '<section class="sec-card" id="downloads"><h2>Скачать книгу</h2><div class="dl">%s</div>'
            '<p style="color:var(--muted);font-size:13px;margin:12px 0 0">Файлы обновляются автоматически по мере перевода. '
            'Сейчас в них главы 1–%d.</p></section>' % (dl, done) +
            '<section class="sec-card"><h2>Оглавление <button class="sort" id="sort">Сначала новые ↑</button></h2>'
            '<div class="toc" id="toc">%s</div></section>' % toc +
            '<footer>Любительский перевод · обновлено %s</footer></div>' % stamp +
            '<script>%s</script></body></html>' % INDEX_JS.replace("hunter-", SLUG + "-"))


# --------------------------------------------------------------------------
# Файлы для скачивания
# --------------------------------------------------------------------------

def make_txt(chapters):
    parts = [TITLE, "%s · %s" % (ORIG, AUTHOR), ""]
    for n, name, paras in chapters:
        parts += ["", "Глава %d. %s" % (n, name), ""] + [p + "\n" for p in paras]
    return "\n".join(parts).replace("\n\n\n\n", "\n\n\n") + "\n"


def make_html(chapters):
    css = ("body{max-width:760px;margin:auto;padding:24px;font:18px/1.8 Georgia,serif;color:#222}"
           "h1{text-align:center}h2{margin-top:2.4em;page-break-before:always}"
           ".toc a{display:block;color:#c43d12;text-decoration:none;margin:2px 0}"
           "img{display:block;margin:auto;max-width:320px;width:60%}")
    toc = "".join('<a href="#c%d">Глава %d. %s</a>' % (n, n, esc(name)) for n, name, _ in chapters)
    body = "".join('<h2 id="c%d">Глава %d. %s</h2>%s' % (
        n, n, esc(name), "".join("<p>%s</p>" % esc(p) for p in paras)) for n, name, paras in chapters)
    cover = ""
    if os.path.exists(COVER):
        cover = '<img src="data:image/jpeg;base64,%s" alt="">' % base64.b64encode(open(COVER, "rb").read()).decode()
    return ('<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" '
            'content="width=device-width,initial-scale=1"><title>%s</title><style>%s</style></head><body>'
            '%s<h1>%s</h1><p style="text-align:center">%s · %s</p><div class="toc">%s</div>%s</body></html>'
            % (esc(TITLE), css, cover, esc(TITLE), esc(ORIG), esc(AUTHOR), toc, body))


def make_fb2(chapters):
    x = lambda s: html.escape(s, quote=False)
    cover = ""
    binary = ""
    if os.path.exists(COVER):
        cover = '<coverpage><image l:href="#cover.jpg"/></coverpage>'
        binary = '<binary id="cover.jpg" content-type="image/jpeg">%s</binary>' % \
                 base64.b64encode(open(COVER, "rb").read()).decode()
    ann = "".join("<p>%s</p>" % x(re.sub("<[^>]+>", " ", s)) for s in SYNOPSIS)
    sections = "".join(
        "<section><title><p>Глава %d. %s</p></title>%s</section>" % (
            n, x(name), "".join("<empty-line/>" if is_break(p) else "<p>%s</p>" % x(p) for p in paras))
        for n, name, paras in chapters)
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" '
            'xmlns:l="http://www.w3.org/1999/xlink"><description><title-info>'
            '<genre>sf_fantasy</genre><author><nickname>%s</nickname></author>'
            '<book-title>%s</book-title><annotation>%s</annotation>%s<lang>ru</lang><src-lang>%s</src-lang>'
            '</title-info><document-info><author><nickname>%s</nickname></author>'
            '<date>%s</date><id>%s</id><version>1.0</version></document-info></description>'
            '<body><title><p>%s</p></title>%s</body>%s</FictionBook>'
            % (x(AUTHOR), x(TITLE), ann, cover, LANG.code, x(SITE_NAME), datetime.now().strftime("%Y-%m-%d"),
               hashlib.md5(TITLE.encode()).hexdigest(), x(TITLE), sections, binary))


def make_epub(last):
    for old in glob.glob(os.path.join(ROOT, "book_ru_*.epub")):
        os.remove(old)
    subprocess.run([sys.executable, os.path.join(ROOT, "build_epub.py"), "--max-chapter", str(last)],
                   cwd=ROOT, check=True, capture_output=True)
    return open(glob.glob(os.path.join(ROOT, "book_ru_*.epub"))[0], "rb").read()


# Вся книга одним документом стоила Chromium 1,35 ГБ памяти при 360 главах, и две
# одновременные сборки уронили сервер в OOM. Части по PDF_PART глав рендерятся по
# одной (~250 МБ), кэшируются по содержимому и склеиваются pypdf с закладками.
PDF_PART = 30
PDF_LOCK = "/tmp/translator-site-pdf.lock"  # общий на сервер: PDF рендерится по одному
PDF_CACHE = os.path.join(ROOT, ".site_cache", "pdf")
PDF_CSS = ("@page{size:A4;margin:18mm 17mm}"
           "body{margin:0;font:12pt/1.55 Georgia,'PT Serif',serif;color:#111}"
           "h2{font-size:15pt;margin:0 0 1.1em;page-break-before:always}"
           "p{margin:0 0 .55em}p.sb{text-align:center;letter-spacing:.4em}"
           ".t{text-align:center;font-size:24pt;font-weight:bold;margin:1.2em 0 .3em}"
           ".s{text-align:center;color:#555}img{display:block;margin:1.5em auto;width:70%}")
PDF_MERGE = r"""
import sys, pypdf
out, title, author = sys.argv[1:4]
w = pypdf.PdfWriter()
for part in sys.argv[4:]:
    w.append(part, import_outline=True)
w.add_metadata({"/Title": title, "/Author": author})
w.page_mode = "/UseOutlines"
with open(out, "wb") as f:
    w.write(f)
"""


def pdf_part_html(chunk, first):
    top = ""
    if first:
        if os.path.exists(COVER):
            top = '<img src="data:image/jpeg;base64,%s" alt="">' % base64.b64encode(open(COVER, "rb").read()).decode()
        top += '<div class="t">%s</div><div class="s">%s · %s</div>' % (esc(TITLE), esc(ORIG), esc(AUTHOR))
    body = "".join('<h2>Глава %d. %s</h2>%s' % (n, esc(name), "".join(
        '<p class="sb">%s</p>' % esc(p) if is_break(p) else "<p>%s</p>" % esc(p) for p in paras))
        for n, name, paras in chunk)
    return ('<!doctype html><html lang="ru"><head><meta charset="utf-8"><style>%s</style></head>'
            '<body>%s%s</body></html>' % (PDF_CSS, top, body))


def pdf_python():
    """Интерпретатор с pypdf: прогон перевода может звать сборку системным python."""
    for py in (os.path.join(ROOT, ".venv", "bin", "python3"), sys.executable):
        if os.path.exists(py) and subprocess.run([py, "-c", "import pypdf"],
                                                 capture_output=True).returncode == 0:
            return py
    return None


def make_pdf(chapters, out):
    py = pdf_python()
    if not os.path.exists(CHROME) or not py:
        return False
    os.makedirs(PDF_CACHE, exist_ok=True)
    with open(PDF_LOCK, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        parts = []
        for i in range(0, len(chapters), PDF_PART):
            doc = pdf_part_html(chapters[i:i + PDF_PART], i == 0)
            part = os.path.join(PDF_CACHE, hashlib.sha1(doc.encode("utf-8")).hexdigest()[:16] + ".pdf")
            if not os.path.exists(part):
                src, tmp = part[:-4] + ".html", part + ".tmp"
                write(src, doc)
                try:
                    subprocess.run([CHROME, "--headless", "--no-sandbox", "--disable-gpu",
                                    "--no-pdf-header-footer", "--generate-pdf-document-outline",
                                    "--print-to-pdf=" + tmp, "file://" + src],
                                   check=True, capture_output=True, timeout=900)
                    os.replace(tmp, part)
                except (subprocess.SubprocessError, OSError):
                    return False
                finally:
                    for f in (src, tmp):
                        if os.path.exists(f):
                            os.remove(f)
            parts.append(part)
        for old in os.listdir(PDF_CACHE):
            if os.path.join(PDF_CACHE, old) not in parts:
                os.remove(os.path.join(PDF_CACHE, old))
        tmp = os.path.join(os.path.dirname(out), ".tmp-merge.pdf")
        r = subprocess.run([py, "-c", PDF_MERGE, tmp, TITLE, AUTHOR] + parts,
                           capture_output=True, timeout=900)
        if r.returncode or not os.path.exists(tmp):
            return False
        os.chmod(tmp, 0o644)
        os.replace(tmp, out)
    return True


def human(n):
    return "%.1f МБ" % (n / 1048576.0) if n >= 1048576 else "%d КБ" % max(1, n // 1024)


def main():
    global VERSION
    ap = argparse.ArgumentParser(description="Сборка публичной читалки")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--no-pdf", action="store_true")
    args = ap.parse_args()

    if not SITE:
        raise SystemExit("Укажи «Папка сайта» в разделе «Публичная читалка» BOOK.md.")
    chapters = collect()
    if not chapters:
        print("Нет принятых глав.")
        return
    fp = fingerprint(chapters)
    stamp_file = os.path.join(SITE, ".build")
    if not args.force and os.path.exists(stamp_file) and open(stamp_file).read() == fp:
        print("Без изменений: %d глав." % len(chapters))
        return
    VERSION = fp[:8]
    last = chapters[-1][0]

    write(os.path.join(SITE, "style.css"), font_css() + CSS)
    if os.path.isdir(FONTS_DIR):
        for fn in sorted(os.listdir(FONTS_DIR)):
            if fn.endswith(".woff2") or fn == "LICENSES.txt":
                with open(os.path.join(FONTS_DIR, fn), "rb") as f:
                    write(os.path.join(SITE, "fonts", fn), f.read())
    write(os.path.join(SITE, "reader.js"), READER_JS.replace("hunter-", SLUG + "-"))
    if os.path.exists(COVER):
        shutil.copyfile(COVER, os.path.join(SITE, "cover.jpg"))
    for i, ch in enumerate(chapters):
        write(os.path.join(SITE, "ch", "%d.html" % ch[0]), chapter_page(ch, chapters, i))

    fdir = os.path.join(SITE, "files")
    base = "%s_1-%d" % (SLUG, last)
    produced = [
        (base + ".epub", "EPUB", "читалки, телефоны", make_epub(last)),
        (base + ".fb2", "FB2", "FBReader, PocketBook", make_fb2(chapters)),
        (base + ".txt", "TXT", "простой текст", make_txt(chapters)),
        (base + ".html", "HTML", "одной страницей", make_html(chapters)),
    ]
    keep = set()
    files = []
    for fn, label, note, data in produced:
        write(os.path.join(fdir, fn), data)
        keep.add(fn)
        files.append((fn, label, note, human(os.path.getsize(os.path.join(fdir, fn)))))
    pdf = base + ".pdf"
    if not args.no_pdf and make_pdf(chapters, os.path.join(fdir, pdf)):
        keep.add(pdf)
        files.append((pdf, "PDF", "для печати", human(os.path.getsize(os.path.join(fdir, pdf)))))
    for old in os.listdir(fdir):
        if old not in keep and not old.startswith(".tmp-"):
            os.remove(os.path.join(fdir, old))

    write(os.path.join(SITE, "index.html"), index_page(chapters, files))
    write(os.path.join(SITE, "chapters.json"),
          json.dumps([{"n": n, "title": name} for n, name, _ in chapters], ensure_ascii=False))
    write(stamp_file, fp)
    print("Собрано: %d глав, файлы: %s" % (len(chapters), ", ".join(f[1] for f in files)))


VERSION = "0"

if __name__ == "__main__":
    main()
