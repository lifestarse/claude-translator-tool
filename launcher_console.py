#!/usr/bin/env python3
"""Interactive task launcher used by ``python main.py``.

Models are remembered in .launcher_settings.json. API keys and tokens are
session-only: they are never written to the project and are only inherited by
the PowerShell window started for the selected task.
"""

import getpass
import os
import subprocess
import sys
import time

from common import ROOT, load_json, save_json


SETTINGS_FILE = os.path.join(ROOT, ".launcher_settings.json")
PROCESSES_FILE = os.path.join(ROOT, ".launcher_processes.json")
WORKER_FILE = os.path.join(ROOT, "launcher_worker.ps1")

DEFAULT_MODELS = [
    {"name": "sonnet", "key_env": "ANTHROPIC_API_KEY", "description": "быстрый перевод"},
    {"name": "opus", "key_env": "ANTHROPIC_API_KEY", "description": "сложные исправления"},
]
TASKS = {
    "autorun": {"label": "Автоперевод", "group": "translator"},
    "passive_upload": {"label": "Фоновая публикация", "group": "publisher"},
    "publish_once": {"label": "Разовая публикация", "group": "publisher"},
    "pipeline": {"label": "Полный конвейер", "group": "publisher"},
}


def ask(prompt, default=""):
    value = input("%s%s: " % (prompt, " [%s]" % default if default != "" else "")).strip()
    return value or default


def ask_yes_no(prompt, default=False):
    value = input("%s [%s]: " % (prompt, "Y/n" if default else "y/N")).strip().lower()
    return default if not value else value in ("y", "yes", "д", "да")


def ask_positive_int(prompt, default):
    while True:
        try:
            value = int(ask(prompt, str(default)))
            if value > 0:
                return value
        except ValueError:
            pass
        print("Нужно положительное целое число.")


def load_settings():
    raw = load_json(SETTINGS_FILE, {}) or {}
    models = [m for m in raw.get("models", [])
              if isinstance(m, dict) and m.get("name") and m.get("key_env")]
    if not models:
        models = [dict(m) for m in DEFAULT_MODELS]
    names = {m["name"] for m in models}
    active = raw.get("active_model") if raw.get("active_model") in names else models[0]["name"]
    fixer = raw.get("fixer_model") if raw.get("fixer_model") in names else active
    defaults = {"autorun_count": 70, "passive_minutes": 15}
    defaults.update(raw.get("defaults") or {})
    return {"models": models, "active_model": active, "fixer_model": fixer,
            "defaults": defaults}


def save_settings(settings):
    # Only model names and plain preferences are saved; never credentials.
    #
    # Читаем-дополняем-пишем, а не заменяем: этот файл ДЕЛИТСЯ с config_ui.py и
    # model_profiles.py, которые держат в нём свою секцию "profiles" с девятью
    # настроенными провайдерами. Прежняя запись целиком (save_json(settings))
    # молча стирала всё, чего этот диспетчер не знает, — первое же сохранение
    # из меню уничтожало конфигурацию профилей без возможности восстановления.
    on_disk = load_json(SETTINGS_FILE, {}) or {}
    on_disk.update(settings)
    save_json(SETTINGS_FILE, on_disk)


def find_model(settings, name):
    return next(m for m in settings["models"] if m["name"] == name)


