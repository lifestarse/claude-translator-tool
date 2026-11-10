#!/usr/bin/env python3
"""Провайдер модели: подписка Claude или API-ключ любого провайдера.

Все флоты агентов (autorun и *_driver.py) запускают агента через этот модуль.
Кто именно отвечает — решает активный профиль:

    python llm.py                         что сейчас включено
    python llm.py list                    готовые профили
    python llm.py use subscription        назад на подписку Claude (claude CLI)
    python llm.py use anthropic           API-ключ Anthropic вместо подписки
    python llm.py use openai --model ИМЯ  любой другой провайдер
    python llm.py key openai              ввести ключ (спросит скрыто, ляжет в .env)
    python llm.py test                    пробный вызов активного профиля

Два способа говорить с провайдером:

  cli     Запускается `claude` (Claude Code). Подписка, ключ Anthropic или
          провайдер с Anthropic-совместимым API (DeepSeek, Z.ai, Moonshot):
          для них claude получает адрес и ключ через переменные окружения.
          Все инструменты агента работают как обычно.

  openai  Провайдер с OpenAI-совместимым API (OpenAI, Gemini, OpenRouter, xAI,
          Groq, Mistral, Together, Ollama, любой свой шлюз). claude не нужен:
          агента ведёт встроенный цикл llm_agent.py с теми же инструментами
          (Read, Write, Edit, Bash, Grep, Glob) и теми же промптами
          .claude/agents/*.md.

Модели. Скрипты просят роль — `opus`, `sonnet`, `haiku`. У чужого провайдера
таких имён нет, поэтому роли отображаются на его модели:

    python llm.py use openrouter --model vendor/model            одна на все роли
    python llm.py use openai --sonnet БЫСТРАЯ --opus СИЛЬНАЯ      по ролям

Ключи в репозиторий не попадают: они читаются из переменной окружения профиля
или из файла `.env` (он в .gitignore). В `.llm.json` лежит только выбор.
"""

import argparse
import getpass
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(ROOT, ".llm.json")
ENV_FILE = os.path.join(ROOT, ".env")
AGENT_SCRIPT = os.path.join(ROOT, "llm_agent.py")
ROLES = ("opus", "sonnet", "haiku")

# kind=cli: через claude CLI; base_url пуст — родной API Anthropic.
# kind=openai: встроенный цикл по /chat/completions.
PRESETS = {
    "subscription": {"kind": "cli", "note": "подписка Claude — как настроен claude CLI"},
    "anthropic": {"kind": "cli", "key_env": "ANTHROPIC_API_KEY",
                  "note": "API-ключ Anthropic (оплата по токенам)"},
    "deepseek": {"kind": "cli", "base_url": "https://api.deepseek.com/anthropic",
                 "key_env": "DEEPSEEK_API_KEY", "note": "DeepSeek, Anthropic-совместимый API"},
    "zai": {"kind": "cli", "base_url": "https://api.z.ai/api/anthropic",
            "key_env": "ZAI_API_KEY", "note": "Z.ai (GLM), Anthropic-совместимый API"},
    "moonshot": {"kind": "cli", "base_url": "https://api.moonshot.ai/anthropic",
                 "key_env": "MOONSHOT_API_KEY", "note": "Moonshot (Kimi), Anthropic-совместимый API"},
    "openai": {"kind": "openai", "base_url": "https://api.openai.com/v1",
               "key_env": "OPENAI_API_KEY", "note": "OpenAI"},
    "gemini": {"kind": "openai",
               "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
               "key_env": "GEMINI_API_KEY", "note": "Google Gemini (OpenAI-совместимый вход)"},
    "openrouter": {"kind": "openai", "base_url": "https://openrouter.ai/api/v1",
                   "key_env": "OPENROUTER_API_KEY", "note": "OpenRouter — модели многих провайдеров"},
    "xai": {"kind": "openai", "base_url": "https://api.x.ai/v1",
            "key_env": "XAI_API_KEY", "note": "xAI (Grok)"},
    "groq": {"kind": "openai", "base_url": "https://api.groq.com/openai/v1",
             "key_env": "GROQ_API_KEY", "note": "Groq"},
    "mistral": {"kind": "openai", "base_url": "https://api.mistral.ai/v1",
                "key_env": "MISTRAL_API_KEY", "note": "Mistral"},
    "together": {"kind": "openai", "base_url": "https://api.together.xyz/v1",
                 "key_env": "TOGETHER_API_KEY", "note": "Together AI"},
    "ollama": {"kind": "openai", "base_url": "http://localhost:11434/v1",
               "key_env": "", "note": "локальный Ollama, ключ не нужен"},
}


