#!/usr/bin/env python3
"""Sequential, unattended translation driver.

One chapter at a time: an agent finishes, its terms go into the glossary, and
only then does the next chapter start. That ordering is the point. Running 30
translators at once meant none of them could see what the others had named, so
the same coinage came back as five different Russian words -- 白膏 got five,
軍團球 got two from neighbouring chapters. Sequential execution removes the race
by construction: there is never more than one namer, so `conflicts` cannot even
arise, and chapter N+1 is dispatched with everything chapter N discovered.

The cost is wall-clock, which is why this runs unattended and survives being
throttled. A session limit is not an error here, it is an expected pause: the
driver parses the reset time out of the message, sleeps past it, and picks up
exactly where it stopped. Nothing is held in memory that disk does not already
have, so a kill at any moment loses at most one chapter.

    python autorun.py --count 70
    python autorun.py --count 70 --prewave     # judge the range's coinages first
    python autorun.py --status

Параллельный режим: --range LO HI --slot NN. Несколько экземпляров работают
по непересекающимся диапазонам глав; у каждого свои state/log/usage файлы,
а записи в общие файлы глоссария сериализуются файловым замком. Гонка
именований, ради которой прогон сделан последовательным, остаётся возможной
на границах диапазонов, но она смягчена: глоссарий уже зрелый (2000+ глав),
prewave судит коинажи каждого окна до перевода, а спорный термин при слиянии
не выбирается автоматически, а остаётся вне глоссария (pending_conflicts.log).

Progress and failures: autorun.log, autorun_state.json.
"""

import argparse
import datetime as dt
import json
import os
import random
import re
import shutil
import socket
import subprocess
import sys
import time

