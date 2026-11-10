#!/usr/bin/env python3
"""Prepare semantic proof-reading tasks for the `proofreader` agent.

verify.py is a structural audit: paragraph counts, volume ratio, leftover CJK.
It cannot see whether the Russian says what the Chinese said. A chapter can be
gutted paragraph-by-paragraph, invent events, or rename a glossary creature and
still pass every check in it.

Reading every chapter with an agent would roughly double the project budget
(~27.4k/chapter to translate, ~15k to proof-read). So this script does not hand
the agent a queue -- it ranks chapters by mechanical risk signals that cost no
tokens at all, and hands over only the top of that ranking plus a small random
sample. The sample is what keeps the ranking honest: if random chapters come
back clean while ranked ones come back defective, the signals work; if random
chapters come back defective too, the signals are missing something.

The control sample is judged BLIND. The task file used to say "this chapter was
drawn at random, a clean verdict is expected here", which told the one group
whose whole purpose is an unbiased second opinion what answer was wanted. A
control group that has been told the expected answer measures its own hint, not
the corpus. The pick reason now lives only in qc_batches/meta/, which no agent
reads.

    python build_qc.py --count 10            # 10 most suspicious
    python build_qc.py --count 10 --sample 3 # ... plus 3 random, as a control
    python build_qc.py --only 42 43
    python build_qc.py --report              # ranking only, write nothing
    python build_qc.py --calibrate           # measure the signals, write nothing
    python build_qc.py --adopt               # record fingerprints of existing tasks
    python build_qc.py --rebuild 7 198       # re-render task files, keep provenance
"""

import argparse
import collections
import datetime
import hashlib
import math
import os
import random
import re
import sys

