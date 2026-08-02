#!/usr/bin/env python3
"""Extract proper-noun candidates for the glossary from the raw Chinese corpus.

Why not frequency: ranking n-grams by how often they occur surfaces ordinary
vocabulary -- 知道 "know", 東西 "thing", 地方 "place" -- because common words are
by definition the most common. That is exactly how the previous glossary ended
up 89% grammar fragments.

The signal that actually separates a coined proper noun from ordinary vocabulary
is DISPERSION. A creature Lin names (岩蟲 "Rock Worm") appears densely inside the
few chapters that arc covers and is absent everywhere else. An ordinary word is
spread evenly across the whole book. So we keep n-grams that are:

    rare across chapters  (docfreq / total_chapters < MAX_SPREAD)
    dense inside theirs   (freq / docfreq >= MIN_BURST)

Measured on 3411 chapters: 44468 raw candidates -> 32807 after fragment removal
-> 1319 after dispersion. Top hits are 塵鱷, 織夢者, 銀甲龍, 晶蜴, 利維坦, 塔克,
比露, 始境 -- coined names, which is the point.

    python glossary_extract.py
    python glossary_extract.py --batch-size 150   # also write per-agent batches
"""

import argparse
import collections
import json
import os
import re
import sys

from common import CHAPTERS_DIR, ROOT, load_glossary, read_text, save_json

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

CANDIDATES_FILE = os.path.join(ROOT, "glossary_candidates.json")
BATCH_DIR = os.path.join(ROOT, "glossary_batches")

CJK_RUN = re.compile(r"[㐀-䶿一-鿿]+")

# Five, not four. The book coins terms that long (織夢者夢食), and with a
# 4-character ceiling their fragments -- 織夢者夢, 夢者夢食 -- have no longer
# form to be compared against, so drop_fragments() cannot recognise them and
# they reach the judges as candidates. Same for 析者 (tail of 分析者) and
# 之域 (tail of 秩序之域).
NGRAM_LENGTHS = (2, 3, 4, 5)

# The book announces its own coinages: "它們被稱為'塵鱷'", "琳把它稱為……'霧綿'",
# "就叫做'藏憶者'好了". Catching those verbs gives far higher precision than any
# statistic, and it catches names that appear too few times to clear the
# dispersion thresholds.
NAMING_RE = re.compile(
    r"(?:叫做|叫作|稱為|稱作|稱呼為|被稱為|被稱作|命名為|名字為|名為|簡稱)"
    r"\s*[‘'\"「『]?([㐀-䶿一-鿿]{2,6})[’'\"」』]?"
)

# Tuned against the measurements in the docstring.
MIN_FREQ = 40
MIN_DOCFREQ = 8
FRAGMENT_RATIO = 0.75  # g is a fragment if an extension keeps >=75% of its count
MAX_SPREAD = 0.25      # appears in <25% of chapters
MIN_BURST = 2.5        # >=2.5 mentions per chapter it appears in
CONTEXT_RADIUS = 34
MAX_CONTEXTS = 2


def chapter_files():
    return sorted(
        f for f in os.listdir(CHAPTERS_DIR) if f.startswith("chapter_") and f.endswith(".txt")
    )


def iter_ngrams(text):
    for run in CJK_RUN.findall(text):
        for length in NGRAM_LENGTHS:
            for i in range(len(run) - length + 1):
                yield run[i : i + length]


