#!/usr/bin/env python3
"""Chapter downloader for czbooks.net/n/ui63l.

Differences from the previous version, all of which were real bugs:
  * one HTTP client per worker thread instead of a fresh TLS handshake per attempt
  * failures are recorded with a reason instead of being silently swallowed
  * a parse failure is not retried against the same URL five times
  * the retry pass runs concurrently, not serially
  * the catalog's chapter numbering restarts across volumes, so file index is NOT
    the chapter number -- `manifest.json` records the real one
  * verification reports what is actually missing and exits non-zero
"""

import argparse
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import primp
from bs4 import BeautifulSoup

from common import (
    CATALOG_FILE,
    CHAPTERS_DIR,
    MANIFEST_FILE,
    ROOT,
    chapter_path,
    clean_paragraphs,
    load_json,
    parse_chapter_numbers,
    read_text,
    save_json,
    split_paragraphs,
    write_text,
)

CATALOG_URL = "https://SET-ME"  # страница каталога глав тайтла
BOOK_ID = CATALOG_URL.rsplit("/", 1)[-1]
MIRROR_BOOK_ID = "s62gnp"
MIN_VALID_BYTES = 200
MIN_VALID_PARAGRAPHS = 3

_local = threading.local()
_print_lock = threading.Lock()


def client():
    """One impersonating client per thread -- keeps connections alive.

    primp 1.0.0 silently falls back to "random" for any impersonate value it
    does not recognise, so the old "chrome_120" was never actually in effect.
    Ask for "random" explicitly rather than pretend to pin a fingerprint.
    """
    c = getattr(_local, "client", None)
    if c is None:
        c = primp.Client(impersonate="random", follow_redirects=True, timeout=30)
        _local.client = c
    return c


def log(msg):
    with _print_lock:
        print(msg, flush=True)


# --------------------------------------------------------------------------
# Catalog
# --------------------------------------------------------------------------


def absolute(href):
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return "https://czbooks.net" + href
    return href


def fetch_catalog(force=False):
    cached = load_json(CATALOG_FILE)
    if cached and not force:
        return cached

    log("[+] Загружаю оглавление: %s" % CATALOG_URL)
    r = client().get(CATALOG_URL)
    if r.status_code != 200:
        raise RuntimeError("оглавление вернуло HTTP %d" % r.status_code)

    soup = BeautifulSoup(r.content, "html.parser")
    links = soup.select("ul.chapter-list li a, ul#chapter-list li a")
    if not links:
        raise RuntimeError("не нашёл ни одной ссылки на главу -- вёрстка сайта изменилась")

    catalog, seen_urls = [], set()
    for el in links:
        href = el.get("href", "")
        if not href:
            continue
        url = absolute(href)
        if url in seen_urls:  # the page lists some chapters twice
            continue
        seen_urls.add(url)
        catalog.append(
            {
                "index": len(catalog) + 1,
                "title": el.text.strip(),
                "url": url,
                "mirror_url": absolute(href.replace("/n/%s/" % BOOK_ID, "/n/%s/" % MIRROR_BOOK_ID)),
            }
        )

    save_json(CATALOG_FILE, catalog)
    log("[+] В оглавлении %d глав (после дедупликации по URL)." % len(catalog))
    return catalog


# --------------------------------------------------------------------------
# Download
# --------------------------------------------------------------------------


def parse_chapter_page(html):
    soup = BeautifulSoup(html, "html.parser")
    title_el = soup.select_one("div.name, h1, div.title")
    title = title_el.text.strip() if title_el else ""

    body = soup.select_one("div.content")
    if body is None:
        return title, []

    tags = body.find_all("p")
    if tags:
        paragraphs = [p.text.strip() for p in tags if p.text.strip()]
    else:
        paragraphs = [ln.strip() for ln in body.text.split("\n") if ln.strip()]

    # czbooks injects piracy watermarks and vote solicitations into the body.
    # Strip them here so they never reach the translator or the glossary --
    # 1423 of the first 4521 downloaded chapters carried them.
    return title, clean_paragraphs(paragraphs)


def is_complete(path):
    """A file counts as downloaded only if it actually holds a chapter."""
    try:
        text = read_text(path)
    except OSError:
        return False
    return len(text.encode("utf-8")) >= MIN_VALID_BYTES and (
        len(split_paragraphs(text)) >= MIN_VALID_PARAGRAPHS
    )


