"""Shared helpers: paths, chapter identity parsing, glossary loading, validation."""

import hashlib
import json
import os
import re
import sys
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.abspath(__file__))
CHAPTERS_DIR = os.path.join(ROOT, "chapters")
RU_DIR = os.path.join(ROOT, "ru")
CATALOG_FILE = os.path.join(ROOT, "catalog.json")
MANIFEST_FILE = os.path.join(ROOT, "manifest.json")
GLOSSARY_FILE = os.path.join(ROOT, "glossary.json")

PARA_MARKER_RE = re.compile(r"^\s*\[\d+\]\s")

# Fragments of the agent's own tool-call envelope, leaked into the chapter text.
# Named tags only, never a bare "<...>": the novel's prose does use angle
# brackets, and an open-ended rule would flag real sentences.
STRAY_TAG_RE = re.compile(
    r"</?(?:content|invoke|function_calls|parameter|antml|thinking)\b[^>]*>", re.I
)
CJK_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿]")

# "第11章+12章 共生", "第 3 章 航母", "第1234章" -> every 章 number in the title
CHAPTER_NUM_RE = re.compile(r"第\s*(\d+)\s*章")
# Bare "+12章" continuation inside a merged title, e.g. "第11章+12章"
BARE_NUM_RE = re.compile(r"[+＋]\s*(\d+)\s*章")


EXCLUDED_FILE = os.path.join(ROOT, "excluded_chapters.json")


def load_excluded():
    """Chapters that must never be translated -> {index: reason}.

    Two kinds, both silent failures if ignored. `duplicate_body` files are
    full-size and pass every structural check, but hold another chapter's text;
    translating one puts the same chapter in the book twice under different
    numbers. `empty_source` files have no text to translate at all.
    """
    data = load_json(EXCLUDED_FILE, {}) or {}
    return {e["index"]: e.get("reason", "") for e in data.get("excluded", [])}


def chapter_path(idx):
    return os.path.join(CHAPTERS_DIR, "chapter_%04d.txt" % idx)


def ru_path(idx):
    return os.path.join(RU_DIR, "chapter_%04d.txt" % idx)


def parse_chapter_numbers(title):
    """Return every real chapter number a title claims, in order.

    The site merges chapters ("第11章+12章 共生"), so one file can cover several.
    Returns [] for front/back matter ("完本感言", "序章").
    """
    nums = [int(n) for n in CHAPTER_NUM_RE.findall(title)]
    nums += [int(n) for n in BARE_NUM_RE.findall(title)]
    seen, out = set(), []
    for n in nums:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def split_paragraphs(text):
    """Blank-line separated paragraphs, blanks dropped. Title line included."""
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


# --------------------------------------------------------------------------
# Site watermark removal
# --------------------------------------------------------------------------

# czbooks injects piracy-site watermarks and vote solicitations into the
# chapter body. Measured across the corpus: 547 paragraphs that are pure spam
# and 201 where the spam is glued onto the END of a real story paragraph.
# That second group is why this cannot be done by dropping whole paragraphs --
# it would delete story text.
#
# Every marker below is high confidence. Deliberately excluded: bare 請記住
# ("please remember") and 最快更新, which are ordinary Chinese and do occur in
# dialogue. Anchoring on those would cut real prose.
SPAM_MARKERS = (
    "手機版閱讀網址", "手機閱讀", "手機用戶請到", "手機版",
    "本站地址", "首發域名", "新域名", "本書首發",
    "一秒記住", "1秒記住", "定個小目標",
    "書客居", "小說網", "筆趣", "風雲小說", "4小說",
    "投推薦票", "月票",
    "更多精彩內容請關注", "無廣告", "免費閱讀",
    "最新章節！", "最快更新進化",
    # Found only in the later arcs -- the site changed its footer partway
    # through, so a marker list built from the early chapters misses these.
    "上拉加載", "下一章s", "手機站", "改版升級",
    "數據和書簽", "與電腦站同步", "電腦站",
    # Reached the translator before this was caught: three chapters of wave 1
    # shipped with the site's reader greeting rendered into Russian prose.
    # A marker list is only as good as the variants you have actually seen.
    "歡迎廣大書友", "光臨閱讀", "連載作品", "最火的", "書友光臨",
)

