#!/usr/bin/env python3
"""Встроенный агент для OpenAI-совместимых API — замена `claude -p --agent`.

Принимает те же флаги, что и claude CLI, и печатает тот же JSON-конверт,
поэтому драйверам всё равно, кто отработал задание:

    python llm_agent.py -p "Файл задания: batches/task_0001.md" \\
        --agent translator --model ИМЯ_МОДЕЛИ --output-format json

Промпт агента и список его инструментов берутся из `.claude/agents/ИМЯ.md`
(тело файла и поле `tools:`). Инструменты повторяют набор Claude Code: Read,
Write, Edit, Bash, Grep, Glob. Файловые инструменты работают только внутри
каталога проекта.

Провайдер (адрес, ключ) — активный профиль `llm.py`. Запускать напрямую
обычно незачем: это делают `llm.agent_cmd()` и драйверы.
"""

import argparse
import fnmatch
import glob as globlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

import llm

ROOT = llm.ROOT
AGENTS_DIR = os.path.join(ROOT, ".claude", "agents")
MAX_TURNS = 80
READ_LIMIT = 2000           # строк за одно чтение
OUTPUT_LIMIT = 30000        # символов вывода инструмента
HTTP_TIMEOUT = 600
RETRIES = 4

HARNESS = """

---
Среда выполнения. Ты работаешь автономно в каталоге проекта; пути к файлам —
относительно него. Действуй только через инструменты, вопросов не задавай:
на них некому ответить. Инструмент Read отдаёт строки с номерами
(«номер<TAB>текст») — номер и табуляция в файл не входят, в Edit и Write их
не переноси. Когда работа сделана, ответь обычным сообщением без вызова
инструментов: оно и будет твоим отчётом."""

TOOL_SPECS = {
    "Read": ("Прочитать текстовый файл. Возвращает строки с номерами. Для длинного "
             "файла читай частями через offset/limit.",
             {"file_path": ("string", "путь к файлу"),
              "offset": ("integer", "номер первой строки, с 1"),
              "limit": ("integer", "сколько строк прочитать")}, ["file_path"]),
    "Write": ("Создать файл или полностью заменить его содержимое.",
              {"file_path": ("string", "путь к файлу"),
               "content": ("string", "полное содержимое файла")}, ["file_path", "content"]),
    "Edit": ("Точечная замена в файле: old_string обязан встречаться ровно один раз "
             "(или задай replace_all). Бери old_string из файла дословно, без номеров строк.",
             {"file_path": ("string", "путь к файлу"),
              "old_string": ("string", "заменяемый текст, дословно"),
              "new_string": ("string", "новый текст"),
              "replace_all": ("boolean", "заменить все вхождения")},
             ["file_path", "old_string", "new_string"]),
    "Bash": ("Выполнить команду оболочки в каталоге проекта.",
             {"command": ("string", "команда"),
              "timeout": ("integer", "секунд, по умолчанию 120, максимум 600")}, ["command"]),
    "Grep": ("Поиск регулярного выражения по файлам. Возвращает «файл:строка:текст».",
             {"pattern": ("string", "регулярное выражение"),
              "path": ("string", "файл или каталог, по умолчанию весь проект"),
              "glob": ("string", "маска имён файлов, например *.py")}, ["pattern"]),
    "Glob": ("Список файлов по маске, например ru/chapter_00*.txt или **/*.py.",
             {"pattern": ("string", "маска")}, ["pattern"]),
}


class ApiError(Exception):
    pass


class FatalApiError(ApiError):
    """Повтором не лечится: неверный ключ, имя модели, сам запрос."""


# --------------------------------------------------------------------------
# Описание агента
# --------------------------------------------------------------------------

def load_agent(name):
    """(системный промпт, [инструменты]) из .claude/agents/NAME.md."""
    if not name:
        return "", []
    path = os.path.join(AGENTS_DIR, name + ".md")
    if not os.path.exists(path):
        raise FatalApiError("нет описания агента: .claude/agents/%s.md" % name)
    with open(path, encoding="utf-8") as f:
        text = f.read()
    tools = list(TOOL_SPECS)
    m = re.match(r"^---\n(.*?)\n---\n?", text, re.S)
    if m:
        t = re.search(r"^tools:\s*(.+)$", m.group(1), re.M)
        if t:
            tools = [x.strip() for x in t.group(1).split(",") if x.strip() in TOOL_SPECS]
        text = text[m.end():]
    return text.strip(), tools


def tool_schema(names):
    out = []
    for n in names:
        desc, props, required = TOOL_SPECS[n]
        out.append({"type": "function", "function": {
            "name": n, "description": desc,
            "parameters": {
                "type": "object",
                "properties": {k: {"type": t, "description": d} for k, (t, d) in props.items()},
                "required": required,
            }}})
    return out


