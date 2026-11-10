#!/usr/bin/env python3
"""Chinese -> Russian chapter translator.

This actually reads chapters/chapter_NNNN.txt and calls the Anthropic API.
Every output is validated before it is accepted; a file that fails validation
is retried with the specific failure fed back to the model, and if it still
fails it is recorded as failed rather than written.

    pip install anthropic
    setx ANTHROPIC_API_KEY sk-ant-...
    python translate.py --limit 3          # try a few first
    python translate.py                    # everything not yet done
"""

import argparse
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from common import (
    CHAPTERS_DIR,
    RU_DIR,
    chapter_path,
    load_glossary,
    load_json,
    read_text,
    relevant_terms,
    ru_path,
    save_json,
    split_paragraphs,
    translation_stats,
    validate_translation,
    write_text,
)

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "translate_state.json")
GLOSSARY_V2_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "glossary_v2.json")
DEFAULT_MODEL = "claude-opus-5"
MAX_TOKENS = 32000

# $ per 1M tokens: (input, output, cache_write_5m, cache_read)
PRICING = {
    "claude-opus-5-5": (4.00, 20.00, 5.00, 0.40),
    "claude-opus-5": (5.00, 25.00, 6.25, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00, 2.50, 0.20),
    "claude-sonnet-5": (2.00, 10.00, 2.50, 0.20),
    "claude-haiku-4-5": (1.00, 5.00, 1.25, 0.10),
}

_print_lock = threading.Lock()
_state_lock = threading.Lock()


def log(msg):
    with _print_lock:
        print(msg, flush=True)


# --------------------------------------------------------------------------
# Prompt construction
# --------------------------------------------------------------------------

RULES = """Ты профессиональный литературный переводчик с китайского (традиционного) на русский язык.
Переводишь веб-новеллу: {{BOOK_BRIEF}}

ЖЕЛЕЗНЫЕ ПРАВИЛА. Их нарушение делает работу браком:

1. ПОЛНЫЙ ПЕРЕВОД, НЕ ПЕРЕСКАЗ. Переводишь весь текст целиком, от первого абзаца до последнего. Запрещено сокращать, обобщать, сводить несколько абзацев в один, опускать «неважные» детали, описания и повторы.

2. АБЗАЦ В АБЗАЦ. Число абзацев на выходе строго совпадает с числом абзацев на входе. Абзацы разделены пустой строкой. Один абзац оригинала = один абзац перевода, в том же порядке. Короткие реплики и однословные абзацы («?», «死了?») остаются отдельными абзацами.

3. НИЧЕГО НЕ ВЫДУМЫВАТЬ. Запрещено добавлять события, реплики, объяснения или выводы, которых нет в оригинале. Если фрагмент непонятен — переводи буквально, не домысливай.

4. ПЕРВАЯ СТРОКА — ЗАГОЛОВОК. Переводишь заголовок главы. Формат: «Глава N: Название». Если в оригинале склейка («第11章+12章»), сохраняешь её: «Главы 11-12: Название».

5. ТЕРМИНОЛОГИЯ. Имена и термины из глоссария передаёшь ровно так, как там указано, и согласуешь в указанном роде. Термина нет в глоссарии — переводишь по смыслу и держишься этого варианта внутри главы.

6. СТИЛЬ. Живая русская проза, а не подстрочник. Сохраняешь регистр оригинала: внутренний монолог Линь — от первого лица, короткие рубленые предложения остаются рублеными. Мысли персонажей в кавычках «...», как в оригинале.

7. ЧИСТЫЙ ВЫВОД. Ответ содержит только текст перевода. Никаких предисловий, комментариев, пометок переводчика, markdown-разметки и служебных тегов. Ни одного китайского иероглифа в результате."""