# Обе семьи выше добавлены 29.07.2026 и потребовали правки ОБЕИХ сторон разом.
# Спам в главах 721, 816 и 881 стоял отдельным абзацем, а не хвостом реального,
# поэтому очистка оригинала уменьшала число абзацев, и уже готовый перевод
# переставал с ним сходиться. Тексты в ru/ поправлены тем же заходом
# (tools/drop_spam_paragraph.py), иначе verify.py забраковал бы главы, с
# которыми всё в порядке.

# A third footer family, found the expensive way: chapter 538 failed acceptance
# three times running, always on the same paragraph -- its last, nine characters
# of "fast updates, no popups, plain text". Three agents each dropped it as junk,
# correctly, and each time the paragraph count came up one short. 321 chapters
# carry one of these.
#
# They are NOT in SPAM_MARKERS, and that distinction is the whole point. A
# marker truncates the paragraph at its position, which assumes the spam is a
# suffix. This family is not: in chapter 1183 the footer sits mid-paragraph with
# story text after it, and truncating there deleted the prose while keeping a
# list of other novels' titles. Excising the footer as a span keeps both sides.
#
# Every alternative below is anchored on fixed wording taken from the corpus,
# never an open-ended wildcard -- an unbounded ".*" in the thanks rule once
# condemned an entire scene.
SPAM_SPANS = (
    r"[^\s。！？]{0,4}最快更新[，,]?[無无]彈窗閱讀請(?:收藏[^\s。！？]{0,4})?(?:\(\))?。?",
    r"[?？]?純文字在線閱讀本站域名[\s　]*(?:手機同步閱讀請訪問)?",
    r"[〖【]?[∷·、\s]*更新快[∷·、\s]*[無无]彈窗[∷·、\s]*純文字[∷·、\s]*[〗】]?",
    r"[^\s。！？]{0,20}txt下載地址[:：]\s*\S*",
    r"[^\s。！？]{0,16}，精彩小說無彈窗",
    r"[^\s。！？]{0,16}，更新快[、,，]\s*[無无]彈窗[！!]?",
    # Пятая семья, найденная читателем в готовой книге, а не проверкой: просьба
    # поделиться сайтом, стоящая отдельным абзацем в конце главы (гл. 721, 816,
    # 1543, 1569). Взята пролётом, а не маркером, намеренно: маркер обрезает
    # абзац по своей позиции и оставляет огрызок «如果您覺得網不錯就多多», который
    # проходит проверку на мусор — он китайский — и остаётся в книге. Пролёт
    # съедает фразу целиком, абзац становится пустым и выбрасывается.
    # 謝謝各位讀者 отдельным маркером не берётся: обычная вежливая фраза, может
    # стоять в речи персонажа.
    r"如果您覺得[^\s。！？]{0,10}不錯[^\s。！？]{0,10}分享本站[！!]?謝謝各位讀者的支持[！!]?",
    # Шестое семейство, найдено 01.08.2026 при параллельном прогоне: «нашли
    # ошибку в главе — пожалуйтесь, исправим» отдельным последним абзацем в 36
    # главах 2022–2122, формулировка одна во всех. Именно спаном, не маркером:
    # у фразы есть префикс 如果您發現 ДО опорных слов, и маркер-усечение
    # оставил бы огрызок «如果您發現», который проходит проверку на мусор — он
    # китайский — и остался бы в книге. 27 глав ушли переводчику до
    # обнаружения и получили футер по-русски; обе стороны чинились одним
    # заходом, как и семьи выше.
    r"如果您發現章節內容錯誤請舉報[，,]?我們會第一時間修複[。.!！]?",
    # Седьмое семейство, найдено 02.08.2026 читкой заголовков: обломки
    # джаваскрипта сайтов, попавшие в текст при скачивании — readx();
    # dudu1(); dudu2(); reads(); cpa300_4(); и голое «();». 39 исходников.
    # Латинский идентификатор со скобками в китайской прозе однозначен;
    # в гл. 2299/2300 переводчик сохранил его отдельным «абзацем», и глава
    # 2300 потеряла заголовок при сошедшемся счёте абзацев.
    r"\b[A-Za-z_][A-Za-z0-9_]*\(\);?|\(\);",
    # Восьмое семейство, найдено 02.08.2026 по багу заголовка на сайте:
    # реклама веб-игры по мотивам новеллы отдельным абзацем в начале И конце
    # глав 2035–2072 (переводчики честно перевели её восемью разными способами:
    # «ноктюрнов», «вариаций», «совместных партий»…), плюс навигационный мусор
    # «快速打字/高速文字手打…章節列表» и «上一頁…下一頁» в 2977 и 4546–4551.
    r"進化的四十六億重奏\s*結合\s*蠻荒紀\S*\s*小說改編網頁遊戲[^\n。]{0,20}！{0,3}",
    r"高速文字手打\s*進化的四十六億重奏章節列表",
    r"上一頁返回最新章節列表\(回車\)下一頁",
)
SPAM_SPAN_RE = re.compile("|".join(SPAM_SPANS))