def count_pass(files):
    """Count n-gram frequency and document frequency.

    Prunes singletons periodically: the corpus yields millions of unique
    n-grams and holding them all costs gigabytes.

    The floor stays at 2 and does NOT scale with progress. A floor that grows
    (say n//200, reaching 22 near the end) would discard terms introduced late
    in the book -- the later arcs' creature names, which are precisely what we
    are hunting. Dropping singletons is safe because a term pruned at count 1
    can at worst finish one short of its true total.
    """
    prune_floor = 2
    freq = collections.Counter()
    docfreq = collections.Counter()
    named = collections.Counter()

    for n, name in enumerate(files, 1):
        text = read_text(os.path.join(CHAPTERS_DIR, name))
        seen = set()
        for gram in iter_ngrams(text):
            freq[gram] += 1
            seen.add(gram)
        for gram in seen:
            docfreq[gram] += 1
        for hit in NAMING_RE.findall(text):
            named[hit] += 1

        if n % 500 == 0:
            freq = collections.Counter({g: c for g, c in freq.items() if c >= prune_floor})
            docfreq = collections.Counter({g: c for g, c in docfreq.items() if g in freq})
            print("    прочитано %d глав, в памяти %d n-грамм" % (n, len(freq)), flush=True)

    return freq, docfreq, named


def drop_fragments(counts):
    """Remove n-grams that are merely a piece of a longer, equally common one.

    Checks only the direct one-character extensions (c+g and g+c) by dictionary
    lookup. Comparing every candidate against every other is O(n^2) and does not
    finish on 44k candidates -- that is not a hypothetical, it timed out here.
    """
    chars = set()
    for gram in counts:
        chars.update(gram)

    kept = {}
    for gram, count in counts.items():
        if len(gram) >= max(NGRAM_LENGTHS):
            kept[gram] = count
            continue
        threshold = FRAGMENT_RATIO * count
        is_fragment = False
        for ch in chars:
            if counts.get(ch + gram, 0) >= threshold or counts.get(gram + ch, 0) >= threshold:
                is_fragment = True
                break
        if not is_fragment:
            kept[gram] = count
    return kept


def collect_contexts(files, wanted):
    """Second pass: grab a couple of short snippets and the first chapter seen."""
    contexts = {g: [] for g in wanted}
    first_seen = {}

    for name in files:
        idx = int(name[8:-4])
        text = read_text(os.path.join(CHAPTERS_DIR, name))
        for gram in wanted:
            if len(contexts[gram]) >= MAX_CONTEXTS and gram in first_seen:
                continue
            pos = text.find(gram)
            if pos < 0:
                continue
            first_seen.setdefault(gram, idx)
            if len(contexts[gram]) < MAX_CONTEXTS:
                start = max(0, pos - CONTEXT_RADIUS)
                end = min(len(text), pos + len(gram) + CONTEXT_RADIUS)
                snippet = text[start:end].replace("\n", " ").strip()
                if snippet not in contexts[gram]:
                    contexts[gram].append(snippet)

        if all(len(v) >= MAX_CONTEXTS for v in contexts.values()):
            break

    return contexts, first_seen