def build_system_prompt(curated_terms, pronoun_rules, core_limit=180):
    """Byte-stable across every request -> cacheable prefix.

    Nothing chapter-specific may appear here: a single changed byte invalidates
    the cache for every request that follows.
    """
    parts = [RULES]

    if pronoun_rules:
        lines = ["", "СОГЛАСОВАНИЕ В РОДЕ:"]
        for name in sorted(pronoun_rules):
            rule = pronoun_rules[name]
            lines.append("- %s — %s. %s" % (name, rule.get("ru_gender", ""), rule.get("rule_desc", "")))
        parts.append("\n".join(lines))

    core = sorted(
        curated_terms,
        key=lambda t: (-(t.get("total_mentions") or 0), t["cn"]),
    )[:core_limit]
    if core:
        lines = ["", "ОСНОВНОЙ ГЛОССАРИЙ (сквозной для всей книги):"]
        for t in sorted(core, key=lambda t: t["cn"]):
            bits = ["%s = %s" % (t["cn"], t["ru"])]
            if t.get("gender"):
                bits.append(t["gender"])
            if t.get("category"):
                bits.append(t["category"])
            lines.append("- " + " | ".join(bits))
        parts.append("\n".join(lines))

    return "\n".join(parts)


def build_user_message(source, chapter_terms, problems=None):
    parts = []
    if chapter_terms:
        lines = ["ТЕРМИНЫ, ВСТРЕЧАЮЩИЕСЯ В ЭТОЙ ГЛАВЕ:"]
        for t in chapter_terms:
            bits = ["%s = %s" % (t["cn"], t["ru"])]
            if t.get("gender"):
                bits.append(t["gender"])
            if t.get("description"):
                bits.append(t["description"])
            lines.append("- " + " | ".join(bits))
        parts.append("\n".join(lines))

    n = len(split_paragraphs(source))
    parts.append(
        "В исходном тексте ниже %d абзацев (считая заголовок). "
        "В переводе должно быть ровно %d абзацев, разделённых пустой строкой." % (n, n)
    )

    if problems:
        parts.append(
            "ПРЕДЫДУЩАЯ ПОПЫТКА ЗАБРАКОВАНА:\n"
            + "\n".join("- " + p for p in problems)
            + "\nПереведи заново, устранив это. Переводи полностью, абзац в абзац."
        )

    parts.append("ИСХОДНЫЙ ТЕКСТ:\n\n" + source)
    parts.append("Выведи только перевод.")
    return "\n\n".join(parts)


# --------------------------------------------------------------------------
# API call
# --------------------------------------------------------------------------


def call_model(client, anthropic, model, system_prompt, user_text, effort):
    """One streamed request. Returns (text, usage). Raises on refusal."""
    kwargs = dict(
        model=model,
        max_tokens=MAX_TOKENS,
        system=[
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_text}],
    )
    if effort:
        kwargs["output_config"] = {"effort": effort}

    with client.messages.stream(**kwargs) as stream:
        message = stream.get_final_message()

    if message.stop_reason == "refusal":
        detail = getattr(getattr(message, "stop_details", None), "category", None)
        raise RuntimeError("модель отказалась отвечать (категория: %s)" % detail)
    if message.stop_reason == "max_tokens":
        raise RuntimeError("ответ обрезан по max_tokens (%d) — глава слишком длинная" % MAX_TOKENS)

    text = "".join(b.text for b in message.content if b.type == "text").strip()
    return text, message.usage


def usage_cost(model, usage):
    rates = PRICING.get(model)
    if not rates:
        return 0.0
    inp, out, cw, cr = rates
    return (
        (usage.input_tokens or 0) * inp
        + (usage.output_tokens or 0) * out
        + (getattr(usage, "cache_creation_input_tokens", 0) or 0) * cw
        + (getattr(usage, "cache_read_input_tokens", 0) or 0) * cr
    ) / 1_000_000.0


# --------------------------------------------------------------------------
# Per-chapter worker
# --------------------------------------------------------------------------


