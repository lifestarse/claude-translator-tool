#!/usr/bin/env python3
"""Аудит глоссария: термины, которые переводчик добавил сам, против выверенного канона.

Переводчик в отчёте называет новые имена, и конвейер сразу кладёт их в
глоссарий (glossary_accumulate → glossary_merge), чтобы следующая глава уже
знала имя. Без проверки терминология разъезжается. На «Охотнике из другого
мира» за 324 главы набралось 526 таких терминов, и вышло вот что:
- одни и те же сущности получили разные имена: «больница «Нео-Сеул»» и
  «сеульская больница Нео», «Клятва Рыцаря» и «Рыцарская клятва»;
- старые сущности под новым английским именем стали новыми: «Весы истины»
  вместо канонной «Юстиции»;
- персонаж сменил пол, потому что английский источник раз ошибся с «she».

Аудит отдаёт такие термины судье (агент glossaudit) вместе с каноном и
выровненными примерами из текста. Вердикты ложатся в glossary_overrides.json:
мусор снимается, дубли и старые сущности получают каноническое имя,
персонажи — род. Проверенные термины запоминаются в glossary_audit/audited.json,
и следующий аудит берёт только новые.

    python glossary_audit.py build [--range LO HI]   # пакеты для непроверенных терминов
    python glossary_audit.py run [--model M]          # судья на каждый пакет
    python glossary_audit.py apply                    # вердикты → overrides → glossary_merge
    python glossary_audit.py all [--model M]          # всё подряд

autorun.py делает то же каждые --audit-every глав. Текст уже переведённых глав
аудит не трогает: если имя изменилось, главы выравнивает term_align.py.
"""

import argparse
import collections
import datetime as dt
import glob
import json
import os
import re
import shutil
import subprocess
import sys

from common import ROOT, load_json, save_json
from term_match import aligned, gender_label, source_key_rx, translated_chapters

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

AUDIT_DIR = os.path.join(ROOT, "glossary_audit")
AUDITED_FILE = os.path.join(AUDIT_DIR, "audited.json")
CONFLICTS_LOG = os.path.join(AUDIT_DIR, "conflicts.log")
GLOSSARY_V2 = os.path.join(ROOT, "glossary_v2.json")
OVERRIDES = os.path.join(ROOT, "glossary_overrides.json")
REPORTS_FILE = os.path.join(ROOT, "new_terms_raw.txt")
AGENT = "glossaudit"
BATCH = 80
EXAMPLES = 3


def rel(path):
    return os.path.relpath(path, ROOT).replace(os.sep, "/")


def first_chapters():
    """Термин → глава, где переводчик назвал его впервые (по new_terms_raw.txt)."""
    first = {}
    if not os.path.exists(REPORTS_FILE):
        return first
    for line in open(REPORTS_FILE, encoding="utf-8"):
        m = re.match(r"\s*(\d+)\s+ok\b.*?новые термины:\s*(.*)$", line.strip())
        if not m:
            continue
        for part in re.split(r",\s*(?=[^,=]+=)", m.group(2)):
            if "=" in part:
                first.setdefault(part.split("=")[0].strip(), int(m.group(1)))
    return first


def pending_verdicts():
    return sorted(glob.glob(os.path.join(AUDIT_DIR, "verdict_*.json")))