# The site's list of other novels it promotes, e.g. "A、 B、 C、 D". Applied ONLY
# to paragraphs that already matched a span -- see strip_spam for why.
TITLE_CHAIN_RE = re.compile(r"(?:[^\s、。！？]{2,8}、\s*){3,}[^\s、。！？]{2,8}")
SPAM_RE = re.compile("|".join(re.escape(m) for m in SPAM_MARKERS))

# What is left over once the marker is gone: punctuation, latin, digits, URL
# debris. If a paragraph reduces to only this, it carried no story at all.
RESIDUE_RE = re.compile(r"[:：。，、．\s\[\]()（）!！?？~～/.,＆&…\-—_a-zA-Z0-9]+")


# Author's notes thanking named readers for votes ("感謝~ник~的月票~"). Cutting
# these at the marker leaves a dangling particle mid-phrase, so drop the whole
# paragraph instead.
#
# Two guards, both learned the hard way. The distance is bounded and may not
# cross a sentence break: an unbounded ".*" matched 感謝 at the start of a
# 1460-character narrative paragraph against a 月票 far later and condemned the
# whole scene. THANKS_MAX_LEN then refuses to drop anything long enough to be
# prose -- real thank-you notes have a median length of 13 characters.
THANKS_RE = re.compile(r"感謝[^。！？\n]{0,60}(月票|推薦票|打賞)")
THANKS_MAX_LEN = 150

# A fourth family, and the second one found by a chapter jamming the pipeline:
# 629 failed acceptance four times running on its last paragraph, nine
# characters thanking a reader for a tip. These are the AUTHOR's notes, not the
# site's -- they sit at the tail, or in the Qidian arcs as a promo pinned to the
# top of the chapter. 325 chapters carry at least one.
#
# Excise-first, drop-second, for a reason paid for once already: in chapters
# 1411, 1413 and 1415 the top promo is followed by real prose IN THE SAME
# paragraph ("‘轟隆……’", a line of dialogue), so dropping the paragraph whole
# would delete the opening of the scene -- exactly the mistake SPAM_SPANS
# exists to prevent.
PS_NOTE_RE = re.compile(r"^[\s　]*(?:ps|p\.s\.?|ｐｓ)[\s　]*[:：.、,]", re.I)

# The promo appeal itself, ending at the ！ that closes it. Anchored on the
# platform's fixed vocabulary; the distance bounds keep it from running past
# the appeal into the story text that follows.
PS_PROMO_RE = re.compile(
    r"^[\s　]*(?:ps|p\.s\.?|ｐｓ)[\s　]*[:：.、,]"
    r"[^\n]{0,120}?(?:公眾號|微信|起點|粉絲節|投票)[^\n]{0,120}?[！!]"
)