def translate_chapter(idx, client, anthropic, model, system_prompt, curated, auto, effort, attempts):
    src_path, out_path = chapter_path(idx), ru_path(idx)
    source = read_text(src_path).strip()
    if not source:
        return idx, "fail", {"problems": ["исходный файл пуст"]}

    terms = relevant_terms(curated, source) + relevant_terms(auto, source, limit=40)
    problems, cost, last_text = None, 0.0, ""

    for attempt in range(1, attempts + 1):
        user_text = build_user_message(source, terms, problems)
        try:
            text, usage = call_model(client, anthropic, model, system_prompt, user_text, effort)
        except anthropic.RateLimitError:
            time.sleep(20 * attempt)
            continue
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500 and attempt < attempts:
                time.sleep(5 * attempt)
                continue
            return idx, "fail", {"problems": ["API %s: %s" % (exc.status_code, exc.message)], "cost": cost}
        except anthropic.APIConnectionError:
            if attempt < attempts:
                time.sleep(5 * attempt)
                continue
            return idx, "fail", {"problems": ["сеть недоступна"], "cost": cost}
        except RuntimeError as exc:
            return idx, "fail", {"problems": [str(exc)], "cost": cost}

        cost += usage_cost(model, usage)
        last_text = text
        problems = validate_translation(source, text)
        if not problems:
            write_text(out_path, text + "\n")
            info = translation_stats(source, text)
            info.update(cost=round(cost, 4), attempts=attempt)
            return idx, "ok", info

        log("    гл.%04d попытка %d забракована: %s" % (idx, attempt, "; ".join(problems)))

    # Never write a file that failed validation -- that is exactly how the
    # previous run ended up with 20 fabricated "translations" on disk.
    info = translation_stats(source, last_text) if last_text else {}
    info.update(problems=problems or ["не удалось получить ответ"], cost=round(cost, 4))
    return idx, "fail", info


# --------------------------------------------------------------------------


def pick_targets(state, only, limit, retry_failed, overwrite=False):
    """Что переводить. Готовый файл в ru/ — это граница, а не собственный журнал.

    translate_state.json знает только о главах, переведённых ЭТИМ скриптом.
    Основной корпус (1800+ глав) сделан autorun.py и в журнале не значится,
    поэтому прежняя проверка «status == ok» его не видела: голый запуск
    `python translate.py` перевёл бы весь готовый корпус заново, молча
    перезаписав главы, прошедшие приёмку и уже выложенные на сайт. Один такой
    случай уже был: гейтвей-вариант 31.07 перезаписал ru/chapter_0001.txt,
    главу восстанавливали из журнала выкладки.

    Отсюда два правила: существующий файл перевода не трогается без --overwrite,
    а исключённые главы (дубли и пустышки источника) не переводятся вовсе.
    """
    from common import load_excluded
    excluded = load_excluded()
    if only:
        picked = []
        for idx in sorted(only):
            if idx in excluded:
                log("[!] гл.%04d исключена из перевода (%s) — пропуск" % (idx, excluded[idx]))
                continue
            if os.path.exists(ru_path(idx)) and not overwrite:
                log("[!] гл.%04d уже переведена; перезапись только с --overwrite" % idx)
                continue
            picked.append(idx)
        return picked
    todo = []
    for name in sorted(os.listdir(CHAPTERS_DIR)):
        if not (name.startswith("chapter_") and name.endswith(".txt")):
            continue
        idx = int(name[8:-4])
        if idx in excluded:
            continue
        if os.path.exists(ru_path(idx)):
            continue                      # готово — чьей бы рукой ни было
        rec = state["chapters"].get(str(idx))
        if rec and rec.get("status") == "fail" and not retry_failed:
            continue
        todo.append(idx)
    return todo[:limit] if limit else todo