def _evidence(cn, ru, chapters):
    """Сколько выровненных абзацев с ключом и примеры «оригинал / перевод»."""
    rx = source_key_rx(cn)
    occ = []
    for n, (src, tr, joined) in chapters.items():
        if not rx.search(joined):
            continue
        for i, (ps, pr) in enumerate(zip(src, tr)):
            m = rx.search(ps)
            if m:
                occ.append((n, i, m, ps, pr))
    picks = []
    for o in occ[:1] + occ[len(occ) // 2:len(occ) // 2 + 1] + occ[-1:]:
        if o not in picks:
            picks.append(o)
    lines = []
    for n, i, m, ps, pr in picks[:EXAMPLES]:
        lines.append("  - гл.%d ¶%d оригинал: …%s…" % (n, i + 1, ps[max(0, m.start() - 90):m.end() + 90].replace("\n", " ")))
        lines.append("    перевод: %s" % pr[:300].replace("\n", " "))
    chs = sorted({o[0] for o in occ})
    return chs, lines


def build(lo=None, hi=None, limit=None):
    """Пакеты для терминов переводчика, которых судья ещё не видел. Возвращает их число."""
    g = load_json(GLOSSARY_V2)
    if not g:
        return 0
    os.makedirs(AUDIT_DIR, exist_ok=True)
    for old in glob.glob(os.path.join(AUDIT_DIR, "batch_*.md")):
        os.remove(old)
    audited = load_json(AUDITED_FILE, {}) or {}
    entries = g["glossary"]
    todo = sorted((e for e in entries if e.get("source") == "translator" and e["cn"] not in audited),
                  key=lambda e: e["cn"].lower())
    if limit:
        todo = todo[:limit]
    if not todo:
        return 0

    # Канон: всё выверенное плюс прошлые вердикты, сгруппировано по русскому имени.
    canon = collections.OrderedDict()
    for e in entries:
        if e.get("source") != "translator" or e["cn"] in audited:
            canon.setdefault(e["ru"], []).append(e)
    ref = ["# Канон глоссария", "",
           "РУССКОЕ ИМЯ | род | категория | ключи оригинала | пояснение", ""]
    for ru, es in canon.items():
        ref.append("- %s | %s | %s | %s | %s" % (ru, es[0].get("gender") or "—", es[0].get("category", ""),
                   "; ".join(sorted({x["cn"] for x in es})), re.sub(r"\s+", " ", es[0].get("description", ""))[:170]))
    open(os.path.join(AUDIT_DIR, "reference.md"), "w", encoding="utf-8").write("\n".join(ref) + "\n")

    first = first_chapters()
    lst = ["# Все непроверенные термины переводчика (ключ = русское имя, первая глава)", ""]
    lst += ["- %s = %s (гл.%s)" % (e["cn"], e["ru"], first.get(e["cn"], "?")) for e in todo]
    open(os.path.join(AUDIT_DIR, "all_terms.md"), "w", encoding="utf-8").write("\n".join(lst) + "\n")

    chapters = {}
    for n in translated_chapters(lo, hi):
        al = aligned(n)
        if al:
            chapters[n] = (al[0], al[1], "\n".join(al[0]))
    shared = collections.defaultdict(list)
    for c in g.get("ru_collisions", []):
        for t in c["terms"]:
            shared[t["cn"]] = [x["cn"] for x in c["terms"] if x["cn"] != t["cn"]]

    for b, start in enumerate(range(0, len(todo), BATCH), 1):
        out = ["# Аудит глоссария: пакет %02d" % b, "",
               "Канон: `%s`. Все непроверенные термины: `%s`. Результат запиши в `%s`." % (
                   rel(os.path.join(AUDIT_DIR, "reference.md")), rel(os.path.join(AUDIT_DIR, "all_terms.md")),
                   rel(os.path.join(AUDIT_DIR, "verdict_%02d.json" % b))), ""]
        for e in todo[start:start + BATCH]:
            chs, lines = _evidence(e["cn"], e["ru"], chapters)
            span = ("гл.%d–%d, %d глав" % (chs[0], chs[-1], len(chs))) if chs else "в тексте дословно не найден"
            out.append("### %s = %s" % (e["cn"], e["ru"]))
            out.append("  встречается: %s; переводчик назвал впервые в гл.%s" % (span, first.get(e["cn"], "?")))
            if shared.get(e["cn"]):
                out.append("  то же русское имя у: %s" % "; ".join(shared[e["cn"]][:8]))
            out += lines
            out.append("")
        open(os.path.join(AUDIT_DIR, "batch_%02d.md" % b), "w", encoding="utf-8").write("\n".join(out) + "\n")
    return len(todo)


def run(runner):
    """Судья на каждый пакет без вердикта. runner(prompt, agent, tag) → (ok, отчёт)."""
    results = []
    for path in sorted(glob.glob(os.path.join(AUDIT_DIR, "batch_*.md"))):
        nn = re.search(r"batch_(\d+)\.md$", path).group(1)
        verdict = os.path.join(AUDIT_DIR, "verdict_%s.json" % nn)
        if os.path.exists(verdict):
            continue
        ok, report = runner("Файл задания: %s" % rel(path), AGENT, "audit_%s" % nn)
        data = load_json(verdict)
        if not (ok and isinstance(data, dict) and isinstance(data.get("verdicts"), list)):
            ok = False
            if os.path.exists(verdict):
                os.replace(verdict, verdict + ".bad")
        results.append((nn, ok, (report or "").replace("\n", " ")[:160]))
    return results


def apply():
    """Вердикты → glossary_overrides.json → glossary_merge.py. Возвращает сводку."""
    paths = pending_verdicts()
    summary = {"judged": 0, "dropped": 0, "renamed": [], "gendered": 0, "conflicts": 0}
    if not paths:
        return summary
    own, claims = {}, collections.defaultdict(list)
    for path in paths:
        for v in (load_json(path, {}) or {}).get("verdicts", []):
            if not isinstance(v, dict) or not v.get("cn"):
                continue
            own[v["cn"]] = v
            for k in v.get("group") or []:
                if k != v["cn"] and v.get("ru"):
                    claims[k].append(v["ru"])
    # Решение о термине принимает судья его собственного пакета; чужая группа
    # только сообщает о расхождении — человек увидит его в conflicts.log.
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    conflicts = ["%s: свой вердикт «%s», группа соседнего пакета «%s»" % (k, own[k].get("ru"), "», «".join(sorted(set(v))))
                 for k, v in claims.items() if k in own and own[k].get("ru") and set(v) - {own[k]["ru"]}]
    if conflicts:
        with open(CONFLICTS_LOG, "a", encoding="utf-8") as fh:
            fh.write("--- %s\n%s\n" % (stamp, "\n".join(conflicts)))
    summary["conflicts"] = len(conflicts)

    g = {e["cn"]: e for e in (load_json(GLOSSARY_V2) or {}).get("glossary", [])}
    ov = load_json(OVERRIDES, {}) or {}
    ov.setdefault("drop", {})
    ov.setdefault("add", [])
    by_add = {a["cn"]: a for a in ov["add"]}
    first = first_chapters()
    tag = "аудит %s: " % dt.date.today().strftime("%d.%m.%Y")
    for cn, v in own.items():
        cur = g.get(cn, {})
        if v.get("verdict") == "drop":
            ov["drop"].setdefault(cn, tag + (v.get("note") or "не термин"))
            summary["dropped"] += 1
            continue
        ru, gender = v.get("ru") or cur.get("ru"), gender_label(v.get("gender", ""))
        if not ru:
            continue
        if ru == cur.get("ru") and (not gender or gender == cur.get("gender")):
            continue
        entry = {"cn": cn, "ru": ru, "gender": gender or cur.get("gender", ""),
                 "category": cur.get("category") or "Проверено судьёй",
                 "description": tag + (v.get("note") or v.get("verdict", ""))}
        if cn in by_add:
            by_add[cn].update(entry)
        else:
            ov["add"].append(entry)
            by_add[cn] = entry
        if ru != cur.get("ru"):
            summary["renamed"].append((cn, cur.get("ru", ""), ru, first.get(cn)))
        if gender and gender != cur.get("gender"):
            summary["gendered"] += 1
    summary["judged"] = len(own)

    done = os.path.join(AUDIT_DIR, "done", stamp)
    os.makedirs(done, exist_ok=True)
    if os.path.exists(OVERRIDES):
        shutil.copy(OVERRIDES, os.path.join(done, "glossary_overrides.before.json"))
    save_json(OVERRIDES, ov)
    subprocess.run([sys.executable, "glossary_merge.py"], cwd=ROOT, capture_output=True,
                   encoding="utf-8", errors="replace")
    audited = load_json(AUDITED_FILE, {}) or {}
    for cn, v in own.items():
        audited[cn] = {"verdict": v.get("verdict"), "ru": v.get("ru", ""), "at": stamp}
    save_json(AUDITED_FILE, audited)
    for path in glob.glob(os.path.join(AUDIT_DIR, "batch_*.md")) + paths:
        shutil.move(path, os.path.join(done, os.path.basename(path)))
    return summary


def describe(s):
    line = "проверено %d, снято %d, переименовано %d, род уточнён у %d" % (
        s["judged"], s["dropped"], len(s["renamed"]), s["gendered"])
    if s["conflicts"]:
        line += ", расхождений судей %d (см. %s)" % (s["conflicts"], rel(CONFLICTS_LOG))
    return line


def align_hint(s, hi=None):
    """Команда выравнивания уже переведённых глав, если имена поменялись."""
    firsts = [f for *_, f in s["renamed"] if f]
    if not s["renamed"]:
        return ""
    lo = min(firsts) if firsts else 1
    if hi:
        lo = min(lo, hi)
    rng = "--range %d %d " % (lo, hi) if hi else "--range %d 99999 " % lo
    return "python term_align.py build %s&& python term_align.py run" % rng


def _runner(model, effort):
    try:
        import autorun  # ждёт лимиты и пишет расход; без него — простой запуск
        return lambda prompt, agent, tag: autorun.run_claude(prompt, agent, tag=tag, model=model, effort=effort)
    except Exception:  # noqa: BLE001
        import llm

        def plain(prompt, agent, tag):
            proc = llm.run_agent(prompt, agent, model)
            return proc.returncode == 0, proc.stdout
        return plain


def _lock():
    try:
        import autorun
        return autorun.GlossaryLock()
    except Exception:  # noqa: BLE001
        import contextlib
        return contextlib.nullcontext()


def main():
    ap = argparse.ArgumentParser(description="Аудит терминов, добавленных переводчиком")
    ap.add_argument("cmd", choices=("build", "run", "apply", "all"))
    ap.add_argument("--range", type=int, nargs=2, metavar=("LO", "HI"), help="откуда брать примеры")
    ap.add_argument("--model", default="sonnet", help="модель судьи")
    ap.add_argument("--effort", choices=("low", "medium", "high", "xhigh", "max"))
    ap.add_argument("--limit", type=int, help="не больше N терминов (проба)")
    args = ap.parse_args()
    lo, hi = args.range if args.range else (None, None)

    if args.cmd in ("build", "all"):
        if pending_verdicts():
            raise SystemExit("Есть неприменённые вердикты (%s). Сначала: python glossary_audit.py apply"
                             % ", ".join(os.path.basename(p) for p in pending_verdicts()))
        n = build(lo, hi, args.limit)
        print("Непроверенных терминов: %d%s" % (n, "" if n else " — аудит не нужен"))
        if not n:
            return
    if args.cmd in ("run", "all"):
        for nn, ok, report in run(_runner(args.model, args.effort)):
            print("пакет %s: %s" % (nn, report if ok else "НЕ СПРАВИЛСЯ — " + report))
    if args.cmd in ("apply", "all"):
        with _lock():
            s = apply()
        print("Аудит: " + describe(s))
        for cn, old, new, first in s["renamed"][:30]:
            print("  %s: «%s» → «%s»%s" % (cn, old, new, " (с гл.%d)" % first if first else ""))
        hint = align_hint(s)
        if hint:
            print("Имена поменялись после того, как попали в главы. Выровнять текст: " + hint)


if __name__ == "__main__":
    main()