# What the promo excision sometimes leaves behind: a second sentence carrying
# the appeal but no ps marker of its own (ch.2167, 2168). Platform vocabulary
# only -- none of these three strings occurs anywhere in the novel's prose.
PROMO_RESIDUE_RE = re.compile(r"起點幣|粉絲節|訂閱我的章節")

# Thanks notes the vote/tip rule above misses: gifts, birthday red packets, and
# the bare "感謝~ник~的1888~" shape where the amount carries no keyword at all.
# 謝謝 needs its own rule rather than joining THANKS_RE -- see strip_spam for
# the ordering that makes adding it safe.
THANKS_ALT_RE = re.compile(r"(?:謝謝|谢谢|多謝)[^。！？\n]{0,60}(?:月票|推薦票|打賞|打赏)")
THANKS_GIFT_RE = re.compile(
    r"(?:感謝|感谢|謝謝|谢谢|多謝)[^。！？\n]{0,60}(?:贈送|紅包|红包|訂閱|订阅)")
THANKS_NICK_RE = re.compile(r"(?:感謝|感谢|謝謝|谢谢|多謝)[~～][^。！？\n]{0,40}[~～]")

# Авторская благодарность за донат БЕЗ слова «спасибо»: «столько донатов, что
# всех не перечислить» (гл. 881). Все правила выше держатся на 感謝/謝謝/多謝 и
# такую форму не видят ни одно. Привязка к 打賞 и достаточна, и обязательна: по
# всему корпусу 好多打賞 встречается один раз, и это именно записка автора.
THANKS_TIP_RE = re.compile(r"好多打賞")

# Thanks glued onto real prose after a run of ellipses. Chapter 30 is the only
# instance in the corpus and it is precisely the shape that costs a sentence:
# the paragraph is under THANKS_MAX_LEN, so the whole-paragraph rules would
# delete the story text in front of the ellipses along with the note.
THANKS_TAIL_RE = re.compile(r"[…]{4,}(?=[^。！？\n]{0,60}(?:謝謝|感謝|多謝))[^\n]*$")

# Trailing junk left dangling once the tail is cut. Sentence-final marks
# (。？！…」』) are NOT here -- stripping them truncates real sentences, which
# an earlier version of this function did.
DANGLING = " 　\t:：，,、;；-—~～("


def strip_spam(paragraph):
    """Cut the watermark off a paragraph, keeping any story text before it.

    The spam is always a suffix, so truncating at the first marker preserves
    the prose and removes the injected tail.
    """
    # Author's notes, in strict order: excise what has prose attached, only then
    # test for a paragraph that is nothing but note. Reversing these two steps
    # deletes the sentence in front of the note.
    if THANKS_TAIL_RE.search(paragraph):
        paragraph = THANKS_TAIL_RE.sub("", paragraph).strip()
    promo = PS_PROMO_RE.match(paragraph)
    if promo:
        rest = paragraph[promo.end():].strip()
        # A trailing second appeal is still spam; real prose never carries the
        # platform's vocabulary.
        if len(rest) <= THANKS_MAX_LEN and PROMO_RESIDUE_RE.search(rest):
            return ""
        paragraph = rest
    if not paragraph:
        return ""
    if PS_NOTE_RE.match(paragraph):
        return ""
    if len(paragraph) <= THANKS_MAX_LEN and (
        THANKS_RE.search(paragraph)
        or THANKS_ALT_RE.search(paragraph)
        or THANKS_GIFT_RE.search(paragraph)
        or THANKS_NICK_RE.search(paragraph)
        or THANKS_TIP_RE.search(paragraph)
    ):
        return ""
    # Spans first: they are excised in place, so story text on either side of a
    # mid-paragraph footer survives. Only then the suffix markers truncate.
    if SPAM_SPAN_RE.search(paragraph):
        paragraph = SPAM_SPAN_RE.sub("", paragraph)
        # The footer is sometimes preceded by the site's list of other novels
        # it is promoting. Removing that chain is only safe HERE, inside a
        # paragraph already known to carry site spam: the same "A、B、C、D"
        # shape occurs in real prose (ch.673 lists martial siblings, then
        # weapons), and a corpus-wide rule would delete both.
        paragraph = TITLE_CHAIN_RE.sub("", paragraph)
    paragraph = paragraph.strip()
    if not paragraph:
        return ""
    m = SPAM_RE.search(paragraph)
    if not m:
        return paragraph
    return paragraph[: m.start()].rstrip(DANGLING)