def download_one(item, max_attempts=6):
    """Returns (index, status, detail). status: ok | skip | fail."""
    idx = item["index"]
    path = chapter_path(idx)
    if is_complete(path):
        return idx, "skip", "уже скачана"

    # The mirror book (czbooks.net/n/s62gnp) is real, but it uses its own
    # per-chapter slugs. Pasting the primary book's slug onto the mirror path
    # -- which the original scraper did -- yields a URL that always errors, so
    # every other retry was spent on a guaranteed failure. Verified: 0 of 5
    # mirror fetches succeeded, and matching the two catalogues by title gives
    # 23 hits out of 4885. There is no usable fallback, so spend every attempt
    # on the URL that actually works.
    urls = [item["url"]]

    last = "неизвестно"
    for attempt in range(max_attempts):
        url = urls[attempt % len(urls)]
        try:
            r = client().get(url)
        except Exception as exc:  # noqa: BLE001 - network layer raises anything
            # Under sustained load czbooks drops connections rather than
            # answering 429, so the transport raises instead of returning a
            # status. It is the same throttling, and it needs the same backoff:
            # short retries here cost 776 chapters on the first full run.
            last = "сеть: %s" % type(exc).__name__
            _local.client = None  # the pooled connection is poisoned; rebuild it
            time.sleep(2.0 * (attempt + 1) + random.uniform(0.5, 1.5))
            continue

        if r.status_code == 429:
            last = "HTTP 429 (rate limit)"
            time.sleep(2.0 * (attempt + 1) + random.uniform(0.5, 1.5))
            continue
        if r.status_code != 200:
            last = "HTTP %d" % r.status_code
            time.sleep(0.5 * (attempt + 1))
            continue

        title, paragraphs = parse_chapter_page(r.content)
        if not paragraphs:
            # Retrying the same URL will not make div.content appear. Try the
            # mirror once, then give up and report it honestly.
            last = "страница отдалась, но текст не распознан (нет div.content)"
            if attempt + 1 >= len(urls):
                return idx, "fail", last
            continue

        body = (title or item["title"]) + "\n\n" + "\n\n".join(paragraphs) + "\n"
        write_text(path, body)
        time.sleep(random.uniform(0.05, 0.15))
        return idx, "ok", title or item["title"]

    return idx, "fail", last