# --------------------------------------------------------------------------
# Инструменты
# --------------------------------------------------------------------------

def safe_path(path):
    full = os.path.realpath(os.path.join(ROOT, str(path or "")))
    root = os.path.realpath(ROOT)
    if full != root and not full.startswith(root + os.sep):
        raise ValueError("путь вне каталога проекта: %s" % path)
    return full


def clip(text):
    if len(text) > OUTPUT_LIMIT:
        return text[:OUTPUT_LIMIT] + "\n… [вывод обрезан, всего %d символов]" % len(text)
    return text


def tool_read(a):
    path = safe_path(a["file_path"])
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.read().split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    start = max(int(a.get("offset") or 1), 1)
    limit = min(int(a.get("limit") or READ_LIMIT), READ_LIMIT)
    chunk = lines[start - 1:start - 1 + limit]
    if not chunk:
        return "(пусто: в файле %d строк)" % len(lines)
    out = "\n".join("%6d\t%s" % (n, ln) for n, ln in enumerate(chunk, start))
    if start - 1 + limit < len(lines):
        out += "\n… [показаны строки %d-%d из %d]" % (start, start + len(chunk) - 1, len(lines))
    return clip(out)


def tool_write(a):
    path = safe_path(a["file_path"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(a["content"])
    return "записано: %s (%d символов)" % (a["file_path"], len(a["content"]))


def tool_edit(a):
    path = safe_path(a["file_path"])
    with open(path, encoding="utf-8", newline="") as f:
        text = f.read()
    old, new = a["old_string"], a["new_string"]
    if old == new:
        return "ОШИБКА: old_string и new_string совпадают"
    count = text.count(old)
    if count == 0:
        return ("ОШИБКА: old_string не найден в файле. Перечитай нужные строки и "
                "возьми текст дословно, без номеров строк.")
    if count > 1 and not a.get("replace_all"):
        return ("ОШИБКА: old_string встречается %d раз. Расширь его контекстом до "
                "единственного вхождения или задай replace_all." % count)
    text = text.replace(old, new) if a.get("replace_all") else text.replace(old, new, 1)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    return "заменено вхождений: %d" % (count if a.get("replace_all") else 1)


def tool_bash(a):
    timeout = min(int(a.get("timeout") or 120), 600)
    try:
        proc = subprocess.run(a["command"], shell=True, cwd=ROOT, capture_output=True,
                              timeout=timeout, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return "ОШИБКА: команда не завершилась за %d с" % timeout
    out = (proc.stdout or "") + (proc.stderr or "")
    return clip("%s\n[код возврата %d]" % (out.rstrip(), proc.returncode))


SKIP_DIRS = {".git", "__pycache__", "node_modules"}


def tool_grep(a):
    rx = re.compile(a["pattern"])
    base = safe_path(a.get("path") or ".")
    mask = a.get("glob")
    files = [base] if os.path.isfile(base) else []
    if not files:
        for d, dirs, names in os.walk(base):
            dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
            for n in sorted(names):
                if not mask or fnmatch.fnmatch(n, mask):
                    files.append(os.path.join(d, n))
    hits = []
    for path in sorted(files):
        try:
            with open(path, encoding="utf-8") as f:
                for n, line in enumerate(f, 1):
                    if rx.search(line):
                        hits.append("%s:%d:%s" % (os.path.relpath(path, ROOT), n,
                                                  line.rstrip("\n")[:300]))
                        if len(hits) >= 300:
                            return "\n".join(hits) + "\n… [обрезано на 300 совпадениях]"
        except (OSError, UnicodeDecodeError):
            continue
    return "\n".join(hits) or "(совпадений нет)"


def tool_glob(a):
    safe_path(os.path.dirname(a["pattern"]) or ".")
    found = sorted(globlib.glob(os.path.join(ROOT, a["pattern"]), recursive=True))
    found = [os.path.relpath(p, ROOT) for p in found if safe_path(p)]
    return "\n".join(found[:1000]) or "(ничего не найдено)"


TOOLS = {"Read": tool_read, "Write": tool_write, "Edit": tool_edit,
         "Bash": tool_bash, "Grep": tool_grep, "Glob": tool_glob}


def run_tool(name, raw_args, allowed):
    if name not in allowed:
        return "ОШИБКА: инструмент %s этому агенту недоступен" % name
    try:
        args = json.loads(raw_args or "{}")
        if not isinstance(args, dict):
            raise ValueError("аргументы должны быть объектом")
    except ValueError as exc:
        return "ОШИБКА: аргументы не разобрались как JSON (%s)" % exc
    try:
        return TOOLS[name](args)
    except KeyError as exc:
        return "ОШИБКА: не хватает аргумента %s" % exc
    except (OSError, ValueError, re.error) as exc:
        return "ОШИБКА: %s" % exc


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------

def chat(p, key, body):
    """Один запрос /chat/completions с повторами на перегрузке и обрыве."""
    url = p["base_url"].rstrip("/") + "/chat/completions"
    data = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": "claude-translator-tool"}
    if key:
        headers["Authorization"] = "Bearer " + key
    last = ""
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            if exc.code == 429:
                last = "rate limit (HTTP 429): %s" % detail
            elif exc.code >= 500:
                last = "service unavailable (HTTP %d): %s" % (exc.code, detail)
            else:
                # 400/401/403/404 повтором не лечатся: ключ, модель или запрос.
                raise FatalApiError("HTTP %d от %s: %s" % (exc.code, url, detail))
            wait = exc.headers.get("Retry-After") if exc.headers else None
            delay = float(wait) if wait and wait.replace(".", "", 1).isdigit() else 5 * 2 ** attempt
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last = "network error: %s" % exc
            delay = 5 * 2 ** attempt
        if attempt < RETRIES - 1:
            time.sleep(min(delay, 60))
    raise ApiError(last)


def run(prompt, agent, model, p, usage):
    key = llm.api_key(p)
    system, tool_names = load_agent(agent)
    messages = []
    if system or tool_names:
        messages.append({"role": "system", "content": (system + HARNESS).strip()})
    messages.append({"role": "user", "content": prompt})
    cost, turns = None, 0

    while turns < MAX_TURNS:
        turns += 1
        body = {"model": model, "messages": messages}
        if tool_names:
            body["tools"] = tool_schema(tool_names)
        body.update(p.get("extra_body") or {})
        reply = chat(p, key, body)

        u = reply.get("usage") or {}
        cached = ((u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
        usage["input_tokens"] += max((u.get("prompt_tokens") or 0) - cached, 0)
        usage["cache_read_input_tokens"] += cached
        usage["output_tokens"] += u.get("completion_tokens") or 0
        if isinstance(u.get("cost"), (int, float)):
            cost = (cost or 0) + u["cost"]

        choices = reply.get("choices") or []
        if not choices:
            raise ApiError("пустой ответ провайдера: %s" % json.dumps(reply)[:300])
        msg = choices[0].get("message") or {}
        calls = msg.get("tool_calls") or []
        if not calls:
            if choices[0].get("finish_reason") == "length":
                raise ApiError("ответ обрезан по длине: у модели не хватило лимита вывода "
                               "(подними его через extra_body в .llm.json)")
            return (msg.get("content") or "").strip(), cost, turns

        # Сообщение ассистента возвращается провайдеру как есть: в нём могут
        # быть поля, без которых он не свяжет ответы инструментов с вызовами.
        keep = dict(msg)
        keep["role"] = "assistant"
        keep.setdefault("content", "")
        if keep["content"] is None:
            keep["content"] = ""
        messages.append(keep)
        for call in calls:
            fn = call.get("function") or {}
            result = run_tool(fn.get("name", ""), fn.get("arguments"), tool_names)
            messages.append({"role": "tool", "tool_call_id": call.get("id", ""),
                             "content": result})
    raise ApiError("агент не закончил за %d шагов" % MAX_TURNS)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Агент для OpenAI-совместимых API")
    ap.add_argument("-p", "--print", dest="prompt", required=True)
    ap.add_argument("--agent")
    ap.add_argument("--model", required=True)
    ap.add_argument("--permission-mode", default="bypassPermissions")   # совместимость
    ap.add_argument("--output-format", default="json")
    args = ap.parse_args()

    started = time.time()
    envelope = {"type": "result", "is_error": False, "num_turns": 0, "result": "",
                "usage": {}, "total_cost_usd": None, "model": args.model}
    # Счётчик живёт снаружи run(): при сбое посреди работы уже потраченные
    # токены остаются в конверте, и драйвер не примет сбой за обрыв сети.
    usage = {"input_tokens": 0, "output_tokens": 0,
             "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    envelope["usage"] = usage
    try:
        p = llm.provider()
        if p["kind"] != "openai":
            raise FatalApiError("профиль «%s» работает через claude CLI, а не через "
                                "llm_agent.py" % p["name"])
        text, cost, turns = run(args.prompt, args.agent, args.model, p, usage)
        envelope.update(result=text, total_cost_usd=cost, num_turns=turns)
    except ApiError as exc:
        envelope.update(is_error=True, subtype="error", result=str(exc),
                        fatal=isinstance(exc, FatalApiError))
    envelope["duration_ms"] = int((time.time() - started) * 1000)

    if args.output_format == "json":
        print(json.dumps(envelope, ensure_ascii=False))
    else:
        print(envelope["result"])
    raise SystemExit(1 if envelope["is_error"] else 0)


if __name__ == "__main__":
    main()