def main():
    ap = argparse.ArgumentParser(description="Кандидаты в глоссарий по разбросу")
    ap.add_argument("--min-freq", type=int, default=MIN_FREQ)
    ap.add_argument("--min-docfreq", type=int, default=MIN_DOCFREQ)
    ap.add_argument("--max-spread", type=float, default=MAX_SPREAD)
    ap.add_argument("--min-burst", type=float, default=MIN_BURST)
    ap.add_argument("--batch-size", type=int, default=150, help="кандидатов на агента")
    args = ap.parse_args()

    files = chapter_files()
    if not files:
        print("В chapters/ пусто.")
        raise SystemExit(1)
    total = len(files)
    print("[1/4] Считаю n-граммы по %d главам..." % total, flush=True)
    freq, docfreq, named = count_pass(files)
    print("      уникальных n-грамм после прунинга: %d" % len(freq))
    print("      терминов из конструкций именования: %d" % len(named))

    raw = {
        g: c
        for g, c in freq.items()
        if c >= args.min_freq and docfreq[g] >= args.min_docfreq
    }
    print("[2/4] После порогов freq>=%d docfreq>=%d: %d" % (args.min_freq, args.min_docfreq, len(raw)))

    kept = drop_fragments(raw)
    print("      после отсева фрагментов: %d" % len(kept))

    # Terms already curated by hand need no agent review.
    curated, _auto, _rules = load_glossary()
    known = {t["cn"] for t in curated}

    scored = []
    for gram, count in kept.items():
        if gram in known:
            continue
        df = docfreq[gram]
        spread = df / total
        burst = count / df
        explicit = named.get(gram, 0)
        if spread < args.max_spread and burst >= args.min_burst:
            score = burst * (1 - spread) + 5.0 * min(explicit, 5)
            scored.append((round(score, 3), gram, count, df, round(burst, 2), explicit))

    # Names the text announces outright are high precision even when they are
    # too rare to clear the dispersion thresholds -- a creature named once and
    # mentioned a dozen times still belongs in the glossary.
    #
    # The dispersion ceiling still applies here. Without it "叫做什麽?"
    # ("what is it called?") registers as a naming event and promotes 什麽
    # -- the commonest interrogative in the language, present in 93% of
    # chapters -- to a proper noun.
    in_scored = {g for _s, g, _c, _d, _b, _e in scored}
    added = 0
    for gram, explicit in named.items():
        if gram in known or gram in in_scored:
            continue
        count = freq.get(gram, 0)
        df = docfreq.get(gram, 0)
        if df and df / total >= args.max_spread:
            continue
        if explicit >= 2 and count >= 10 and df >= 2:
            burst = count / df
            scored.append((round(5.0 * min(explicit, 5), 3), gram, count, df, round(burst, 2), explicit))
            added += 1

    scored.sort(reverse=True)
    print("[3/4] После фильтра разброса: %d кандидатов (+%d только по именованию)"
          % (len(scored), added))

    wanted = {g for _s, g, _c, _d, _b, _e in scored}
    print("[4/4] Собираю контексты...", flush=True)
    contexts, first_seen = collect_contexts(files, wanted)

    candidates = [
        {
            "cn": gram,
            "score": score,
            "freq": count,
            "docfreq": df,
            "per_chapter": burst,
            "explicitly_named": explicit,
            "first_chapter": first_seen.get(gram),
            "contexts": contexts.get(gram, []),
        }
        for score, gram, count, df, burst, explicit in scored
    ]

    save_json(
        CANDIDATES_FILE,
        {
            "note": (
                "Кандидаты отобраны по разбросу (плотно в своих главах, редко в остальных), "
                "а не по частоте. Требуют оценки агентом: имя собственное или обычная лексика."
            ),
            "chapters_scanned": total,
            "already_curated": sorted(known),
            "thresholds": {
                "min_freq": args.min_freq,
                "min_docfreq": args.min_docfreq,
                "max_spread": args.max_spread,
                "min_burst": args.min_burst,
            },
            "count": len(candidates),
            "candidates": candidates,
        },
    )
    print("")
    print("Записано в glossary_candidates.json: %d кандидатов." % len(candidates))

    if args.batch_size > 0:
        os.makedirs(BATCH_DIR, exist_ok=True)
        for old in os.listdir(BATCH_DIR):
            os.remove(os.path.join(BATCH_DIR, old))
        batches = [
            candidates[i : i + args.batch_size]
            for i in range(0, len(candidates), args.batch_size)
        ]
        for i, batch in enumerate(batches, 1):
            save_json(os.path.join(BATCH_DIR, "batch_%02d.json" % i), batch)
        print("Разбито на %d порций по %d в glossary_batches/." % (len(batches), args.batch_size))

    print("")
    explicit_total = sum(1 for c in candidates if c["explicitly_named"])
    print("Из них названы в тексте явно ('叫做X', '被稱為X'): %d" % explicit_total)
    print("")
    print("Топ-25 кандидатов:")
    print("  %-8s %7s %6s %9s %6s  %s" % ("термин", "всего", "глав", "на главу", "имен.", "контекст"))
    for c in candidates[:25]:
        ctx = c["contexts"][0][:38] if c["contexts"] else ""
        print("  %-8s %7d %6d %9.1f %6d  %s"
              % (c["cn"], c["freq"], c["docfreq"], c["per_chapter"], c["explicitly_named"], ctx))


if __name__ == "__main__":
    main()