def clean_paragraphs(paragraphs):
    """Strip watermarks; drop paragraphs that were nothing but watermark."""
    out = []
    for p in paragraphs:
        cleaned = strip_spam(p)
        if not cleaned:
            continue
        # Residue only counts as residue if this paragraph actually carried
        # spam. Applied unconditionally it also deletes the novel's own scene
        # breaks -- 553 paragraphs that are nothing but an ellipsis run -- which
        # is not debris but punctuation the author wrote. Restricting it here
        # costs 43 fewer accepted chapters a rebuild.
        if cleaned != p and RESIDUE_RE.fullmatch(cleaned):
            continue
        out.append(cleaned)
    return out


def read_text(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def write_text(path, text):
    """Atomically replace a text file without sharing a temporary name.

    Several maintenance commands may run at once.  A fixed ``path + '.tmp'``
    lets two writers overwrite or remove each other's staging file before
    either reaches ``os.replace``.  Give every write its own sibling file so
    replacement stays atomic on Windows too.
    """
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=".%s." % os.path.basename(path), suffix=".tmp", dir=directory,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, OSError):
        return default


def save_json(path, obj):
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=".%s." % os.path.basename(path), suffix=".tmp", dir=directory,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------
# Input fingerprints -- "is this generated task still describing the files it
# was built from?"
# --------------------------------------------------------------------------

# Deliberately NOT mtime. Four independent ways mtime gets this wrong here:
#   * write_text() does os.replace on every call, so finalize.py normalising a
#     title or termfix.py replacing one word bumps mtime on a file whose
#     task-relevant content did not change;
#   * glossary_merge.py rewrites glossary_v2.json on every wave -- under an
#     mtime rule every task in the project is stale after each merge;
#   * shutil.move (qc_report.py --quarantine) PRESERVES mtime, so a file
#     restored from ru_rejected/ or from a backup looks older than the task
#     that was built from a completely different text;
#   * mtime answers "was the file touched", never "what changed".
# A content hash answers the actual question and costs one file read.


def text_fingerprint(path):
    """sha1 of a file's paragraphs, or None when the file is missing.

    Hashes the DECODED, split text rather than the bytes. Line endings in this
    corpus are mixed -- write_text() opens without newline= so on Windows it
    emits CRLF, while chapters written straight by an agent's Write tool are
    LF. Byte hashing would call a chapter changed the moment a different tool
    rewrote it without touching a single word. read_text() opens in universal
    newline mode and split_paragraphs() drops trailing blanks, which is exactly
    the normalisation the text undergoes on its way into a task file.
    """
    if not os.path.exists(path):
        return None
    return blob_fingerprint("\n".join(split_paragraphs(read_text(path))))