def main():
    ap = argparse.ArgumentParser(description="Переводчик глав CN->RU через Anthropic API")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="по умолчанию %s" % DEFAULT_MODEL)
    ap.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"])
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--attempts", type=int, default=3, help="попыток на главу перед отбраковкой")
    ap.add_argument("--limit", type=int, default=0, help="перевести не больше N глав")
    ap.add_argument("--only", type=int, nargs="*", help="конкретные номера файлов")
    ap.add_argument("--retry-failed", action="store_true", help="заново взяться за забракованные")
    ap.add_argument("--overwrite", action="store_true",
                    help="разрешить --only перезаписывать готовые главы из ru/")
    args = ap.parse_args()

    try:
        import anthropic
    except ImportError:
        print("Не установлен SDK. Выполни:  pip install anthropic")
        raise SystemExit(2)

    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        print("Не задан ANTHROPIC_API_KEY. Задай его и перезапусти терминал:")
        print('  setx ANTHROPIC_API_KEY "sk-ant-..."')
        raise SystemExit(2)

    os.makedirs(RU_DIR, exist_ok=True)
    client = anthropic.Anthropic(max_retries=5, timeout=600.0)

    # Рабочий глоссарий конвейера — glossary_v2.json (glossary_merge). Старый
    # glossary.json — только запасной вариант, как в build_batches.load_terms.
    v2 = load_json(GLOSSARY_V2_FILE, None) or {}
    if v2.get("glossary"):
        curated, auto, pronoun_rules = v2["glossary"], [], v2.get("pronoun_rules", {})
    else:
        curated, auto, pronoun_rules = load_glossary()
    system_prompt = build_system_prompt(curated, pronoun_rules)
    log(
        "[+] Глоссарий: %d выверенных терминов, %d авто-терминов; системный промпт %d символов."
        % (len(curated), len(auto), len(system_prompt))
    )

    state = load_json(STATE_FILE, None) or {"chapters": {}, "total_cost": 0.0}
    targets = pick_targets(state, args.only, args.limit, args.retry_failed, args.overwrite)
    if not targets:
        log("[+] Нечего переводить — всё уже готово.")
        return

    log("[+] К переводу %d глав | модель %s | effort %s | потоков %d"
        % (len(targets), args.model, args.effort, args.threads))

    # First chapter alone: writes the prompt cache and surfaces auth/model
    # errors before N threads hit the same wall.
    ok_count = fail_count = 0
    session_cost = 0.0
    t0 = time.time()

    def record(idx, status, info):
        nonlocal ok_count, fail_count, session_cost
        with _state_lock:
            state["chapters"][str(idx)] = dict(info, status=status)
            session_cost += info.get("cost", 0.0)
            state["total_cost"] = round(state.get("total_cost", 0.0) + info.get("cost", 0.0), 4)
            save_json(STATE_FILE, state)
        if status == "ok":
            ok_count += 1
        else:
            fail_count += 1

    head, rest = targets[0], targets[1:]
    idx, status, info = translate_chapter(
        head, client, anthropic, args.model, system_prompt, curated, auto, args.effort, args.attempts
    )
    record(idx, status, info)
    log("[%s] гл.%04d  %s" % ("ok" if status == "ok" else "БРАК", idx,
        "%.2fx, %d/%d абз., $%.4f" % (info.get("char_ratio", 0), info.get("ru_paras", 0),
                                       info.get("src_paras", 0), info.get("cost", 0))
        if status == "ok" else "; ".join(info.get("problems", []))))

    if status != "ok" and not args.only:
        log("")
        log("[!] Первая же глава забракована. Останавливаюсь, чтобы не жечь токены.")
        log("    Разберись с причиной выше, потом запускай снова.")
        raise SystemExit(1)

    if rest:
        with ThreadPoolExecutor(max_workers=args.threads) as pool:
            futures = {
                pool.submit(
                    translate_chapter, i, client, anthropic, args.model, system_prompt,
                    curated, auto, args.effort, args.attempts,
                ): i
                for i in rest
            }
            done = 1
            for fut in as_completed(futures):
                idx, status, info = fut.result()
                record(idx, status, info)
                done += 1
                if status != "ok":
                    log("[БРАК] гл.%04d: %s" % (idx, "; ".join(info.get("problems", []))))
                if done % 10 == 0 or done == len(targets):
                    rate = done / max(time.time() - t0, 1e-6)
                    eta = (len(targets) - done) / max(rate, 1e-6)
                    log("    [%d/%d] ok=%d брак=%d | $%.2f | осталось ~%dм"
                        % (done, len(targets), ok_count, fail_count, session_cost, eta // 60))

    log("")
    log("=== ИТОГ ===")
    log("Переведено и прошло проверку: %d" % ok_count)
    log("Забраковано:                  %d" % fail_count)
    log("Потрачено за запуск:          $%.2f" % session_cost)
    log("Потрачено всего:              $%.2f" % state.get("total_cost", 0.0))
    if ok_count:
        log("Средняя цена главы:           $%.4f" % (session_cost / ok_count))
    if fail_count:
        log("Забракованные перечислены в %s (status=fail). Повтор: --retry-failed" % STATE_FILE)
    raise SystemExit(0 if fail_count == 0 else 1)


if __name__ == "__main__":
    main()