import finalize
import llm
from common import (
    RU_DIR,
    ROOT,
    chapter_path,
    load_excluded,
    read_text,
    ru_path,
    save_json,
    split_paragraphs,
    validate_translation,
    write_text,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

STATE_FILE = os.path.join(ROOT, "autorun_state.json")
LOG_FILE = os.path.join(ROOT, "autorun.log")
REPORTS_FILE = os.path.join(ROOT, "new_terms_raw.txt")
COLLISION_LOG = os.path.join(ROOT, "pending_conflicts.log")
USAGE_FILE = os.path.join(ROOT, "autorun_usage.jsonl")
GLOSSARY_LOCK = os.path.join(ROOT, ".glossary.lock")
LOCK_STALE = 900               # держатель замка живёт секунды; 15 минут — это труп
REJECT_DIR = os.path.join(ROOT, "ru_rejected")
MANIFEST = os.path.join(ROOT, "manifest.json")
FIXES_DIR = os.path.join(ROOT, "fixes")

CHAPTER_TIMEOUT = 900          # a chapter takes 1-4 min; 15 is a hang, not slowness

# When a chapter fails acceptance three times, the loop below would otherwise
# retry it forever at ~27k tokens a go -- chapter 629 burned an hour of the run
# that way. Three identical rejections mean three independent translations
# agreed, so the fault is in the data or the rules, and no fourth translation
# will find it. Escalate to an engineer instead of spending another attempt.
FIXER_AFTER = 3                # rejections before the engineer is called in
FIXER_TIMEOUT = 1800           # it reads the corpus and reruns cleaners
MAX_FIXER_RUNS = 2             # two failed diagnoses mean the run needs a human
STUCK_SLEEP = 1800             # backoff once the engineer is out of ideas
MAX_SLEEP = 6 * 3600           # never trust a parsed reset time beyond this

# Throttling and transport failures are told apart because they deserve very
# different waits. A session limit is measured in hours; a dropped socket is
# measured in seconds, and sleeping an hour over one would waste the night.
RATE_PATTERNS = (
    "usage limit", "session limit", "rate limit", "rate_limit", "ratelimit",
    "429", "too many requests", "quota", "insufficient_quota",
    "overloaded", "capacity", "server_overloaded",
)
NETWORK_PATTERNS = (
    "econnreset", "etimedout", "enotfound", "eai_again", "econnrefused",
    "socket hang up", "fetch failed", "network error", "timed out",
    "502", "503", "504", "bad gateway", "service unavailable",
    "connection error", "tls", "handshake",
    # Spelled without the errno prefix by the CLI. Matching "econnrefused"
    # against "ConnectionRefused" fails, and three such misses in a row set a
    # perfectly good chapter aside -- which is why the zero-spend rule below
    # exists and this list is only a fast path.
    "connectionrefused", "connection refused",
    "failedtoopensocket", "failed to open socket", "socket",
)

API_HOST = "api.anthropic.com"

RESET_CLOCK_RE = re.compile(r"resets?\s+(\d{1,2}):(\d{2})\s*(am|pm)?", re.I)
RESET_EPOCH_RE = re.compile(r"limit reached\|(\d{9,13})")


class GlossaryLock:
    """Межпроцессный замок на общие файлы глоссария.

    Параллельные прогоны (--range/--slot) обновляют new_terms_raw.txt,
    glossary_accumulated.json и glossary_v2.json циклом прочитал-изменил-
    записал. Атомарной записи write_text() хватает, чтобы читатель не увидел
    полфайла, но не хватает, чтобы два писателя не затёрли работу друг друга.
    Замок сериализует писателей; читатели ходят без него.
    """

    def __enter__(self):
        while True:
            try:
                self.fd = os.open(GLOSSARY_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.fd, str(os.getpid()).encode())
                return self
            except FileExistsError:
                # Держатель работает секунды. Пятнадцатиминутный замок оставил
                # убитый процесс — снять его безопаснее, чем встать навсегда.
                try:
                    if time.time() - os.path.getmtime(GLOSSARY_LOCK) > LOCK_STALE:
                        os.remove(GLOSSARY_LOCK)
                        continue
                except OSError:
                    pass
                # Джиттер обязателен. Ровный опрос раз в секунду фазово
                # синхронизируется с чужими захватами, и невезучий процесс
                # проигрывает гонку бесконечно: слот 15 простоял так 12 минут,
                # пока 15 соседей передавали замок друг другу мимо него.
                time.sleep(0.3 + random.random())

    def __exit__(self, *exc):
        try:
            os.close(self.fd)
        except OSError:
            pass
        try:
            os.remove(GLOSSARY_LOCK)
        except OSError:
            pass
        return False


def short(text, limit=170):
    """One-line form for the log, truncated visibly rather than silently.

    The log is the only window into an unattended run, so a report cut mid
    term -- 大顎= with nothing after it -- reads as data loss when the full
    line did reach the glossary intact.
    """
    one = " ".join((text or "").split())
    return one if len(one) <= limit else one[:limit] + " …обрезано"


def log(msg):
    line = "%s  %s" % (dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            return json.loads(read_text(STATE_FILE))
        except ValueError:
            log("[!] autorun_state.json не разобрался, начинаю с чистого состояния")
    return {"attempts": {}, "done": [], "set_aside": [], "started": None}


def save_state(state):
    write_text(STATE_FILE, json.dumps(state, ensure_ascii=False, indent=2))


def find_claude():
    for name in ("claude", "claude.cmd", "claude.exe"):
        path = shutil.which(name)
        if path:
            return path
    raise SystemExit("Не найден исполняемый файл claude в PATH.")


CLAUDE = None


def parse_result(stdout, tag):
    """Take the report line out of the JSON envelope, recording what it cost.

    Falls back to raw text if the payload is not JSON, so a CLI that changes
    its output format degrades to the old behaviour instead of killing the run.
    """
    data = envelope(stdout)
    if data is None:
        return (stdout or "").strip()

    u = data.get("usage") or {}
    rec = {
        "tag": tag,
        "at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "turns": data.get("num_turns"),
        "in": u.get("input_tokens", 0),
        "cache_write": u.get("cache_creation_input_tokens", 0),
        "cache_read": u.get("cache_read_input_tokens", 0),
        "out": u.get("output_tokens", 0),
        "usd": data.get("total_cost_usd"),
    }
    with open(USAGE_FILE, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return str(data.get("result") or "").strip()


def usage_summary():
    """Measured cost so far -- the basis for any projection onto the remainder."""
    if not os.path.exists(USAGE_FILE):
        return None
    rows = []
    for line in read_text(USAGE_FILE).splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    chapters = [r for r in rows if (r.get("tag") or "").isdigit()]
    if not chapters:
        return None
    tokens = sum(r["in"] + r["cache_write"] + r["cache_read"] + r["out"] for r in chapters)
    usd = sum(r["usd"] or 0 for r in chapters)
    return {
        "chapters": len(chapters),
        "tokens_per_chapter": tokens / len(chapters),
        "usd_per_chapter": usd / len(chapters),
    }


def envelope(stdout):
    """The --output-format json payload, or None if this is not JSON."""
    raw = (stdout or "").strip()
    if not raw.startswith("{"):
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def error_text(stdout, fallback):
    """The human-readable half of a failure, not the raw JSON tail.

    Logging out[-600:] spilled the middle of the envelope into the log --
    a truncated error glued to session_id and a usage block -- which reads as
    corruption rather than as "the socket would not open".
    """
    data = envelope(stdout)
    if data is not None:
        msg = str(data.get("result") or data.get("error") or data.get("subtype") or "")
        if msg:
            return msg
    return (fallback or "")[-300:]


def internet_up(host=None, port=None, timeout=3):
    """Can we open a socket to the API at all."""
    host = host or llm.api_host()
    port = port or llm.api_port()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_for_internet():
    """Poll once a second until the API is reachable again.

    A dropped link is not a reason to give up on a chapter, and it is not a
    reason to sleep a fixed ten minutes either -- the connection usually comes
    back in seconds. Polling costs nothing and resumes the moment it does.
    """
    waited = 0
    while not internet_up():
        if waited % 30 == 0:
            log("    сети нет, жду восстановления… (%d с)" % waited)
        time.sleep(1)
        waited += 1
    if waited:
        log("    сеть вернулась через %d с" % waited)
    return waited


def spent_nothing(data):
    """True when the run burned no tokens at all.

    A request that cost zero never reached the model, so whatever the message
    says, the failure is transport. This is the load-bearing test: the wording
    list will always be incomplete -- it already missed ConnectionRefused and
    FailedToOpenSocket -- but "no tokens were charged" cannot be missed.
    """
    u = data.get("usage") or {}
    spent = sum(u.get(k, 0) or 0 for k in (
        "input_tokens", "output_tokens",
        "cache_creation_input_tokens", "cache_read_input_tokens",
    ))
    return spent == 0 and not (data.get("total_cost_usd") or 0)


def classify(stdout, stderr, rc):
    """ok / ratelimit / network / fail.

    The JSON envelope is authoritative whenever it parses, and its numbers must
    never be pattern-matched. Scanning the whole payload for "429" was wrong in
    a way that cost real work: the envelope carries duration_ms and token
    counts, and a duration of 74293 ms contains "429". A finished chapter was
    read as throttled, thrown away, and re-translated ten minutes later. Only
    human-readable fields are searched now.
    """
    data = envelope(stdout)
    if data is not None and rc == 0 and not data.get("is_error"):
        return "ok"
    if data is not None and data.get("fatal"):
        # Встроенный агент (llm_agent.py) сам знает, что повтор не поможет:
        # неверный ключ или имя модели. Ждать сеть тут — ждать вечно.
        return "fail"

    parts = [stderr or ""]
    if data is not None:
        parts += [str(data.get(k, "")) for k in ("result", "subtype", "error")]
    else:
        parts.append(stdout or "")
    blob = " ".join(parts).lower()

    if any(p in blob for p in RATE_PATTERNS):
        return "ratelimit"
    if any(p in blob for p in NETWORK_PATTERNS):
        return "network"
    if data is not None and spent_nothing(data):
        return "network"
    return "ok" if rc == 0 else "fail"


def seconds_until_reset(text):
    """How long to wait when throttled, taken from the message when it says.

    Claude Code reports either an epoch ("limit reached|1730000000") or a wall
    clock ("resets 5:20am"). The clock form has no date, so it means the next
    time that clock comes round.
    """
    m = RESET_EPOCH_RE.search(text or "")
    if m:
        stamp = int(m.group(1))
        if stamp > 1e11:            # milliseconds
            stamp //= 1000
        delay = stamp - time.time()
        if 0 < delay < MAX_SLEEP:
            return delay + 120

    m = RESET_CLOCK_RE.search(text or "")
    if m:
        hour, minute, ampm = int(m.group(1)), int(m.group(2)), (m.group(3) or "").lower()
        if ampm == "pm" and hour != 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        now = dt.datetime.now()
        target = now.replace(hour=hour % 24, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += dt.timedelta(days=1)
        delay = (target - now).total_seconds()
        if 0 < delay < MAX_SLEEP:
            return delay + 120
    return None


def sleep_noisily(seconds, why):
    """Sleep in visible chunks so a tailed log never looks hung."""
    seconds = int(max(seconds, 1))
    finish = dt.datetime.now() + dt.timedelta(seconds=seconds)
    log("    пауза %d мин (%s), продолжу в %s"
        % (round(seconds / 60), why, finish.strftime("%H:%M:%S")))
    step = 300
    left = seconds
    while left > 0:
        time.sleep(min(step, left))
        left -= step
        if left > 0:
            log("    ... ещё %d мин" % round(left / 60))


def run_claude(prompt, agent, timeout=CHAPTER_TIMEOUT, tag="", model="sonnet"):
    """Run one headless agent, waiting out throttling for as long as it takes.

    Rate limits retry forever by design -- the whole point of the driver is that
    hitting a limit at 3am costs the run nothing. Only genuine task failures are
    counted and given up on.
    """
    # Кто отвечает — подписка Claude или API-ключ другого провайдера —
    # решает активный профиль llm.py; форма вывода у всех одна.
    provider = llm.provider()
    cmd = llm.agent_cmd(prompt, agent, model, provider)
    env = llm.agent_env(provider)
    rate_waits, net_waits, hard_fails, timeouts = 0, 0, 0, 0
    while True:
        try:
            proc = subprocess.run(
                cmd, cwd=ROOT, env=env, capture_output=True, timeout=timeout,
                encoding="utf-8", errors="replace",
            )
            out = (proc.stdout or "") + "\n" + (proc.stderr or "")
            kind = classify(proc.stdout, proc.stderr, proc.returncode)
        except subprocess.TimeoutExpired:
            # Таймаут — зависание, а не обрыв связи: бесконечно повторять его
            # нельзя, иначе одна вечно виснущая глава держит слот навсегда.
            timeouts += 1
            if timeouts >= 3:
                return False, "таймаут %d раза подряд" % timeouts
            out, kind = "", "network"
        except OSError as exc:
            out, kind = str(exc), "network"

        if kind == "ok":
            return True, parse_result(proc.stdout, tag)

        if kind == "ratelimit":
            rate_waits += 1
            delay = seconds_until_reset(out)
            if delay is None:
                # Without a stated reset time, probe rather than commit. Doubling
                # up to MAX_SLEEP would idle five hours over a limit that cleared
                # in one; a probe costs a few hundred tokens, so cap the step at
                # half an hour and let it retry.
                delay = min(600 * (2 ** (rate_waits - 1)), 1800)
            sleep_noisily(delay, "лимит исчерпан")
            continue

        if kind == "network":
            # Unbounded on purpose. A chapter must never be abandoned because
            # the link went down; the driver simply waits for it to come back.
            net_waits += 1
            wait_for_internet()
            time.sleep(min(5 * net_waits, 60))   # let the endpoint settle
            continue

        hard_fails += 1
        if hard_fails >= 3:
            return False, error_text(proc.stdout, out)
        wait_for_internet()
        sleep_noisily(30, "сбой агента, пробую ещё раз")


def run_py(script, *args, allow_fail=False):
    proc = subprocess.run(
        [sys.executable, script] + list(args), cwd=ROOT,
        capture_output=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0 and not allow_fail:
        log("[!] %s вернул %d: %s" % (script, proc.returncode, (proc.stderr or "")[-300:]))
    return proc.returncode, (proc.stdout or "")


def pending_chapters():
    """Untranslated chapters in reading order, minus the ones set aside."""
    order = []
    if os.path.exists(MANIFEST):
        try:
            data = json.loads(read_text(MANIFEST))
            order = [e["index"] for e in data.get("entries", []) if e.get("downloaded")]
        except (ValueError, KeyError):
            order = []
    if not order:
        order = sorted(
            int(f[8:12]) for f in os.listdir(os.path.join(ROOT, "chapters"))
            if f.startswith("chapter_") and f.endswith(".txt")
        )
    excluded = load_excluded()
    return [i for i in order if i not in excluded and not os.path.exists(ru_path(i))]


def accept_chapter(idx):
    """Strip markers and validate. Returns (ok, problem)."""
    status, detail, text = finalize.finalize_one(idx)
    if status == "fail":
        return False, detail
    if status == "ok":
        write_text(ru_path(idx), text + "\n")

    # Агент отчитался об успехе, но файла нет. Это рядовой отказ, а не аварийная
    # ситуация: цикл выше умеет повторять главу и умеет звать разбирающего.
    # Пока проверка читала файл вслепую, такой случай ронял ВЕСЬ прогон
    # FileNotFoundError'ом — на главе 1411 прогон встал ровно так, потеряв
    # оставшиеся 3464 главы очереди из-за одного неудачного вызова агента.
    if not os.path.exists(ru_path(idx)):
        return False, "агент не создал файл перевода"

    problems = validate_translation(read_text(chapter_path(idx)), read_text(ru_path(idx)))
    if problems:
        return False, "; ".join(problems[:3])
    return True, None


def build_fix_dossier(idx, reasons, prior_verdict=None):
    """Write the evidence file the engineer works from.

    Pointers rather than transcripts: the agent reads the files itself, and a
    dossier that inlined both texts would cost more than the diagnosis. What is
    inlined is the one thing it cannot reconstruct -- the rejection reason from
    each attempt, since the rejected outputs overwrite each other in ru_rejected.
    """
    os.makedirs(FIXES_DIR, exist_ok=True)
    src = chapter_path(idx)
    n_src = len(split_paragraphs(read_text(src))) if os.path.exists(src) else 0
    bad = os.path.join(REJECT_DIR, "chapter_%04d.txt" % idx)
    n_bad = len(split_paragraphs(read_text(bad))) if os.path.exists(bad) else 0

    lines = [
        "# Застряла глава %04d" % idx,
        "",
        "Глава не прошла приёмку %d раз подряд. Причины по попыткам:" % len(reasons),
        "",
    ]
    for n, why in enumerate(reasons, 1):
        lines.append("%d. %s" % (n, why))
    if n_bad:
        bad_line = ("- забракованный перевод: `ru_rejected/chapter_%04d.txt`"
                    " — абзацев %d" % (idx, n_bad))
    else:
        bad_line = "- забракованный перевод: не сохранился"
    lines += [
        "",
        "## Файлы",
        "",
        "- исходник: `chapters/chapter_%04d.txt` — абзацев %d" % (idx, n_src),
        bad_line,
        "- задание переводчику: `batches/task_%04d.md`" % idx,
        "- приёмка: `common.validate_translation`",
        "",
    ]
    if len(set(reasons)) == 1 and len(reasons) > 1:
        lines += [
            "Причина отказа **одна и та же во всех попытках**. Разные прогоны",
            "модели сошлись на одном решении — это довод в пользу того, что",
            "правы они, а не приёмка.",
            "",
        ]
    if prior_verdict:
        lines += [
            "## Прошлый твой вердикт по этой главе не помог",
            "",
            "```json",
            json.dumps(prior_verdict, ensure_ascii=False, indent=2),
            "```",
            "",
            "Он не снял затык. Не повторяй ту же правку — ищи причину глубже.",
            "",
        ]
    lines += ["Вердикт пиши в `fixes/stuck_%04d.json`." % idx, ""]
    path = os.path.join(FIXES_DIR, "stuck_%04d.md" % idx)
    write_text(path, "\n".join(lines))
    return path


def call_fixer(idx, reasons, prior_verdict=None, model="opus"):
    """Escalate a jammed chapter to the engineer. Returns its verdict or None.

    Runs on opus deliberately: this is the one step in the pipeline that reasons
    about the pipeline itself, and it edits shared rules that touch all 4885
    chapters. The two jams seen so far both needed a corpus-wide regex written
    without breaking real prose -- not a job for the cheap tier.
    """
    build_fix_dossier(idx, reasons, prior_verdict)
    log("    >>> вызываю инженера (%s) на гл.%04d после %d отказов"
        % (model, idx, len(reasons)))

    ok, report = run_claude(
        "Файл задания: fixes/stuck_%04d.md" % idx,
        agent="fixer", timeout=FIXER_TIMEOUT, tag="fixer_%04d" % idx,
        model=model)
    if not ok:
        log("    <<< инженер не справился: %s" % short(report, 200))
        return None

    verdict_path = os.path.join(FIXES_DIR, "stuck_%04d.json" % idx)
    if not os.path.exists(verdict_path):
        log("    <<< инженер не оставил вердикта: %s" % short(report, 200))
        return None
    try:
        verdict = json.loads(read_text(verdict_path))
    except ValueError:
        log("    <<< вердикт инженера не разобрался как JSON")
        return None

    log("    <<< причина: %s | %s"
        % (verdict.get("root_cause", "?"), short(verdict.get("diagnosis", ""), 200)))
    if verdict.get("action"):
        log("        сделано: %s" % short(verdict["action"], 200))
    if verdict.get("needs_human"):
        log("        [!] инженер просит человека — см. %s" % verdict_path)
    return verdict


def reject_chapter(idx, why):
    os.makedirs(REJECT_DIR, exist_ok=True)
    src = ru_path(idx)
    if os.path.exists(src):
        # Keep the evidence, but get it out of ru/ -- a file left there would be
        # counted as a finished chapter by every other script in the project.
        os.replace(src, os.path.join(REJECT_DIR, "chapter_%04d.txt" % idx))
    log("    [брак] гл.%04d: %s" % (idx, why))


def update_glossary(report_line, idx):
    """Fold this chapter's terms in before the next chapter is dispatched."""
    with GlossaryLock():
        if report_line:
            with open(REPORTS_FILE, "a", encoding="utf-8") as fh:
                fh.write(report_line.strip() + "\n")

        # Both of these exit 1 on unresolved conflicts, and that is not fatal
        # here: the disputed term simply stays out of the glossary, which is the
        # safe default. Auto-picking a winner is exactly the drift this design
        # prevents.
        run_py("glossary_accumulate.py", allow_fail=True)
        rc, out = run_py("glossary_merge.py", allow_fail=True)
        if rc != 0:
            with open(COLLISION_LOG, "a", encoding="utf-8") as fh:
                fh.write("--- после гл.%04d\n%s\n" % (idx, out[-1500:]))


def do_prewave(lo, hi, model="sonnet"):
    log("[этап] термины диапазона %d-%d до перевода" % (lo, hi))
    rc, out = run_py("prewave_terms.py", "--range", str(lo), str(hi), allow_fail=True)
    tag = "wave_%04d_%04d" % (lo, hi)
    task = os.path.join(ROOT, "glossary_batches", "judge_" + tag + ".md")
    if not os.path.exists(task):
        log("    новых кандидатов нет, суд не нужен")
        return
    ok, report = run_claude(
        "Файл задания: glossary_batches/judge_%s.md" % tag,
        agent="judge", tag="judge", model=model)
    log("    судья: %s" % (report.replace("\n", " ")[:160] if ok else "НЕ СПРАВИЛСЯ"))
    with GlossaryLock():
        run_py("glossary_merge.py", allow_fail=True)


def main():
    global CLAUDE
    ap = argparse.ArgumentParser(description="Последовательный автономный перевод")
    ap.add_argument("--count", type=int, default=70, help="сколько глав перевести за прогон")
    ap.add_argument("--until", type=int, metavar="N",
                    help="переводить до главы N включительно (вместо --count)")
    ap.add_argument("--range", type=int, nargs=2, metavar=("LO", "HI"),
                    help="только главы LO..HI — участок параллельного прогона")
    ap.add_argument("--slot", default="",
                    help="имя параллельного прогона: свои файлы состояния и логов")
    # Суд идёт по умолчанию. Раньше он включался флагом --prewave, и прогон
    # 119-500 ушёл без него просто потому, что флаг забыли дописать. Цена
    # забывчивости: 13 терминов разъехались по смыслу, 56 лишних написаний,
    # народ 亞特族群 получил пятнадцать разных имён. Отключать теперь надо
    # осознанно, а не помнить о включении.
    ap.add_argument("--no-prewave", dest="prewave", action="store_false",
                    default=True, help="не судить коинажи диапазона перед переводом")
    ap.add_argument("--wave", type=int, default=100, metavar="N",
                    help="судить термины окнами по N глав (по умолчанию 100)")
    ap.add_argument("--model", default="sonnet",
                    help="модель для переводчика и судьи терминов (по умолчанию sonnet)")
    ap.add_argument("--fixer-model", default="opus",
                    help="модель для инженера при застрявшей главе (по умолчанию opus)")
    ap.add_argument("--status", action="store_true", help="показать состояние и выйти")
    args = ap.parse_args()

    # Свои файлы у каждого параллельного прогона, и переименование ДО первого
    # обращения к ним. Общий state с шестнадцатью писателями терял бы "done"
    # пятнадцати из них при каждом save_state, а общий лог перемешивал бы
    # строки до нечитаемости.
    global STATE_FILE, LOG_FILE, USAGE_FILE
    if args.slot:
        STATE_FILE = os.path.join(ROOT, "autorun_state_%s.json" % args.slot)
        LOG_FILE = os.path.join(ROOT, "autorun_%s.log" % args.slot)
        USAGE_FILE = os.path.join(ROOT, "autorun_usage_%s.jsonl" % args.slot)
        if args.range:
            # Участок слота — для сводки parallel_status.py. Свой файл на слот:
            # общий журнал при одновременном старте шестнадцати слотов
            # затирал бы записи друг друга.
            save_json(os.path.join(ROOT, "parallel_range_%s.json" % args.slot),
                      {"slot": args.slot, "lo": args.range[0], "hi": args.range[1]})

    state = load_state()
    todo = pending_chapters()
    # Nothing is ever excluded from the queue. A chapter that failed before
    # is simply a chapter without a file, and it comes first in reading order.
    aside = set(state.get("set_aside", []))
    # --until names a place in the book, --count a quantity of work. The former
    # survives restarts unchanged: after a crash at chapter 300 the same
    # --until 500 still means "up to 500", where --count would silently extend
    # the target by everything already finished.
    if args.range:
        lo, hi = args.range
        queue = [i for i in todo if lo <= i <= hi]
    elif args.until:
        queue = [i for i in todo if i <= args.until]
    else:
        queue = todo[:args.count]

    if args.status:
        done = len([f for f in os.listdir(RU_DIR) if f.startswith("chapter_")])
        print("Переведено файлов : %d" % done)
        print("Осталось          : %d" % len(todo))
        print("Не дались ранее   : %s" % (sorted(aside) or "нет"))
        print("Очередь этого прогона: %s" % (queue[:10] or "пусто"))
        u = usage_summary()
        if u:
            print("")
            print("Замерено на %d главах:" % u["chapters"])
            print("  токенов на главу : %.1fk" % (u["tokens_per_chapter"] / 1000))
            print("  долларов на главу: $%.4f" % u["usd_per_chapter"])
            print("  прогноз на остаток (%d глав): %.1fM токенов, $%.0f"
                  % (len(todo),
                     u["tokens_per_chapter"] * len(todo) / 1e6,
                     u["usd_per_chapter"] * len(todo)))
        return

    if not queue:
        log("Непереведённых глав не осталось.")
        return

    CLAUDE = find_claude() if llm.provider()["kind"] == "cli" else None
    blockers = llm.problems()
    if blockers:
        raise SystemExit("Провайдер модели не готов:\n  " + "\n  ".join(blockers))
    state["started"] = dt.datetime.now().isoformat(timespec="seconds")
    save_state(state)

    log("=" * 62)
    log("Последовательный прогон: %d глав, с %d по %d" % (len(queue), queue[0], queue[-1]))
    log("Агент: translator (%s). Лимиты пережидаются, работа не теряется." % args.model)
    log("=" * 62)

    # Суд идёт окнами, а не одним куском на всю очередь. Раньше здесь стоял один
    # вызов на queue[0]..queue[-1], и на прогоне «до конца книги» это означало
    # диапазон в 3476 глав: судье уходит список кандидатов, собранный со всего
    # остатка, а перевод не начинается, пока он не вынесет вердикт по всему.
    # Окно возвращает механизм в тот размер, на котором он обкатан.
    judged_until = -1

    started = time.time()
    ok_count = 0
    for n, idx in enumerate(queue, 1):
        if os.path.exists(ru_path(idx)):
            continue                      # finished by an earlier run

        # Окно судится ровно перед тем, как в него войти. Считается от текущей
        # главы, а не от начала очереди, поэтому перезапуск после обрыва берёт
        # окно с места остановки и не пересуживает уже пройденное.
        if args.prewave and idx > judged_until:
            hi = min(idx + args.wave - 1, queue[-1])
            do_prewave(idx, hi, args.model)
            judged_until = hi
        attempts = 0
        reasons = []            # one rejection reason per failed acceptance
        fixer_runs = 0
        verdict = None

        # Strictly blocking: chapter N+1 is never started until N is on disk
        # and validated. Skipping is not a lesser failure here, it is the worst
        # one -- the chapters after a gap get translated without the terms the
        # missing chapter introduced, so one skip silently corrupts everything
        # downstream of it. Better to sit on a chapter for an hour.
        while True:
            attempts += 1
            state["attempts"][str(idx)] = attempts
            save_state(state)

            log("[%d/%d] гл.%04d, попытка %d" % (n, len(queue), idx, attempts))
            run_py("build_batches.py", "--only", str(idx), allow_fail=True)

            ok, report = run_claude(
                "Файл задания: batches/task_%04d.md" % idx,
                agent="translator", tag="%04d" % idx, model=args.model)
            if not ok:
                log("    агент не справился: %s" % short(report, 200))
                wait_for_internet()
                sleep_noisily(min(30 * attempts, 300), "повтор той же главы")
                continue

            good, why = accept_chapter(idx)
            if not good:
                reject_chapter(idx, why)
                reasons.append(why)

                # Three rejections is the signal, not the count of a countdown:
                # the engineer is called once per FIXER_AFTER failures, and its
                # verdict is fed back in if a second call is needed.
                if len(reasons) % FIXER_AFTER == 0 and fixer_runs < MAX_FIXER_RUNS:
                    fixer_runs += 1
                    verdict = call_fixer(idx, reasons, verdict, args.fixer_model)
                    state["fixer"] = state.get("fixer", {})
                    state["fixer"][str(idx)] = {
                        "runs": fixer_runs,
                        "verdict": verdict,
                    }
                    save_state(state)
                    if verdict and verdict.get("retry"):
                        # The cause is reportedly gone, so the next attempt is a
                        # fresh start rather than a fourth identical failure.
                        reasons = []
                        continue

                if fixer_runs >= MAX_FIXER_RUNS:
                    # Still blocking on purpose -- skipping corrupts the glossary
                    # for every later chapter -- but at a pace that does not burn
                    # the night's budget on a chapter nobody can translate.
                    log("    [!] гл.%04d не поддаётся, инженер отработал %d раза. "
                        "Нужен человек: fixes/stuck_%04d.json"
                        % (idx, fixer_runs, idx))
                    sleep_noisily(STUCK_SLEEP, "глава ждёт человека")
                    continue

                sleep_noisily(min(15 * attempts, 120), "перевод не прошёл приёмку")
                continue

            update_glossary(report, idx)
            state["done"].append(idx)
            save_state(state)
            ok_count += 1
            elapsed = (time.time() - started) / 60
            log("    готово | %s | всего %d, %.0f мин, ~%.1f мин/глава"
                % (short(report), ok_count, elapsed,
                   elapsed / max(ok_count, 1)))
            break

    log("=" * 62)
    log("Прогон закончен: переведено %d из %d за %.0f мин"
        % (ok_count, len(queue), (time.time() - started) / 60))
    log("Дальше: python verify.py")


if __name__ == "__main__":
    main()