# --------------------------------------------------------------------------
# Настройки и ключи
# --------------------------------------------------------------------------

def load_config():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}
    cfg.setdefault("active", "subscription")
    cfg.setdefault("providers", {})
    return cfg


def save_config(cfg):
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_FILE)


def read_env_file():
    out = {}
    try:
        with open(ENV_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip("'\"")
    except OSError:
        pass
    return out


def write_env_value(name, value):
    lines, done = [], False
    try:
        with open(ENV_FILE, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        pass
    for i, line in enumerate(lines):
        if line.split("=", 1)[0].strip() == name:
            lines[i] = "%s=%s" % (name, value)
            done = True
    if not done:
        lines.append("%s=%s" % (name, value))
    fd = os.open(ENV_FILE + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(ENV_FILE + ".tmp", ENV_FILE)


def provider(name=None, cfg=None):
    """Действующие настройки профиля: пресет + то, что сохранил пользователь."""
    cfg = cfg or load_config()
    name = name or os.environ.get("TRANSLATOR_LLM") or cfg["active"]
    saved = cfg["providers"].get(name, {})
    if name not in PRESETS and not saved:
        raise SystemExit("Нет профиля провайдера «%s». Список: python llm.py list" % name)
    p = dict(PRESETS.get(name, {}))
    p.update(saved)
    p["name"] = name
    p.setdefault("kind", "openai")
    p.setdefault("models", {})
    return p


def api_key(p):
    """Ключ профиля: переменная окружения, иначе .env. Пусто — ключа нет."""
    name = p.get("key_env") or ""
    if not name:
        return ""
    return os.environ.get(name, "").strip() or read_env_file().get(name, "")


def resolve_model(p, model):
    """Роль (opus/sonnet/haiku) -> модель провайдера. Чужое имя — как есть."""
    models = p.get("models") or {}
    return models.get(model) or models.get("default") or model


def api_host(p=None):
    """Хост, по которому autorun проверяет, что сеть жива."""
    p = p or provider()
    url = p.get("base_url") or "https://api.anthropic.com"
    host = url.split("://", 1)[-1].split("/", 1)[0]
    return host.rsplit(":", 1)[0] if ":" in host else host


def api_port(p=None):
    p = p or provider()
    url = p.get("base_url") or "https://api.anthropic.com"
    host = url.split("://", 1)[-1].split("/", 1)[0]
    if ":" in host:
        return int(host.rsplit(":", 1)[1])
    return 80 if url.startswith("http://") else 443


# --------------------------------------------------------------------------
# Запуск агента
# --------------------------------------------------------------------------

def find_claude():
    for name in ("claude", "claude.cmd", "claude.exe"):
        path = shutil.which(name)
        if path:
            return path
    raise SystemExit(
        "Не найден исполняемый файл claude в PATH. Он нужен профилям через "
        "claude CLI (subscription, anthropic, deepseek, zai, moonshot). "
        "Профили OpenAI-совместимых провайдеров работают без него: python llm.py list")


def agent_cmd(prompt, agent=None, model="sonnet", p=None):
    """Команда запуска одного агента — та же форма вывода при любом провайдере:
    JSON-конверт с полями result, is_error, usage, total_cost_usd."""
    p = p or provider()
    if p["kind"] == "openai":
        head = [sys.executable, AGENT_SCRIPT]
        model = resolve_model(p, model)
    else:
        head = [find_claude()]
        # Anthropic-совместимому провайдеру роли передаются переменными
        # ANTHROPIC_DEFAULT_*_MODEL (см. agent_env), сам флаг остаётся ролью.
        if model not in ROLES:
            model = resolve_model(p, model)
    cmd = head + ["-p", prompt]
    if agent:
        cmd += ["--agent", agent]
    return cmd + ["--model", model, "--permission-mode", "bypassPermissions",
                  "--output-format", "json"]


def agent_env(p=None):
    """Окружение для процесса агента. Подписка — без изменений."""
    p = p or provider()
    env = os.environ.copy()
    env["TRANSLATOR_LLM"] = p["name"]
    if p["name"] == "subscription":
        return env
    key = api_key(p)
    if p["kind"] == "openai":
        if key:
            env[p.get("key_env") or "LLM_API_KEY"] = key
        return env
    if not p.get("base_url"):
        if key:
            env["ANTHROPIC_API_KEY"] = key
        return env
    # Anthropic-совместимый провайдер за claude CLI.
    env["ANTHROPIC_BASE_URL"] = p["base_url"]
    env["ANTHROPIC_AUTH_TOKEN"] = key
    env["ANTHROPIC_API_KEY"] = ""
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    models = p.get("models") or {}
    for role in ROLES:
        target = models.get(role) or models.get("default")
        if target:
            env["ANTHROPIC_DEFAULT_%s_MODEL" % role.upper()] = target
    if models.get("sonnet") or models.get("default"):
        env["ANTHROPIC_MODEL"] = models.get("sonnet") or models["default"]
    return env


def run_agent(prompt, agent=None, model="sonnet", timeout=900):
    """subprocess.run агента с окружением активного профиля."""
    p = provider()
    return subprocess.run(
        agent_cmd(prompt, agent, model, p), cwd=ROOT, env=agent_env(p),
        capture_output=True, timeout=timeout, encoding="utf-8", errors="replace")


def problems(p=None):
    """Что мешает профилю работать — список строк, пустой если всё на месте."""
    p = p or provider()
    out = []
    if p["name"] == "subscription":
        return out
    if p.get("key_env") and not api_key(p):
        out.append("нет ключа: python llm.py key %s  (или переменная %s)"
                   % (p["name"], p["key_env"]))
    needs_models = p["kind"] == "openai" or p.get("base_url")
    if needs_models:
        models = p.get("models") or {}
        missing = [r for r in ROLES if not (models.get(r) or models.get("default"))]
        if missing:
            out.append("не заданы модели для ролей %s: python llm.py use %s --model ИМЯ_МОДЕЛИ"
                       % (", ".join(missing), p["name"]))
    if p["kind"] == "openai" and not p.get("base_url"):
        out.append("не задан адрес API: python llm.py use %s --base-url URL" % p["name"])
    return out


# --------------------------------------------------------------------------
# Командная строка
# --------------------------------------------------------------------------

def show(p):
    kind = "claude CLI" if p["kind"] == "cli" else "встроенный агент (OpenAI-совместимый API)"
    print("Провайдер : %s — %s" % (p["name"], p.get("note", "свой профиль")))
    print("Способ    : %s" % kind)
    if p.get("base_url"):
        print("Адрес API : %s" % p["base_url"])
    if p.get("key_env"):
        print("Ключ      : %s — %s" % (p["key_env"], "есть" if api_key(p) else "НЕТ"))
    models = p.get("models") or {}
    if p["name"] == "subscription" or (p["kind"] == "cli" and not p.get("base_url")):
        print("Модели    : opus / sonnet / haiku — родные модели Claude")
    else:
        print("Модели    : " + ", ".join(
            "%s -> %s" % (r, models.get(r) or models.get("default") or "НЕ ЗАДАНА")
            for r in ROLES))
    if p["name"] == "subscription" and (os.environ.get("ANTHROPIC_API_KEY")
                                        or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        print("[!] В окружении задан ANTHROPIC_API_KEY/ANTHROPIC_AUTH_TOKEN: claude "
              "будет списывать с ключа, а не с подписки. Убери переменную.")
    for line in problems(p):
        print("[!] " + line)


def cmd_list():
    cfg = load_config()
    names = list(PRESETS) + [n for n in cfg["providers"] if n not in PRESETS]
    for n in names:
        p = provider(n, cfg)
        mark = "*" if n == cfg["active"] else " "
        how = "claude CLI" if p["kind"] == "cli" else "встроенный"
        print("%s %-13s %-11s %s" % (mark, n, how, p.get("note", "свой профиль")))
    print("\nСвой шлюз: python llm.py use ИМЯ --base-url URL --key-env ПЕРЕМЕННАЯ --model МОДЕЛЬ")


def cmd_use(args):
    cfg = load_config()
    name = args.name
    saved = cfg["providers"].setdefault(name, {})
    if name not in PRESETS and not saved and not args.base_url:
        raise SystemExit("«%s» — не готовый профиль. Для своего шлюза укажи --base-url "
                         "(и --protocol anthropic, если API Anthropic-совместимый)." % name)
    if args.base_url:
        saved["base_url"] = args.base_url.rstrip("/")
    if args.protocol:
        saved["kind"] = "cli" if args.protocol == "anthropic" else "openai"
    if args.key_env is not None:
        saved["key_env"] = args.key_env
    models = saved.setdefault("models", {})
    if args.model:
        models.clear()
        models["default"] = args.model
    for role in ROLES:
        if getattr(args, role):
            models[role] = getattr(args, role)
    if not saved.get("models"):
        saved.pop("models", None)
    if not saved:
        cfg["providers"].pop(name, None)
    cfg["active"] = name
    save_config(cfg)
    show(provider(name, cfg))


def cmd_key(name):
    p = provider(name)
    if not p.get("key_env"):
        raise SystemExit("Профилю «%s» ключ не нужен." % name)
    value = getpass.getpass("Ключ для %s (%s), ввод скрыт: " % (name, p["key_env"])).strip()
    if not value:
        raise SystemExit("Пусто — ничего не записано.")
    write_env_value(p["key_env"], value)
    print("Записано в .env (%s, длина %d). Файл в .gitignore." % (p["key_env"], len(value)))


def cmd_test(model):
    p = provider()
    show(p)
    if problems(p):
        raise SystemExit(1)
    print("\nПробный вызов (роль %s)..." % model)
    try:
        proc = run_agent("Ответь одним словом: готов", None, model, timeout=180)
    except subprocess.TimeoutExpired:
        raise SystemExit("Нет ответа за 180 с.")
    try:
        data = json.loads(proc.stdout or "")
    except ValueError:
        data = None
    if proc.returncode or data is None or data.get("is_error"):
        msg = (data or {}).get("result") or proc.stderr or proc.stdout or ""
        raise SystemExit("ОШИБКА: %s" % str(msg).strip()[-600:])
    u = data.get("usage") or {}
    print("Ответ : %s" % str(data.get("result") or "").strip()[:200])
    print("Токены: вход %s, выход %s" % (u.get("input_tokens", "?"), u.get("output_tokens", "?")))
    print("Профиль работает.")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Провайдер модели для флотов агентов",
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("status", help="что сейчас включено")
    sub.add_parser("list", help="готовые профили")
    use = sub.add_parser("use", help="переключить провайдера")
    use.add_argument("name")
    use.add_argument("--model", help="одна модель провайдера на все роли")
    for role in ROLES:
        use.add_argument("--" + role, help="модель провайдера для роли %s" % role)
    use.add_argument("--base-url", help="адрес API (для своего шлюза)")
    use.add_argument("--protocol", choices=("openai", "anthropic"),
                     help="какой API у шлюза (по умолчанию openai)")
    use.add_argument("--key-env", help="имя переменной с ключом")
    key = sub.add_parser("key", help="ввести и сохранить ключ в .env")
    key.add_argument("name", nargs="?")
    test = sub.add_parser("test", help="пробный вызов")
    test.add_argument("--model", default="sonnet", help="роль: opus / sonnet / haiku")
    args = ap.parse_args()

    if args.cmd == "list":
        cmd_list()
    elif args.cmd == "use":
        cmd_use(args)
    elif args.cmd == "key":
        cmd_key(args.name or load_config()["active"])
    elif args.cmd == "test":
        cmd_test(args.model)
    else:
        show(provider())


if __name__ == "__main__":
    main()