def blob_fingerprint(text):
    """sha1 of an already-rendered string. 16 hex chars: collision-free at this
    scale and still readable when a human opens the index by hand."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def load_task_provenance(qc_dir):
    """index -> record, for QC tasks. Per-task sidecars are the source of truth.

    Why the sidecars and not index.json: index.json is rebuilt from scratch by
    every build_qc.py run and holds only that run's chapters, so a later
    `--only 131 137` erases the provenance of everything judged before it. That
    is the defect plans/04-provenance.md was written about. A sidecar has the
    same lifetime and the same granularity as the task file it describes: it is
    rewritten when, and only when, qc_NNNN.md is rewritten. That also removes
    the read-modify-write race a shared journal would have.

    index.json is still read first, purely so records written before sidecars
    existed are not lost; any sidecar overrides it.
    """
    out = {}
    for e in load_json(os.path.join(qc_dir, "index.json"), []) or []:
        if isinstance(e, dict) and isinstance(e.get("index"), int):
            out[e["index"]] = e
    meta_dir = os.path.join(qc_dir, "meta")
    if os.path.isdir(meta_dir):
        for name in sorted(os.listdir(meta_dir)):
            if not name.endswith(".json"):
                continue
            m = load_json(os.path.join(meta_dir, name))
            if isinstance(m, dict) and isinstance(m.get("index"), int):
                out[m["index"]] = m
    return out


# --------------------------------------------------------------------------
# Glossary
# --------------------------------------------------------------------------

JUNK_RU_MARKER = "[ПЕРЕВОД_НУЖЕН"
AUTO_CATEGORY = "Авто-обнаруженный термин"


def load_glossary():
    """Return (curated_terms, auto_terms, pronoun_rules).

    The builder appended every frequent CJK n-gram it saw, so ~most entries are
    grammar fragments with a placeholder `ru`. Those must never reach the model:
    they teach it to emit "[ПЕРЕВОД_НУЖЕН: ...]" into the prose.
    """
    raw = load_json(GLOSSARY_FILE, {}) or {}
    pronoun_rules = raw.get("pronoun_rules", {})
    curated, auto = [], []
    for e in raw.get("glossary", []):
        cn, ru = (e.get("cn") or "").strip(), (e.get("ru") or "").strip()
        if not cn or not ru or JUNK_RU_MARKER in ru:
            continue
        (auto if e.get("category") == AUTO_CATEGORY else curated).append(e)
    return curated, auto, pronoun_rules


def relevant_terms(terms, source_text, limit=120):
    """Glossary entries whose Chinese form actually occurs in this chapter.

    Longest-first so 蘇蘇米 wins over 蘇; most-mentioned first on ties.
    """
    hits = [t for t in terms if t["cn"] in source_text]
    hits.sort(key=lambda t: (-len(t["cn"]), -(t.get("total_mentions") or 0)))
    return hits[:limit]


# --------------------------------------------------------------------------
# Translation validation
# --------------------------------------------------------------------------

# Calibration note. The first version of these bounds was derived from a single
# sample -- the one pre-existing chapter -- which later turned out to be missing
# 9 of its 78 paragraphs, i.e. itself condensed. Norms taken from a defective
# sample are worse than no norms.
#
# Re-derived from a verified full translation (ch3, 47/47 paragraphs, expansion
# uniform across short and long paragraphs, no single-paragraph outlier above
# 4.7x): a faithful CN->RU rendering of this text runs about 3.0-3.4x in
# characters. The band below is deliberately wide because it is only a smoke
# test; PARAGRAPH COUNT is the load-bearing check, being exact and unambiguous.
MIN_CHAR_RATIO = 2.0
MAX_CHAR_RATIO = 4.5

# Paragraph count must match EXACTLY. This is what actually caught the
# fabrications: a retelling keeps 10-27% of the paragraphs, and no amount of
# stylistic variation moves this number.
#
# A tolerance band was worse than useless here: +-2% let a 64-vs-63 mismatch
# through the count check, and the misalignment then surfaced as seven bogus
# per-paragraph outliers -- one defect reported as seven unrelated ones.

# Was 8, on the theory that parenthetical glosses ("Мали-гриб (魔莉菇)") would
# need the room. 636 accepted chapters later that has never once happened: 635
# contain no CJK at all, and the single exception carries exactly one character
# -- 井 in chapter 471, where the glyph IS the object being described and
# removing it would destroy the sentence.
#
# The slack was not free. Seven characters of a duplicated Chinese title passed
# acceptance silently during the thinking-budget trial, purely because 7 < 8.
MAX_CJK_CHARS = 2

# Per-paragraph outlier bounds. Matching paragraph counts still permits a
# paragraph rendered as "..." or one padded into an essay, so check alignment
# too -- but only when the counts line up, otherwise indexes are meaningless.
PARA_OUTLIER_LOW = 0.35
PARA_OUTLIER_HIGH = 9.0
MAX_OUTLIERS = 2


def validate_translation(source, translated):
    """Return a list of problems. Empty list == passes.

    These are exactly the checks that would have caught the fabricated files:
    a retelling is short and drops most paragraphs.
    """
    problems = []
    src_p = split_paragraphs(source)
    ru_p = split_paragraphs(translated)

    if not translated.strip():
        return ["пустой перевод"]

    if JUNK_RU_MARKER in translated:
        problems.append("в переводе остался placeholder из глоссария")

    # Paragraph markers are scaffolding for finalize.py and must never survive
    # into the book. A chapter killed between the agent's Write and the finalize
    # step keeps them, and every other check passes it happily: the counts match
    # and the text is Russian. Chapter 83 was accepted that way.
    left_markers = sum(1 for p in ru_p if PARA_MARKER_RE.match(p))
    if left_markers:
        problems.append(
            "остались служебные маркеры абзацев [N]: %d — глава не финализирована"
            % left_markers
        )

    # Tool scaffolding from the agent's own output envelope. Found in 16 already
    # accepted chapters, the oldest going back to wave 1: a bare "</content>" on
    # its own line at the end of the file. Every other check passed it happily --
    # the tag glues onto the last paragraph so the count never moves, and it is
    # Latin text so the CJK check stays silent. Same blind spot as the paragraph
    # markers above, which is why it belongs right next to them.
    stray = STRAY_TAG_RE.findall(translated)
    if stray:
        problems.append(
            "в переводе остались служебные теги агента (%d): %s"
            % (len(stray), ", ".join(sorted(set(stray))[:3]))
        )

    cjk = len(CJK_RE.findall(translated))
    if cjk > MAX_CJK_CHARS:
        problems.append("непереведённых китайских иероглифов: %d" % cjk)

    char_ratio = len(translated) / max(len(source), 1)
    if char_ratio < MIN_CHAR_RATIO:
        problems.append(
            "перевод слишком короткий: %.2fx от оригинала (норма %.1f-%.1f) "
            "- признак пересказа, а не перевода" % (char_ratio, MIN_CHAR_RATIO, MAX_CHAR_RATIO)
        )
    elif char_ratio > MAX_CHAR_RATIO:
        problems.append(
            "перевод подозрительно длинный: %.2fx от оригинала (норма %.1f-%.1f)"
            % (char_ratio, MIN_CHAR_RATIO, MAX_CHAR_RATIO)
        )

    if len(ru_p) < len(src_p):
        problems.append(
            "потеряны абзацы: %d из %d (не хватает %d)"
            % (len(ru_p), len(src_p), len(src_p) - len(ru_p))
        )
    elif len(ru_p) > len(src_p):
        problems.append(
            "абзацев больше, чем в оригинале: %d против %d (лишних %d)"
            % (len(ru_p), len(src_p), len(ru_p) - len(src_p))
        )
    else:
        # Only meaningful when the counts match exactly -- otherwise paragraph
        # i on one side is not paragraph i on the other, and every comparison
        # after the first mismatch is noise.
        # Counts line up, so paragraph i corresponds to paragraph i. Now a
        # single gutted or padded paragraph becomes visible -- equal counts
        # alone would hide it.
        outliers = []
        for i, (src, ru) in enumerate(zip(src_p, ru_p)):
            if len(src) < 12:
                continue  # "?" -> "Что?" is a huge ratio and perfectly correct
            r = len(ru) / len(src)
            if r < PARA_OUTLIER_LOW or r > PARA_OUTLIER_HIGH:
                outliers.append((i + 1, round(r, 2)))
        if len(outliers) > MAX_OUTLIERS:
            shown = ", ".join("абз.%d=%.2fx" % (n, r) for n, r in outliers[:5])
            problems.append(
                "абзацы не соответствуют оригиналу по объёму (%d шт): %s" % (len(outliers), shown)
            )

    return problems


def translation_stats(source, translated):
    src_p, ru_p = split_paragraphs(source), split_paragraphs(translated)
    return {
        "src_chars": len(source),
        "ru_chars": len(translated),
        "char_ratio": round(len(translated) / max(len(source), 1), 3),
        "src_paras": len(src_p),
        "ru_paras": len(ru_p),
        "cjk_left": len(CJK_RE.findall(translated)),
    }