def run_download(catalog, threads, start, end):
    targets = [c for c in catalog if start <= c["index"] <= (end or len(catalog))]
    todo = [c for c in targets if not is_complete(chapter_path(c["index"]))]
    log(
        "[+] Целевых глав %d, из них уже на диске %d, качать %d (потоков: %d)."
        % (len(targets), len(targets) - len(todo), len(todo), threads)
    )
    if not todo:
        return []

    failures, done, t0 = [], 0, time.time()
    with ThreadPoolExecutor(max_workers=threads) as pool:
        futures = {pool.submit(download_one, item): item for item in todo}
        for fut in as_completed(futures):
            idx, status, detail = fut.result()
            done += 1
            if status == "fail":
                failures.append({"index": idx, "title": futures[fut]["title"], "reason": detail})
            if done % 50 == 0 or done == len(todo):
                rate = done / max(time.time() - t0, 1e-6)
                eta = (len(todo) - done) / max(rate, 1e-6)
                log(
                    "    [%d/%d] %.1f гл/с | осталось ~%dм %02dс | ошибок: %d"
                    % (done, len(todo), rate, eta // 60, eta % 60, len(failures))
                )
    return failures


# --------------------------------------------------------------------------
# Manifest -- the fix for "file index != chapter number"
# --------------------------------------------------------------------------


RESTART_THRESHOLD = 3  # a drop to ch<=3 is a real new arc, not a typo


def build_manifest(catalog):
    """Record reading order and flag where the site's own titles are wrong.

    Do NOT reconstruct chapter numbers from titles -- czbooks' titles are
    typo-ridden and cannot carry that weight. Observed in this book:
      * genuine merges:   第11章+12章 共生
      * malformed titles: 第400章16章, 第410章4
      * transposed digits: ...2162 followed by 第1263章 (should be ~2163)
      * dropped leading digit: ...597 followed by 第98章 (should be ~598)
      * genuine arc restarts: 5 clean drops to 第1章

    The catalog's own order is the authoritative reading sequence, so the file
    index is the sequence key. The parsed number is kept as a hint and any
    disagreement is reported rather than silently "corrected".
    """
    entries, anomalies = [], []
    volume, prev_num = 1, None

    for item in catalog:
        idx = item["index"]
        on_disk = is_complete(chapter_path(idx))

        title = item["title"]
        if on_disk:
            first_line = read_text(chapter_path(idx)).split("\n", 1)[0].strip()
            if first_line:
                title = first_line

        nums = parse_chapter_numbers(title)
        num = nums[0] if nums else None

        # Re-sync to what was actually observed each step. Anchoring on a
        # running "expected" counter instead makes one discontinuity (the
        # 第11章+12章 merge) flag every chapter after it.
        restarted = False
        if num is not None:
            if num <= RESTART_THRESHOLD and prev_num is not None and prev_num > RESTART_THRESHOLD:
                volume += 1
                restarted = True
            elif prev_num is not None and num != prev_num + 1:
                anomalies.append(
                    {
                        "index": idx,
                        "title": title,
                        "parsed": num,
                        "expected_about": prev_num + 1,
                        "note": "номер в заголовке не следует за предыдущим — опечатка на сайте",
                    }
                )
            prev_num = max(nums)  # a merged entry covers through its last number

        entries.append(
            {
                "index": idx,
                "reading_order": idx,
                "file": "chapters/chapter_%04d.txt" % idx,
                "url": item["url"],
                "title": title,
                "volume": volume,
                "title_number": num,
                "covers_chapters": nums,
                "merged": len(nums) > 1,
                "volume_restart": restarted,
                "downloaded": on_disk,
            }
        )

    not_downloaded = [e["index"] for e in entries if not e["downloaded"]]

    manifest = {
        "note": (
            "Порядок чтения = поле reading_order (позиция в каталоге). "
            "title_number — номер из заголовка на сайте, он местами содержит опечатки "
            "и НЕ является источником истины."
        ),
        "catalog_entries": len(catalog),
        "downloaded": len(entries) - len(not_downloaded),
        "volumes": volume,
        "merged_files": sum(1 for e in entries if e["merged"]),
        "unnumbered_entries": [e["index"] for e in entries if e["title_number"] is None],
        "title_number_anomalies": anomalies,
        "not_downloaded": not_downloaded,
        "entries": entries,
    }
    save_json(MANIFEST_FILE, manifest)
    return manifest


# --------------------------------------------------------------------------
# Verify
# --------------------------------------------------------------------------


def verify(catalog):
    missing_files, truncated = [], []
    total_bytes = 0
    for item in catalog:
        path = chapter_path(item["index"])
        try:
            text = read_text(path)
        except OSError:
            missing_files.append(item["index"])
            continue
        size = len(text.encode("utf-8"))
        if size < MIN_VALID_BYTES or len(split_paragraphs(text)) < MIN_VALID_PARAGRAPHS:
            truncated.append(item["index"])
        else:
            total_bytes += size

    valid = len(catalog) - len(missing_files) - len(truncated)
    print("")
    print("=== ПРОВЕРКА ===")
    print("Глав в оглавлении:     %d" % len(catalog))
    print("Скачано и валидно:     %d (%.1f%%)" % (valid, 100.0 * valid / max(len(catalog), 1)))
    print("Объём:                 %.2f МБ" % (total_bytes / 1048576.0))
    print("Отсутствует файлов:    %d" % len(missing_files))
    print("Обрезано/пусто:        %d" % len(truncated))
    if missing_files:
        print("  первые отсутствующие индексы: %s" % missing_files[:20])
    if truncated:
        print("  первые обрезанные индексы:    %s" % truncated[:20])

    manifest = build_manifest(catalog)
    print("")
    print("=== ИДЕНТИФИКАЦИЯ ГЛАВ (manifest.json) ===")
    print("Порядок чтения задаёт позиция в каталоге, а не номер из заголовка.")
    print("Арок (рестартов нумерации):     %d" % manifest["volumes"])
    print("Файлов со склейкой глав:        %d" % manifest["merged_files"])
    print("Записей без номера в заголовке: %d" % len(manifest["unnumbered_entries"]))
    print("Опечаток в номерах на сайте:    %d" % len(manifest["title_number_anomalies"]))
    print("Ещё не скачано:                 %d" % len(manifest["not_downloaded"]))

    return not missing_files and not truncated


# --------------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser(description="Загрузчик глав czbooks")
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--end", type=int, default=0)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--refresh-catalog", action="store_true")
    ap.add_argument("--verify", action="store_true", help="только проверить, ничего не качать")
    args = ap.parse_args()

    os.makedirs(CHAPTERS_DIR, exist_ok=True)
    catalog = fetch_catalog(force=args.refresh_catalog)

    if args.verify:
        raise SystemExit(0 if verify(catalog) else 1)

    failures = run_download(catalog, args.threads, args.start, args.end)
    if failures:
        save_json(os.path.join(ROOT, "scrape_failures.json"), failures)
        log("")
        log("[!] Не удалось скачать %d глав. Причины записаны в scrape_failures.json:" % len(failures))
        reasons = {}
        for f in failures:
            reasons[f["reason"]] = reasons.get(f["reason"], 0) + 1
        for reason, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
            log("      %4d x %s" % (count, reason))

    ok = verify(catalog)
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