from lang import LANG
from common import (
    ROOT,
    blob_fingerprint,
    chapter_path,
    load_excluded,
    load_glossary,
    load_json,
    load_task_provenance,
    read_text,
    relevant_terms,
    ru_path,
    save_json,
    split_paragraphs,
    text_fingerprint,
    validate_translation,
    write_text,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

QC_DIR = os.path.join(ROOT, "qc_batches")
META_DIR = os.path.join(QC_DIR, "meta")
VERDICT_DIR = os.path.join(ROOT, "qc_verdicts")
GLOSSARY_V2 = os.path.join(ROOT, "glossary_v2.json")
CALIBRATION_FILE = os.path.join(ROOT, "qc_calibration.json")

# The rules live in .claude/agents/proofreader.md, not here: repeating them per
# chapter would re-send them on every turn of every agent. Same reasoning as
# build_batches.py.
TEMPLATE = """# Приёмка главы {idx:04d}

Записать вердикт в: `{out_path}`

Структурную проверку глава прошла: {paragraphs} абзацев в оригинале и в переводе,
коэффициент объёма {ratio:.2f}x.

{flag_block}
{glossary_block}
## ОРИГИНАЛ И ПЕРЕВОД, АБЗАЦ К АБЗАЦУ

Номера абзацев совпадают в обеих колонках — на них и ссылайся в вердикте.

{pairs}
"""

# --------------------------------------------------------------------------
# Risk signals -- all mechanical, all free
# --------------------------------------------------------------------------

# The site's greeting and vote solicitations, as they look AFTER a translator
# rendered them into Russian prose. Three chapters of wave 1 shipped this way:
# strip_spam() only removes what it recognises in Chinese, and the marker list
# was built from early chapters while the site changed its footer later on.
# Patterns are multi-word on purpose -- a bare "сайт" fires on real prose once
# the story reaches its technological arcs.
RU_SPAM_RE = re.compile(
    "|".join([
        r"мобильн\w*\s+верси",
        r"адрес\w*\s+сайт",
        r"нов\w+\s+домен",
        r"(?:рекомендательн|ежемесячн)\w*\s+билет",
        r"добро\s+пожаловать[,\s]+\w*\s*читател",
        r"приятн\w+\s+чтени",
        r"бесплатн\w+\s+чтени",
        r"без\s+рекламы",
        r"последн\w+\s+глав\w+\s+на\s+",
        r"продолжени\w*\s+на\s+сайт",
        r"добав\w+\s+в\s+закладки",
    ]),
    re.IGNORECASE,
)

# Per-paragraph outliers below MAX_OUTLIERS are invisible to verify.py by
# design -- it needs slack for legitimately terse lines. One or two of them is
# exactly where a single gutted paragraph hides.
#
# Measured over 660 chapters, four of these five signals fire on at most ONE
# chapter each: outliers 1, spam 0, cjk 1, low ratio 0. Their weights are
# therefore untunable -- they multiply by zero -- and left exactly as they were.
# They cost nothing and stand as insurance for future batches. The only signal
# with data behind it is the glossary one, so that is the only one recalibrated.
W_OUTLIER = 3.0
W_GLOSSARY_MISS = 2.5
W_SPAM = 8.0        # documented past failure, always worth a look
W_LOW_RATIO = 2.0   # near the pass/fail edge, i.e. a condensed rendering
W_CJK = 1.5

# Proposed at 1.5 by plans/03-ranking.md §7.1 and measured at ZERO here.
# The plan's four confirming cases (0198, 0283 x2, 0288) are all terms the
# translation dropped entirely, and every one of them is already reported by
# glossary_misses with a weight that grows with the same occurrence count --
# so scoring underuse too would double one signal and call it two.
#
# What it does on the corpus, measured: 414 of 680 chapters, dominated by
# multi-word names that Russian prose shortens after first mention. 白骸龍
# "Белый скелетный дракон" alone fires on 116 chapters, 翡翠龍 on 83. That is
# how the language works, not drift. At weight 1.5 it flagged 553 of 661
# chapters and owned the top of the ranking outright.
#
# Kept, computed and shown to the proof-reader, because §7.2 is real: ch.0007
# names 冰結 three different ways and only the arithmetic shows it. But a signal
# with one usable example and a 60%-of-corpus fire rate does not get to order
# the queue. Raise this only when there are verdicts to measure it against.
W_UNDERUSE = 0.0

LOW_RATIO_EDGE = 2.6   # median across accepted chapters is 3.50

# --------------------------------------------------------------------------
# Russian morphology, such as it is
# --------------------------------------------------------------------------
#
# The previous version cut a fixed-length PREFIX off each word: keep
# max(5, len-4) characters. Two floors made that degenerate. For a five-letter
# word the formula returns the word itself, so the check became "does the
# dictionary form appear verbatim" -- and 122 glossary terms consist entirely
# of words that short. Measured casualties: Чибча/"Чибчу", Вождь/"вождя",
# Древо/"Древа", Поющий/"Поющего" all read as MISSING from chapters that
# rendered them perfectly. Words under five letters were dropped in silence,
# which zeroed the reliability of 23 terms outright (藍月 "Синяя луна" kept only
# the adjective) and got them filtered out of the check for good.
#
# So: strip a real ending from the list below, keeping at least MIN_CORE
# characters, and compare cores for EQUALITY rather than substring containment.
# Containment is what let "древ" match "древний"; equality does not.
ENDINGS = tuple(sorted(
    ("ого", "его", "ому", "ему", "ыми", "ими", "ая", "яя", "ое", "ее", "ые",
     "ие", "ый", "ий", "ой", "ей", "ом", "ем", "ах", "ях", "ам", "ям", "ов",
     "ев", "ью", "ия", "ии", "у", "ю", "ы", "и", "а", "я", "о", "е", "ь", "й"),
    key=len, reverse=True,
))
MIN_CORE = 4
VOWELS = "аеиоуыэюя"

# A word is "distinctive" when its core is rare across the translated corpus.
# "Парящие острова" -> "остров" is in half the book, "парящ" is not; asking for
# only ONE of the two (the old any()) let "плавучие острова" pass as canonical.
# Measured sweep over 666 chapters put TP/FP flat between 0.20 and 1.00, so the
# threshold was chosen on how many chapters get flagged (328 vs 407), not on the
# labels. That is an engineering judgement, not a measurement.
DF_DISTINCTIVE = 0.20

WORD_RE = re.compile(r"[А-Яа-яЁё]+")


def word_variants(word):
    """{core} plus a fleeting-vowel variant, for one Russian word.

    Russian drops е/о before the final consonant as a word declines --
    камень/камня, пловец/пловцы, огонь/огня. Nothing above models that, so both
    spellings of the core are produced and a match is any overlap between the
    two sides' variant sets.
    """
    w = word.lower().replace("ё", "е")
    core = w
    for e in ENDINGS:
        if w.endswith(e) and len(w) - len(e) >= MIN_CORE:
            core = w[: -len(e)]
            break
    out = {core}
    if len(core) >= 3 and core[-1] not in VOWELS and core[-2] in "ео":
        out.add(core[:-2] + core[-1])
    return out


def term_word_variants(term_ru):
    """One variant-set per word of a glossary term, longest words first.

    Order matters only for readability of reports; the check itself is a set
    operation. Words shorter than three letters carry no evidence and are
    dropped -- "Ат", "и", "на" would match half the corpus.
    """
    return [word_variants(w) for w in WORD_RE.findall(term_ru or "") if len(w) >= 3]


def ru_stems(term_ru):
    """All core variants of a term, flattened. Kept for callers that only ask
    'could this term be checked at all'."""
    out = set()
    for group in term_word_variants(term_ru):
        out |= group
    return out


def text_cores(text):
    """Counter of core variants over every word of a chapter.

    Each word contributes 1 to each of its own variants (deduplicated within
    the word), so counter[v] is "how many words in this chapter could be v".
    """
    c = collections.Counter()
    for w in WORD_RE.findall(text):
        if len(w) < 3:
            continue
        for v in word_variants(w):
            c[v] += 1
    return c


def standalone_terms(terms, source):
    """Terms occurring in the source outside every longer glossary term.

    relevant_terms() matches by plain substring, so 創造者 "occurs" in every
    chapter that mentions 琥珀創造者. Checking the short form's Russian stem then
    reports a miss against a chapter that rendered the long form perfectly --
    measured at 21 chapters for that one term alone, and the same fragment
    problem the drop list in glossary_overrides.json exists to fix.

    Masking longest-first leaves each term only where it stands on its own.
    """
    masked = source
    out = []
    for t in sorted(terms, key=lambda t: -len(t["cn"])):
        cn = t["cn"]
        if cn and cn in masked:
            out.append(t)
            masked = masked.replace(cn, " " * len(cn))
    return out


def corpus_df(records):
    """core variant -> share of translated chapters containing it."""
    seen = collections.Counter()
    for r in records:
        seen.update(set(r["ru_cores"]))
    n = max(len(records), 1)
    return {v: c / n for v, c in seen.items()}


def check_term(term_ru, cores, df):
    """Is this term present in a chapter, and how often? -> (present, ru_n).

    Two rules, in order:

      * if any word of the term is distinctive (rare across the corpus), ALL
        distinctive words must be present. This is what catches a swapped
        half: 浮空群島 rendered "плавучие острова" keeps the common word and
        loses the identifying one, and the old any() called that a match.
      * if every word is ordinary vocabulary, fall back to any(). Demanding all
        of "Гигантский клык" would fire on half the book.

    ru_n counts occurrences of the scarcest required word -- an upper bound on
    how many times the whole term can actually be in there.
    """
    groups = term_word_variants(term_ru)
    if not groups:
        return None, 0  # nothing checkable -- silence, not a miss
    distinctive = [g for g in groups
                   if max((df.get(v, 0.0) for v in g), default=0.0) <= DF_DISTINCTIVE]
    required, need_all = (distinctive, True) if distinctive else (groups, False)
    counts = [max((cores.get(v, 0) for v in g), default=0) for g in required]
    present = all(c > 0 for c in counts) if need_all else any(c > 0 for c in counts)
    hits = [c for c in counts if c > 0]
    return present, (min(hits) if (present and hits) else 0)


def paragraph_outliers(src_p, ru_p):
    """Same measure verify.py uses, but reported instead of thresholded."""
    out = []
    for i, (src, ru) in enumerate(zip(src_p, ru_p), 1):
        if len(src) < LANG.short_para:
            continue
        r = len(ru) / len(src)
        if r < 0.35 or r > 9.0:
            out.append((i, round(r, 2)))
    return out


# A term found canonically almost nowhere is not 500 defective chapters -- it is
# a broken check. Two causes, both common: the stemmer cannot follow the word
# ("племя Ат" never matches "племени Ат"), or the glossary entry is ordinary
# vocabulary that legitimately renders differently every time. Both fail
# uniformly across the corpus, while real drift is by definition local: the
# canon holds in most chapters and slips in a few.
#
# So reliability is measured per term against the whole corpus, and only terms
# the check demonstrably CAN follow are allowed to raise an alarm. This
# calibrates itself as the glossary and the translations grow, which hand-tuned
# stem lengths did not: three rounds of tuning left the fire rate at 43%.
MIN_TERM_EVIDENCE = 3    # fewer chapters than this is no basis for a rate
MIN_TERM_RELIABILITY = 0.6

# How far below the source's own usage the canon may fall before that is a
# signal in itself. See canon_underuse() for why this only fires on terms that
# ARE present.
#
# Tightened from the plan's (0.5, 2): at those values the line appeared on 414
# chapters and would have buried the signals that mean something. At (0.34, 4)
# it appears on the chapters where the shortfall is too large to be stylistic
# shortening, ch.0007 among them.
UNDERUSE_RATIO = 0.34
UNDERUSE_MIN_SRC = 4


def term_reliability(records):
    """{cn: found_rate} over chapters where the term stands on its own.

    Measured across the WHOLE corpus, deliberately. It used to be measured over
    whatever was left after chapters with verdicts were skipped, which made the
    denominator shrink with every verdict written -- so the score of every other
    chapter drifted between runs and no ranking was reproducible.
    """
    seen, found = collections.Counter(), collections.Counter()
    for r in records:
        for cn, st in r["term_stats"].items():
            seen[cn] += 1
            if st["present"]:
                found[cn] += 1
    return {cn: found[cn] / n for cn, n in seen.items() if n >= MIN_TERM_EVIDENCE}


def assess(idx, terms):
    """Risk profile of one translated chapter, or None if it is not eligible.

    Chapters that fail verify.py are excluded on purpose: they are already known
    to be broken and are going back to a translator regardless. Spending a
    proof-reader on them buys nothing.

    Everything glossary-shaped is deferred to score_records(): whether a term
    counts as missing depends on how common its words are across the whole
    corpus, which is not knowable from one chapter.
    """
    src_file, ru_file = chapter_path(idx), ru_path(idx)
    if not (os.path.exists(src_file) and os.path.exists(ru_file)):
        return None
    source, translated = read_text(src_file), read_text(ru_file)
    if validate_translation(source, translated):
        return None

    src_p, ru_p = split_paragraphs(source), split_paragraphs(translated)
    hits = relevant_terms(terms, source)
    standalone = standalone_terms(hits, source)
    outliers = paragraph_outliers(src_p, ru_p)
    spam = sorted({m.group(0) for m in RU_SPAM_RE.finditer(translated)})
    cjk = LANG.leftover(translated)
    ratio = len(translated) / max(len(source), 1)

    return {
        "index": idx,
        "ratio": ratio,
        "paragraphs": len(src_p),
        "terms": hits,
        "standalone": standalone,
        "ru_cores": text_cores(translated),
        "term_stats": {},      # filled by score_records()
        "missed": [],          # filled by score_records()
        "underused": [],       # filled by score_records()
        "outliers": outliers,
        "spam": spam,
        "cjk": cjk,
        "score": 0.0,
        "source": source,
        "translated": translated,
        "src_p": src_p,
        "ru_p": ru_p,
    }


def canon_underuse(r, rates):
    """Terms whose canon IS in the chapter, but far less often than the source.

    §7.1 of plans/03-ranking.md proposed this for terms the translator replaced
    wholesale (爆甲 fifteen times in the source, zero in the translation). Those
    are already reported by glossary_misses with a weight that grows with the
    same occurrence count, so counting them twice would just double one signal
    and call it two. Every one of the four cases the plan measured is such a
    case.

    What is NOT already covered is the chapter that uses the canon once and
    then invents synonyms -- ch.0007 names 冰結 "обледенение", "замерзание" and
    "лёд" in the same scene. The canon is present, so no miss fires; only the
    arithmetic shows it. That is the gap this fills, so it fires only on terms
    that are present.
    """
    out = []
    for cn, st in sorted(r["term_stats"].items()):
        if not st["present"]:
            continue
        src_n, ru_n = st["src_n"], st["ru_n"]
        if src_n < UNDERUSE_MIN_SRC:
            continue
        if rates.get(cn, 0.0) < MIN_TERM_RELIABILITY:
            continue
        if ru_n >= math.ceil(src_n * UNDERUSE_RATIO):
            continue
        out.append((st["term"], src_n, ru_n))
    return out


def score_records(records):
    """Evaluate every glossary term, then score. Returns the reliability rates."""
    df = corpus_df(records)

    for r in records:
        for t in r["standalone"]:
            present, ru_n = check_term(t.get("ru", ""), r["ru_cores"], df)
            if present is None:
                continue  # uncheckable term, no evidence either way
            r["term_stats"][t["cn"]] = {
                "term": t,
                "present": present,
                "src_n": r["source"].count(t["cn"]),
                "ru_n": ru_n,
            }

    rates = term_reliability(records)

    for r in records:
        # A miss weighs by how sure we are the check works (rate) and by how
        # loudly the source says the word. A term named once and not found is a
        # coin flip (37% precision, measured); a term named fifteen times and
        # never found is not. Both used to weigh exactly 2.5, which is half the
        # reason the whole corpus held only two distinct scores.
        r["missed"] = [
            st["term"] for cn, st in sorted(r["term_stats"].items())
            if not st["present"] and rates.get(cn, 0.0) >= MIN_TERM_RELIABILITY
        ]
        r["underused"] = canon_underuse(r, rates)

        miss_score = 0.0
        for t in r["missed"]:
            st = r["term_stats"][t["cn"]]
            miss_score += (W_GLOSSARY_MISS * rates.get(t["cn"], 0.0)
                           * (1 + math.log2(max(st["src_n"], 1))))
        under_score = sum(
            W_UNDERUSE * rates.get(t["cn"], 0.0) * (1 + math.log2(src_n - ru_n + 1))
            for t, src_n, ru_n in r["underused"]
        )

        r["score"] = round(
            W_OUTLIER * len(r["outliers"])
            + miss_score
            + under_score
            + W_SPAM * len(r["spam"])
            + W_CJK * r["cjk"]
            + (W_LOW_RATIO * (LOW_RATIO_EDGE - r["ratio"]) / LOW_RATIO_EDGE
               if r["ratio"] < LOW_RATIO_EDGE else 0.0),
            3,
        )
    return rates


def jitter(seed, idx):
    """Deterministic pseudo-random tiebreak in [0, 1).

    Ties used to be broken by chapter number, and with only two distinct scores
    in the corpus that made the queue a strictly ascending walk from chapter 19:
    of 160 flagged chapters 69 were numbered 400+, and they would have reached a
    proof-reader on the fifteenth batch of ten. Reproducible under a fixed
    --seed, uniform across the book, and needs to know nothing about how large
    the tied group is.
    """
    return int(hashlib.blake2b(b"%d:%d" % (seed, idx),
                               digest_size=8).hexdigest(), 16) / 2.0 ** 64


# --------------------------------------------------------------------------
# Pronoun drift -- reported, never scored
# --------------------------------------------------------------------------
#
# Exactly one confirming example exists (ch.0344), and its "defect" turned out
# to be a wrong gender field in the glossary rather than wrong prose. A signal
# with one example and a known false positive gets no weight; it gets a line in
# the task so the human-shaped reader can look. Composite gender fields
# ("Он / Его (индивид) / Они (вид)") are skipped rather than guessed at.
PRONOUNS = {
    "ж": {"она", "её", "ее", "ей", "неё", "нее", "ею", "ней"},
    "м": {"он", "его", "ему", "нём", "нем", "него", "им", "ним"},
    "с": {"оно", "его", "ему", "нём", "нем", "него"},
}
GENDER_OF = {"она / её": "ж", "он / его": "м", "оно / его": "с"}
PRONOUN_WINDOW = 40

# Off, and the measurement is the reason. Implemented as plans/03-ranking.md
# §7.3 specifies -- weight zero, one line in the task -- then measured on the
# corpus: it fires on 636 of 722 chapters (88%) and produces 1730 lines, two or
# three on a typical task. A hint that appears on almost every chapter is not a
# hint; it teaches the proof-reader to skip the whole flag block, which costs
# the signals that do work.
#
# The evidence behind it is worse than thin, it points the other way. Its single
# confirming example was ch.0344 -- and that turned out to be a wrong `gender`
# field in the glossary, not wrong prose: "Глава Чумы" is a feminine Russian
# noun and the text agreeing with it feminine is correct. So the signal has zero
# confirmed hits and one confirmed false positive.
#
# Kept, not deleted: a window that also required the term to be the nearest
# preceding noun would be worth measuring once there are verdicts to measure it
# against. Turn on here, do not scatter the switch.
PRONOUN_DRIFT_IN_TASKS = False


def pronoun_drift(a, df):
    """[(term, expected, found)] -- pronouns of a clashing gender near a term."""
    words = WORD_RE.findall(a["translated"])
    lowered = [w.lower().replace("ё", "е") for w in words]
    variants = [word_variants(w) for w in lowered]
    out = []
    for t in a["standalone"]:
        g = GENDER_OF.get((t.get("gender") or "").strip().lower())
        if not g:
            continue
        groups = term_word_variants(t.get("ru", ""))
        key = None
        for grp in groups:
            if max((df.get(v, 0.0) for v in grp), default=1.0) <= DF_DISTINCTIVE:
                key = grp
                break
        if not key:
            continue
        clashing = set()
        for other, pron in PRONOUNS.items():
            if other != g:
                clashing |= pron
        clashing -= PRONOUNS[g]
        for i, vs in enumerate(variants):
            if not (vs & key):
                continue
            near = set(lowered[i + 1: i + 1 + PRONOUN_WINDOW]) & clashing
            if near:
                out.append((t, t.get("gender", ""), sorted(near)[:4]))
                break
    return out


# --------------------------------------------------------------------------
# Task file
# --------------------------------------------------------------------------

def flag_block(a, df=None):
    """The 'what the script already noticed' section of a task.

    Takes no pick reason. It used to, and printed "this chapter was drawn at
    random -- a clean verdict is expected here" for the control group, which is
    the one group that must not be told what is expected of it. See the module
    docstring; the pick reason now lives in qc_batches/meta/.
    """
    lines = ["## На что скрипт уже обратил внимание", ""]
    if a["spam"]:
        lines.append(
            "- **Возможна реклама сайта в прозе**, совпадения: %s"
            % ", ".join("«%s»" % s for s in a["spam"])
        )
    if a["missed"]:
        # The occurrence counts turn "look through the chapter for this" into
        # "the original says it fifteen times and the translation never does".
        bits = []
        for t in a["missed"][:12]:
            st = a["term_stats"].get(t["cn"], {})
            bits.append("%s = %s (в оригинале %d, в переводе 0)"
                        % (t["cn"], t["ru"], st.get("src_n", 0)))
        lines.append(
            "- **Термины глоссария не найдены в переводе** (%d): %s. "
            "Проверь, не придумал ли переводчик своё имя."
            % (len(a["missed"]), "; ".join(bits))
        )
    if a["underused"]:
        lines.append(
            "- **Канон есть, но реже, чем в оригинале** (%d): %s. "
            "Обычно значит, что часть вхождений названа другими словами."
            % (len(a["underused"]),
               "; ".join("%s = %s (в оригинале %d, в переводе ~%d)"
                         % (t["cn"], t["ru"], s, u)
                         for t, s, u in a["underused"][:8]))
        )
    if a["outliers"]:
        lines.append(
            "- **Абзацы с расходящимся объёмом** (%d): %s. Смотри их в первую "
            "очередь: сюда прячется выхолощенный абзац."
            % (len(a["outliers"]),
               ", ".join("абз.%d=%.2fx" % (n, r) for n, r in a["outliers"][:8]))
        )
    if a["ratio"] < LOW_RATIO_EDGE:
        lines.append(
            "- **Перевод короче обычного**: %.2fx против типичных 3.5x. "
            "Признак сжатия по всей главе." % a["ratio"]
        )
    if a["cjk"]:
        lines.append("- Осталось %s: %d" % (LANG.leftover_name, a["cjk"]))
    if df is not None and PRONOUN_DRIFT_IN_TASKS:
        for t, gender, pron in pronoun_drift(a, df)[:4]:
            lines.append(
                "- Рядом с «%s» (род по глоссарию: %s) стоят местоимения %s. "
                "Сигнал сырой и часто ложный — местоимение может относиться к "
                "другому существительному, а поле «род» в глоссарии бывает "
                "проставлено по местоимению оригинала, а не по русскому имени. Проверь, "
                "но не правь текст только ради согласия с глоссарием."
                % (t["ru"], gender, ", ".join("«%s»" % p for p in pron))
            )
    if len(lines) == 2:
        lines.append("- Ничего не сработало. Читай главу без подсказок.")
    lines.append("")
    lines.append(
        "Этот список — подсказка, а не задание. Сигналы бывают ложными, "
        "и дефекты бывают там, где ничего не сработало."
    )
    lines.append("")
    lines.append(
        "Отдельно, независимо от сигналов: смотри на отглагольные конструкции "
        "(«орган для производства саранчи» вместо «рождает саранчу») и на "
        "повторы одного союза в соседних придаточных. Механически они не "
        "ловятся вообще."
    )
    lines.append("")
    return "\n".join(lines)


def glossary_block(terms):
    if not terms:
        return "## Канон для этой главы\n\nСовпадений с глоссарием нет.\n"
    lines = ["## Канон для этой главы — так термины должны звучать в переводе", ""]
    for t in terms:
        bits = ["**%s** = %s" % (t["cn"], t["ru"])]
        if t.get("gender"):
            bits.append("род: %s" % t["gender"])
        lines.append("- " + " | ".join(bits))
    lines.append("")
    return "\n".join(lines)


def pairs_block(src_p, ru_p):
    out = []
    for n, (src, ru) in enumerate(zip(src_p, ru_p), 1):
        out.append("### [%d]\n\nCN: %s\n\nRU: %s" % (n, src, ru))
    return "\n\n".join(out)


# --------------------------------------------------------------------------
# Provenance and staleness
# --------------------------------------------------------------------------

def task_path(idx):
    return os.path.join(QC_DIR, "qc_%04d.md" % idx)


def verdict_path(idx):
    return os.path.join(VERDICT_DIR, "qc_%04d.json" % idx)


def task_fingerprint(idx, terms, hits=None):
    """Fingerprint of the three real inputs of a task. Does not render the task.

    Rendering the task and diffing it against the file on disk cannot work:
    flag_block depends on term reliability measured over the whole run and, as
    it used to, on the pick reason -- so the same unchanged chapter produces a
    different task in a different run, and every task would read as stale
    forever. What is stable is the INPUTS, and there are exactly three of them.

    The canon input is the RENDERED glossary block, not glossary_v2.json.
    glossary_merge.py rebuilds that file after every translated chapter, so
    hashing it would declare every task in the project stale several times an
    hour. The block only moves when a term relevant to THIS chapter moves.
    """
    src_file = chapter_path(idx)
    if not os.path.exists(src_file):
        return None
    if hits is None:
        hits = relevant_terms(terms, read_text(src_file))
    return {
        "src": text_fingerprint(src_file),
        "ru": text_fingerprint(ru_path(idx)),
        "canon": blob_fingerprint(glossary_block(hits)),
    }


def changed_inputs(old, new):
    """Which of the three inputs moved, in words, for the report."""
    if not old:
        return ["отпечатка нет"]
    names = {"src": "источник", "ru": "перевод", "canon": "канон"}
    out = [ru for key, ru in names.items() if old.get(key) != (new or {}).get(key)]
    return out or ["ничего"]


def write_task(a, picked_by, run, df=None, preserve=None):
    """Render one task file and its provenance sidecar. Returns the index entry.

    Split out of main() so it can be driven by a test without a corpus, and so
    that the task and its sidecar are written by the same call -- the property
    the whole design rests on. See common.load_task_provenance for why the
    sidecar, and not index.json, is the source of truth.
    """
    idx = a["index"]
    os.makedirs(QC_DIR, exist_ok=True)
    os.makedirs(META_DIR, exist_ok=True)
    write_text(
        task_path(idx),
        TEMPLATE.format(
            idx=idx,
            out_path="qc_verdicts/qc_%04d.json" % idx,
            paragraphs=a["paragraphs"],
            ratio=a["ratio"],
            flag_block=flag_block(a, df),
            glossary_block=glossary_block(a["terms"]),
            pairs=pairs_block(a["src_p"], a["ru_p"]),
        ),
    )
    entry = {
        "index": idx,
        "task_file": "qc_batches/qc_%04d.md" % idx,
        "out_file": "qc_verdicts/qc_%04d.json" % idx,
        "picked_by": picked_by,
        "score": a["score"],
        "flagged": a["score"] > 0,
        "task_chars": len(a["source"]) + len(a["translated"]),
        "fp": a.get("fp"),
    }
    # Fields that record a historical fact, not a current measurement. `flagged`
    # says whether the chapter had risk signals AT THE MOMENT it was judged --
    # that is the whole content of the control group -- and recomputing it under
    # today's scoring would quietly rewrite the past. Re-rendering a task file
    # after the scoring changed must not move chapters between the "no signals"
    # and "signals below the cut" rows of the report.
    entry.update(preserve or {})
    save_json(os.path.join(META_DIR, "qc_%04d.json" % idx), dict(entry, **run))
    return entry


def run_passport(args):
    now = datetime.datetime.now(datetime.timezone.utc)
    return {
        "batch": now.strftime("%Y%m%dT%H%M%SZ"),
        "issued_at": now.isoformat(timespec="seconds"),
        "seed": args.seed,
        "count": args.count,
        "sample": args.sample,
        "redo": bool(args.redo or args.redo_stale),
        "argv": sys.argv[1:],
        "source": "build_qc",
    }


# --------------------------------------------------------------------------
# Calibration
# --------------------------------------------------------------------------

def calibrate(assessed, rates, verdict_of):
    """Measure the signals against qc_calibration.json. Writes nothing."""
    cal = load_json(CALIBRATION_FILE, {}) or {}
    confirmed = {(int(p["chapter"]), p["cn"]) for p in cal.get("confirmed", [])}
    cleared = {(int(p["chapter"]), p["cn"]) for p in cal.get("cleared", [])}
    # Any signal raised on a chapter the proof-reader passed is a false alarm,
    # by definition. Deriving this instead of listing it means the test gets
    # stronger with every verdict written, without anyone editing the file.
    for idx, v in verdict_of.items():
        if v == "ok":
            for a in assessed:
                if a["index"] == idx:
                    for t in a["missed"]:
                        if (idx, t["cn"]) not in confirmed:
                            cleared.add((idx, t["cn"]))

    tp = unlabelled = 0
    fps = []
    for a in assessed:
        for t in a["missed"]:
            key = (a["index"], t["cn"])
            if key in confirmed:
                tp += 1
            elif key in cleared:
                fps.append("гл.%04d/%s" % (a["index"], t["cn"]))
            else:
                unlabelled += 1
    fp = len(fps)
    caught = sum(1 for a in assessed for t in a["missed"]
                 if (a["index"], t["cn"]) in confirmed)

    print("")
    print("=== КАЛИБРОВКА СИГНАЛОВ ===")
    print("Размеченных пар (глава, термин): подтверждено %d, опровергнуто %d"
          % (len(confirmed), len(cleared)))
    total = tp + fp
    print("Точность по терминам      : TP %d / FP %d = %s"
          % (tp, fp, "%.0f%%" % (100.0 * tp / total) if total else "н/д"))
    if fps:
        print("  ложные: %s" % ", ".join(sorted(fps)))
    print("  не размечено (неизвестность, не ложное срабатывание): %d" % unlabelled)
    print("Recall по подтверждённым  : %d из %d" % (caught, len(confirmed)))
    missed_pairs = sorted(confirmed - {(a["index"], t["cn"])
                                       for a in assessed for t in a["missed"]})
    if missed_pairs:
        print("  НЕ сработали: %s"
              % ", ".join("гл.%04d/%s" % (i, cn) for i, cn in missed_pairs))

    scores = [a["score"] for a in assessed if a["score"] > 0]
    groups = collections.Counter(round(s, 3) for s in scores)
    print("Вырожденность балла       : различных ненулевых значений %d, "
          "крупнейшая ничья %d"
          % (len(groups), max(groups.values()) if groups else 0))

    top = [a["index"] for a in assessed[:40]]
    if top:
        srt = sorted(top)
        print("Разброс по книге (первые 40): min %d, медиана %d, max %d"
              % (srt[0], srt[len(srt) // 2], srt[-1]))

    print("Срабатываний по сигналам:")
    for name, fn in (("термины не найдены", lambda a: len(a["missed"])),
                     ("канон недоиспользован", lambda a: len(a["underused"])),
                     ("выбросы по абзацам", lambda a: len(a["outliers"])),
                     ("реклама", lambda a: len(a["spam"])),
                     ("остатки оригинала", lambda a: a["cjk"]),
                     ("низкий коэффициент",
                      lambda a: 1 if a["ratio"] < LOW_RATIO_EDGE else 0)):
        n = sum(1 for a in assessed if fn(a))
        print("  %-24s глав %4d" % (name, n))

    # AUC over the labelled chapters: does a higher score mean a worse chapter?
    pos = [a["score"] for a in assessed if verdict_of.get(a["index"]) == "fix"]
    neg = [a["score"] for a in assessed if verdict_of.get(a["index"]) == "ok"]
    if pos and neg:
        wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
        print("AUC (fix против ok), %d/%d глав: %.3f"
              % (len(pos), len(neg), wins / (len(pos) * len(neg))))
        print("  Осторожно: выборка крошечная и смещена — эти главы попали в "
              "приёмку по этому же баллу. Числу верить нельзя.")


def load_verdict_map():
    out = {}
    if not os.path.isdir(VERDICT_DIR):
        return out
    for name in sorted(os.listdir(VERDICT_DIR)):
        if not name.endswith(".json"):
            continue
        d = load_json(os.path.join(VERDICT_DIR, name))
        if isinstance(d, dict):
            try:
                out[int(d.get("chapter", name[3:7]))] = str(d.get("verdict", "")).lower()
            except (TypeError, ValueError):
                continue
    return out


# --------------------------------------------------------------------------

def do_adopt(terms):
    """Record fingerprints for tasks already on disk. One-off migration.

    Tasks built before fingerprints existed have no record, and the honest
    default for "no record" is "assume stale". Without this every one of them
    would sit in the report forever. Provenance already on file is preserved.
    """
    prov = load_task_provenance(QC_DIR)
    os.makedirs(META_DIR, exist_ok=True)
    n = 0
    for name in sorted(os.listdir(QC_DIR)) if os.path.isdir(QC_DIR) else []:
        if not (name.startswith("qc_") and name.endswith(".md")):
            continue
        idx = int(name[3:7])
        fp = task_fingerprint(idx, terms)
        if fp is None:
            print("    гл.%04d: источника нет, пропущено" % idx)
            continue
        rec = dict(prov.get(idx) or {})
        rec.update({
            "index": idx,
            "task_file": "qc_batches/qc_%04d.md" % idx,
            "out_file": "qc_verdicts/qc_%04d.json" % idx,
            "picked_by": rec.get("picked_by", "?"),
            "fp": fp,
            "fp_adopted_at": datetime.datetime.now(
                datetime.timezone.utc).isoformat(timespec="seconds"),
        })
        save_json(os.path.join(META_DIR, "qc_%04d.json" % idx), rec)
        n += 1
    print("[+] Отпечатки записаны для %d заданий, лежащих на диске." % n)
    print("    Пометки отбора не тронуты. Утраченные (\"?\") впиши вручную "
          "в поле picked_by файлов qc_batches/meta/qc_NNNN.json")


def main():
    ap = argparse.ArgumentParser(description="Сборка заданий на смысловую приёмку")
    ap.add_argument("--count", type=int, default=10, help="сколько самых подозрительных взять")
    ap.add_argument("--sample", type=int, default=0, help="сколько добавить случайных, для контроля")
    ap.add_argument("--only", type=int, nargs="*")
    ap.add_argument("--redo", action="store_true", help="не пропускать главы с готовым вердиктом")
    ap.add_argument("--redo-stale", action="store_true",
                    help="вернуть в очередь главы, чей вердикт вынесен по устаревшему снимку")
    ap.add_argument("--rebuild", type=int, nargs="*",
                    help="переиздать файлы заданий по этим главам, сохранив пометку отбора "
                         "(для случая «шаблон задания изменился, а вердикт остаётся в силе»)")
    ap.add_argument("--adopt", action="store_true",
                    help="разово записать отпечатки для уже лежащих заданий, ничего не пересобирая")
    ap.add_argument("--report", action="store_true", help="только рейтинг, ничего не записывать")
    ap.add_argument("--calibrate", action="store_true",
                    help="померить сигналы по qc_calibration.json, ничего не записывать")
    ap.add_argument("--seed", type=int, default=0,
                    help="зерно: и случайной выборки, и разрыва ничьих в рейтинге")
    args = ap.parse_args()

    v2 = load_json(GLOSSARY_V2) or {}
    terms = v2.get("glossary") or load_glossary()[0]
    print("[+] Глоссарий: %d терминов" % len(terms))

    if args.adopt:
        do_adopt(terms)
        return

    excluded = load_excluded()
    all_indexes = sorted(
        int(f[8:-4])
        for f in os.listdir(os.path.join(ROOT, "ru"))
        if f.startswith("chapter_") and f.endswith(".txt")
    )
    all_indexes = [i for i in all_indexes if i not in excluded]
    wanted = set(args.only) if args.only else None
    if args.rebuild:
        wanted = set(args.rebuild)

    # Every eligible chapter is assessed, always -- including ones that already
    # have a verdict. Term reliability is a corpus-wide statistic, and it used
    # to be computed over "whatever chapters have no verdict yet", so it moved
    # every time a verdict was written and dragged every chapter's score with
    # it. Selection happens further down, after the scores are settled.
    assessed = []
    for idx in all_indexes:
        a = assess(idx, terms)
        if not a:
            continue
        a["fp"] = task_fingerprint(idx, terms, hits=a["terms"])
        assessed.append(a)

    if not assessed:
        print("[+] Оценивать нечего: нет глав, прошедших verify.py.")
        return

    rates = score_records(assessed)
    df = corpus_df(assessed)
    assessed.sort(key=lambda a: (-a["score"], jitter(args.seed, a["index"])))

    prov = load_task_provenance(QC_DIR)
    eligible = {a["index"] for a in assessed}

    # Staleness is checked for every chapter that has a task or a verdict on
    # disk, INCLUDING ones assess() just rejected. A chapter that stopped
    # passing verify.py is exactly when a task file turns into a phantom -- the
    # text it quotes no longer exists -- and dropping it before the fingerprint
    # comparison would keep the loudest case silent. Such a task cannot be
    # rebuilt (there is no valid chapter to build it from), so it is reported
    # separately instead of being queued.
    orphan_stale = []
    for idx in all_indexes:
        if idx in eligible or (wanted is not None and idx not in wanted):
            continue
        rec = prov.get(idx) or {}
        if not os.path.exists(task_path(idx)):
            continue
        fp = task_fingerprint(idx, terms)
        if rec.get("fp") and rec["fp"] != fp:
            orphan_stale.append((idx, changed_inputs(rec.get("fp"), fp)))

    pool, stale_verdicts, stale_tasks, unknown = [], [], [], []
    for a in assessed:
        idx = a["index"]
        if wanted is not None and idx not in wanted:
            continue
        rec = prov.get(idx) or {}
        rec_fp = rec.get("fp")
        has_verdict = os.path.exists(verdict_path(idx))
        has_task = os.path.exists(task_path(idx))

        if has_verdict and not (args.redo or args.rebuild):
            if rec_fp and rec_fp == a["fp"]:
                continue                       # verdict matches the current snapshot
            if rec_fp is None and has_task:
                unknown.append(idx)            # built before fingerprints existed
                continue
            stale_verdicts.append((idx, changed_inputs(rec_fp, a["fp"])))
            if not args.redo_stale:
                continue

        a["prev_picked_by"] = rec.get("picked_by")
        if has_task and rec_fp is not None and rec_fp != a["fp"]:
            stale_tasks.append(idx)            # the file on disk is lying
        pool.append(a)

    flagged = [a for a in pool if a["score"] > 0]

    blind = sorted(cn for cn, r in rates.items() if r < MIN_TERM_RELIABILITY)
    print("[+] Термины, по которым проверка канона не работает: %d из %d "
          "(находятся реже %d%% — эвристика их не видит либо запись сомнительна)"
          % (len(blind), len(rates), int(MIN_TERM_RELIABILITY * 100)))
    print("[+] Глав оценено: %d, в очереди: %d, из них с сигналами риска: %d"
          % (len(assessed), len(pool), len(flagged)))
    print("")
    print("%-8s %7s %7s %8s %8s %6s %s" %
          ("глава", "балл", "коэф", "термины", "абзацы", "спам", "сигналы"))
    for a in pool[:20]:
        sig = []
        if a["missed"]:
            sig.append("терминов нет: %d" % len(a["missed"]))
        if a["underused"]:
            sig.append("недоиспользован: %d" % len(a["underused"]))
        if a["outliers"]:
            sig.append("выбросов: %d" % len(a["outliers"]))
        if a["spam"]:
            sig.append("реклама")
        if a["cjk"]:
            sig.append("остатков оригинала: %d" % a["cjk"])
        if a["ratio"] < LOW_RATIO_EDGE:
            sig.append("короткий")
        print("%-8d %7.2f %7.2f %8d %8d %6s %s"
              % (a["index"], a["score"], a["ratio"], len(a["missed"]),
                 len(a["outliers"]), "да" if a["spam"] else "-", "; ".join(sig) or "-"))
    if len(pool) > 20:
        print("... ещё %d" % (len(pool) - 20))

    # Printed BEFORE the --report early exit on purpose: this is the part a
    # human actually needs, and --report is the command they run to look.
    if stale_tasks:
        print("")
        print("[!] Заданий устарело (входы изменились после сборки): %d — %s"
              % (len(stale_tasks), ", ".join(str(i) for i in sorted(stale_tasks))))
        print("    Они будут пересобраны в этом прогоне.")
    if orphan_stale:
        print("")
        print("[!] Задание на диске описывает главу, которая больше не проходит "
              "verify.py: %d" % len(orphan_stale))
        for idx, what in sorted(orphan_stale):
            print("    гл.%04d — изменилось: %s" % (idx, ", ".join(what)))
        print("    Пересобрать нельзя — собирать не из чего. Сначала чинить главу")
        print("    (python verify.py --detail), файл задания до тех пор не отдавать.")
    if stale_verdicts:
        print("")
        print("[!] Вердиктов вынесено по старому снимку: %d" % len(stale_verdicts))
        for idx, what in sorted(stale_verdicts)[:20]:
            print("    гл.%04d — изменилось: %s" % (idx, ", ".join(what)))
        print("    Эти вердикты недостоверны: номера абзацев в issues могли уехать.")
        print("    Переспросить приёмщика: python build_qc.py --redo-stale --count N")
    if unknown:
        print("")
        print("[i] Заданий без отпечатка (собраны до этой проверки): %d. "
              "Разово принять как актуальные: python build_qc.py --adopt" % len(unknown))

    if args.calibrate:
        calibrate(assessed, rates, load_verdict_map())
        return
    if args.report:
        return

    run = run_passport(args)

    if args.rebuild:
        # Re-render task files whose CONTENT template changed while their
        # verdicts stay valid -- e.g. when a leading question was removed from
        # flag_block. The pick reason is deliberately preserved: it describes
        # how the chapter reached a proof-reader, and rewriting it as "only"
        # would silently corrupt the control group it belongs to.
        prepared = []
        for a in pool:
            rec = prov.get(a["index"]) or {}
            keep = a.get("prev_picked_by") or "?"
            carry = {k: rec[k] for k in ("flagged", "score", "evidence")
                     if k in rec and os.path.exists(verdict_path(a["index"]))}
            prepared.append(write_task(
                a, keep, dict(run, source="build_qc:rebuild"), df, preserve=carry))
        save_json(os.path.join(QC_DIR, "index.json"), prepared)
        print("")
        print("[+] Переиздано заданий: %d — %s"
              % (len(prepared), ", ".join("%d(%s)" % (p["index"], p["picked_by"])
                                          for p in prepared)))
        return

    if args.only:
        chosen = [(a, "only") for a in pool]
    else:
        chosen = [(a, "risk") for a in pick_spread(flagged, args.count)]
        if args.sample:
            taken = {a["index"] for a, _ in chosen}
            rest = [a for a in pool if a["index"] not in taken]
            rng = random.Random(args.seed)
            chosen += [(a, "sample") for a in rng.sample(rest, min(args.sample, len(rest)))]

    # Stale tasks are rebuilt even when the ranking did not select them: the
    # file on disk describes a text that no longer exists, and an orchestrator
    # picking tasks off `ls qc_batches/*.md` will hand it to an agent. Rebuilding
    # costs two file reads; sending an agent at a phantom costs ~15k tokens.
    picked = {a["index"] for a, _ in chosen}
    by_idx = {a["index"]: a for a in pool}
    for idx in stale_tasks:
        if idx not in picked and idx in by_idx:
            a = by_idx[idx]
            chosen.append((a, a.get("prev_picked_by") or "risk"))
            picked.add(idx)

    if not chosen:
        print("")
        print("[+] Сигналов риска нет ни на одной главе — брать в приёмку нечего.")
        print("    Для контроля качества самих сигналов: --sample 5")
        return

    os.makedirs(VERDICT_DIR, exist_ok=True)
    prepared = [write_task(a, picked_by, run, df) for a, picked_by in chosen]
    save_json(os.path.join(QC_DIR, "index.json"), prepared)

    total = sum(p["task_chars"] for p in prepared)
    by_pick = collections.Counter(p["picked_by"] for p in prepared)
    print("")
    print("[+] Подготовлено заданий: %d (%s)"
          % (len(prepared),
             ", ".join("%s %d" % (k, by_pick[k]) for k in sorted(by_pick))))
    print("    главы: %s" % ", ".join(str(p["index"]) for p in prepared))
    print("    материала суммарно: %d символов (~%dk токенов на всю партию)"
          % (total, total // 2500))
    print("")
    print("Дальше: на каждое задание агент типа proofreader с промптом вида")
    print('    "Файл задания: qc_batches/qc_0042.md"')
    print("Затем: python qc_report.py")


def pick_spread(flagged, count):
    """Top `count` chapters, but at most ceil(count/4) from any one hundred.

    Without this a single arc can own an entire batch: the ranking has no idea
    that chapters 190, 191, 198 are the same scene with the same drifting term,
    and reading ten neighbours measures one mistake ten times.
    """
    if count <= 0:
        return []
    cap = math.ceil(count / 4)
    per, out, spill = collections.Counter(), [], []
    for a in flagged:
        if len(out) >= count:
            break
        bucket = a["index"] // 100
        if per[bucket] >= cap:
            spill.append(a)
            continue
        per[bucket] += 1
        out.append(a)
    # If the cap starved the batch (one arc is genuinely all there is), fill the
    # remainder in rank order rather than hand back a short batch.
    for a in spill:
        if len(out) >= count:
            break
        out.append(a)
    return out


if __name__ == "__main__":
    main()