def pid_alive(pid):
    """Check a Windows PID without ever sending it a signal."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", "PID eq %d" % pid, "/FO", "CSV", "/NH"],
            capture_output=True, text=True, errors="replace",
        )
        return result.returncode == 0 and ('"%d"' % pid) in result.stdout
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, OSError):
        return False
    return True


class TaskManager:
    def __init__(self, session_env):
        self.session_env = session_env
        self.tasks = load_json(PROCESSES_FILE, {}) or {}
        self.refresh()

    def save(self):
        save_json(PROCESSES_FILE, self.tasks)

    def refresh(self):
        stale = [name for name, rec in self.tasks.items()
                 if not isinstance(rec, dict) or not pid_alive(rec.get("pid", 0))]
        for name in stale:
            self.tasks.pop(name, None)
        if stale:
            self.save()

    def launch(self, name, script, args):
        self.refresh()
        spec = TASKS[name]
        if name in self.tasks:
            print("«%s» уже запущена (PID %s)." % (spec["label"], self.tasks[name]["pid"]))
            return
        busy = [n for n, rec in self.tasks.items() if rec.get("group") == spec["group"]]
        if busy:
            print("Сначала останови: %s." % ", ".join(TASKS[n]["label"] for n in busy))
            return
        if not os.path.isfile(WORKER_FILE) or not os.path.isfile(os.path.join(ROOT, script)):
            print("Не найден файл запуска для «%s»." % spec["label"])
            return
        command = [
            "powershell.exe", "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", WORKER_FILE, "-Python", sys.executable,
            "-WorkingDirectory", ROOT, "-Script", os.path.join(ROOT, script),
            "-TaskLabel", spec["label"], "-ScriptArgs",
        ] + list(args)
        env = os.environ.copy()
        env.update(self.session_env)
        flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0) | \
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        try:
            proc = subprocess.Popen(command, cwd=ROOT, env=env, shell=False,
                                    creationflags=flags)
        except OSError as exc:
            print("Не удалось открыть окно PowerShell: %s" % exc)
            return
        self.tasks[name] = {"pid": proc.pid, "group": spec["group"], "script": script,
                            "started": time.strftime("%Y-%m-%d %H:%M:%S")}
        self.save()
        print("«%s» открыта в новом окне PowerShell (PID %d)." % (spec["label"], proc.pid))

    def show(self):
        self.refresh()
        print("\n=== Запущенные задачи ===")
        if not self.tasks:
            print("Нет.")
            return
        for name, rec in self.tasks.items():
            print("- %s — PID %s, с %s" %
                  (TASKS[name]["label"], rec["pid"], rec.get("started", "?")))

    def stop(self, name):
        self.refresh()
        rec = self.tasks.get(name)
        if not rec:
            print("Эта задача уже не запущена.")
            return
        result = subprocess.run(["taskkill", "/PID", str(rec["pid"]), "/T", "/F"],
                                capture_output=True, text=True, errors="replace")
        if result.returncode:
            print("Не удалось остановить: %s" % result.stderr.strip())
            return
        self.tasks.pop(name, None)
        self.save()
        print("«%s» остановлена." % TASKS[name]["label"])


def choose_model(settings, field, title):
    print("\n%s:" % title)
    for number, model in enumerate(settings["models"], 1):
        active = " *" if model["name"] == settings[field] else ""
        print("%d. %s — %s%s" % (number, model["name"], model.get("description", ""), active))
    while True:
        try:
            settings[field] = settings["models"][int(ask("Номер модели")) - 1]["name"]
            save_settings(settings)
            return
        except (ValueError, IndexError):
            print("Выбери номер из списка.")


def add_model(settings):
    name = ask("ID новой модели для Claude CLI")
    if not name or any(m["name"] == name for m in settings["models"]):
        print("Название пустое или уже используется.")
        return
    settings["models"].append({
        "name": name,
        "key_env": ask("Переменная API-ключа", "ANTHROPIC_API_KEY"),
        "description": ask("Описание", "пользовательская модель"),
    })
    save_settings(settings)
    print("Модель добавлена. Её ключ можно ввести в пункте 4.")


def set_secret(session_env, env_name, label):
    value = getpass.getpass("%s (ввод скрыт, Enter — отмена): " % label).strip()
    if value:
        session_env[env_name] = value
        print("%s задан только для текущего запуска диспетчера." % label)


def credentials(settings, session_env):
    model = find_model(settings, settings["active_model"])
    print("\n1. API-ключ активной модели (%s)" % model["key_env"])
    print("2. Токен RanobeLIB")
    print("3. Другая переменная окружения")
    choice = ask("Выбор", "1")
    if choice == "1":
        set_secret(session_env, model["key_env"], "API-ключ %s" % model["name"])
    elif choice == "2":
        set_secret(session_env, "RANOBELIB_TOKEN", "Токен RanobeLIB")
    elif choice == "3":
        env_name = ask("Имя переменной")
        if env_name:
            set_secret(session_env, env_name, env_name)


def launch_task(settings, manager):
    print("\n1. Автоперевод")
    print("2. Фоновая публикация")
    print("3. Разовая публикация готовых глав")
    print("4. Полный конвейер после перевода")
    choice = ask("Что запустить")
    if choice == "1":
        count = ask_positive_int("Сколько глав", settings["defaults"]["autorun_count"])
        settings["defaults"]["autorun_count"] = count
        save_settings(settings)
        args = ["--count", str(count), "--model", settings["active_model"],
                "--fixer-model", settings["fixer_model"]]
        if not ask_yes_no("Судить новые термины перед переводом", True):
            args.append("--no-prewave")
        manager.launch("autorun", "autorun.py", args)
    elif choice == "2":
        minutes = ask_positive_int("Интервал в минутах", settings["defaults"]["passive_minutes"])
        settings["defaults"]["passive_minutes"] = minutes
        save_settings(settings)
        manager.launch("passive_upload", "passive-update.py", ["--every", str(minutes)])
    elif choice == "3":
        if ask_yes_no("Реально опубликовать все готовые главы", False):
            manager.launch("publish_once", "publish_ranobelib.py", ["--publish"])
    elif choice == "4":
        if ask_yes_no("Запустить полный конвейер", False):
            manager.launch("pipeline", "main.py", ["all"])


def stop_task(manager):
    manager.show()
    choices = list(manager.tasks)
    if not choices:
        return
    for number, name in enumerate(choices, 1):
        print("%d. %s" % (number, TASKS[name]["label"]))
    while True:
        raw = ask("Номер для остановки (Enter — отмена)")
        if not raw:
            return
        try:
            manager.stop(choices[int(raw) - 1])
            return
        except (ValueError, IndexError):
            print("Выбери номер из списка.")


def main():
    settings, session_env = load_settings(), {}
    manager = TaskManager(session_env)
    print("=== Диспетчер конвейера перевода ===")
    active = find_model(settings, settings["active_model"])
    print("Ключи и токены не сохраняются. Активная модель: %s." % active["name"])
    if ask_yes_no("Ввести API-ключ сейчас", False):
        set_secret(session_env, active["key_env"], "API-ключ %s" % active["name"])
    if ask_yes_no("Ввести токен RanobeLIB сейчас", False):
        set_secret(session_env, "RANOBELIB_TOKEN", "Токен RanobeLIB")
    while True:
        active = find_model(settings, settings["active_model"])
        print("\nМодель: %s | инженер: %s | API-ключ: %s | токен: %s" % (
            active["name"], settings["fixer_model"],
            "введён" if active["key_env"] in session_env else "не задан",
            "введён" if "RANOBELIB_TOKEN" in session_env else "не задан"))
        print("1. Выбрать модель переводчика")
        print("2. Выбрать модель инженера")
        print("3. Добавить модель")
        print("4. Ключи и токены")
        print("5. Запустить задачу")
        print("6. Остановить задачу")
        print("7. Показать запущенные задачи")
        print("0. Выйти (задачи в отдельных окнах продолжат работу)")
        choice = ask("Выбор")
        if choice == "1":
            choose_model(settings, "active_model", "Модель переводчика")
        elif choice == "2":
            choose_model(settings, "fixer_model", "Модель инженера")
        elif choice == "3":
            add_model(settings)
        elif choice == "4":
            credentials(settings, session_env)
        elif choice == "5":
            launch_task(settings, manager)
        elif choice == "6":
            stop_task(manager)
        elif choice == "7":
            manager.show()
        elif choice == "0":
            return
        else:
            print("Нет такого пункта.")


if __name__ == "__main__":
    main()
