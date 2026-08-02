#!/usr/bin/env python3
"""Пульт конвейера глав — веб-обёртка над консольным диспетчером.

Механика та же, что у `python main.py` -> launcher_console: задачи стартуют в
отдельных окнах PowerShell через launcher_worker.ps1 и учитываются в
.launcher_processes.json, настройки живут в .launcher_settings.json. Поэтому
пульт и консольный диспетчер видят одно и то же и не мешают друг другу.

    python config_ui.py     # поднять пульт, вкладка браузера откроется сама
"""

import io
import json
import os
import subprocess
import sys
import time
from contextlib import redirect_stdout

# Автозапуск: `python config_ui.py` сам поднимает streamlit и открывает браузер.
# Без ScriptRunContext streamlit-вызовы уходят в пустоту — процесс «работает»,
# а интерфейса нет. Поэтому: headless=false явно (иначе браузер не откроется),
# а защита от рекурсии — через переменную окружения, не через молчаливый except.
if __name__ == "__main__" and os.environ.get("SIMPHONY_CONFIG_UI_CHILD") != "1":
    _ctx = None
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        _ctx = get_script_run_ctx()
    except Exception:
        pass  # не под streamlit — значит, перезапускаемся сами
    if _ctx is None:
        cmd = [
            sys.executable, "-m", "streamlit", "run", os.path.abspath(__file__),
            "--browser.gatherUsageStats", "false",
            "--server.headless", "false",
        ] + sys.argv[1:]
        print("Поднимаю пульт конвейера: сейчас откроется вкладка браузера "
              "(если нет — http://localhost:8501). Остановить: Ctrl+C или закрыть это окно.")
        env = dict(os.environ, SIMPHONY_CONFIG_UI_CHILD="1")
        raise SystemExit(subprocess.run(cmd, env=env).returncode)

import streamlit as st

import autorun
import launcher_console as lc
from common import ROOT, load_json, save_json

TOKEN_FILE = os.path.join(ROOT, ".ranobelib_token")
CHAPTERS_DIR = os.path.join(ROOT, "chapters")
RU_DIR = os.path.join(ROOT, "ru")
# Прежняя (шлюзовая) секция profiles при первом сохранении уезжает сюда,
# а не стирается молча: путь через сторонние API забракован, но не забыт.
GATEWAY_BAK = lc.SETTINGS_FILE + ".gateway.bak"
CONSOLE_KEYS = ("models", "active_model", "fixer_model", "defaults")

st.set_page_config(page_title="Пульт конвейера — Симфония", page_icon="📖", layout="wide")


def manager_call(fn, *args):
    """Метод диспетчера пишет в консоль; здесь его сообщение уходит в интерфейс."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        fn(*args)
    return buf.getvalue().strip()


def save_settings(settings):
    """Сохранение с одноразовым выносом шлюзовой секции в .gateway.bak.

    Не через lc.save_settings: тот дополняет файл, а не заменяет, и вернул бы
    вынесенную секцию обратно. Здесь она сначала целиком уходит в копию, и
    только потом пишется чистая схема диспетчера.
    """
    raw = load_json(lc.SETTINGS_FILE, {}) or {}
    foreign = {k: v for k, v in raw.items() if k not in CONSOLE_KEYS}
    if foreign and not os.path.exists(GATEWAY_BAK):
        with open(GATEWAY_BAK, "w", encoding="utf-8") as f:
            json.dump(raw, f, indent=2, ensure_ascii=False)
    save_json(lc.SETTINGS_FILE, {k: settings[k] for k in CONSOLE_KEYS})


def flash(message):
    """Показать сообщение ПОСЛЕ перерисовки — иначе список задач отстаёт на шаг."""
    st.session_state["flash"] = message
    st.rerun()


def tail(path, lines=25):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-lines:]).strip() or "(пусто)"
    except OSError:
        return "(файла ещё нет)"


settings = lc.load_settings()
model_names = [m["name"] for m in settings["models"]]
manager = lc.TaskManager({})

st.title("📖 Конвейер перевода — пульт")
if "flash" in st.session_state:
    st.info(st.session_state.pop("flash"))

tab_run, tab_models, tab_token = st.tabs(
    ["Конвейер", "Модели и параметры", "Токен RanobeLIB"])

# ---------------------------------------------------------------- Конвейер
with tab_run:
    total = len([f for f in os.listdir(CHAPTERS_DIR) if f.startswith("chapter_")])
    done = len([f for f in os.listdir(RU_DIR) if f.startswith("chapter_")])
    todo = autorun.pending_chapters()
    aside = sorted(set(autorun.load_state().get("set_aside", [])))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Глав в источнике", total)
    c2.metric("Переведено", done)
    c3.metric("Осталось", len(todo))
    c4.metric("Не дались ранее", len(aside))
    if aside:
        st.warning("Отложенные главы (нужен человек): %s"
                   % ", ".join(str(i) for i in aside))

    usage = autorun.usage_summary()
    if usage:
        # Доллар экранирован: в markdown-подписях $...$ превращается в формулу.
        st.caption(
            "Замерено на %d главах: ≈%.1fk токенов и \\$%.4f на главу. "
            "Прогноз на остаток (%d глав): %.1fM токенов, ≈\\$%.0f."
            % (usage["chapters"], usage["tokens_per_chapter"] / 1000,
               usage["usd_per_chapter"], len(todo),
               usage["tokens_per_chapter"] * len(todo) / 1e6,
               usage["usd_per_chapter"] * len(todo)))

    st.subheader("Запущенные задачи")
    if not manager.tasks:
        st.caption("Ничего не запущено. Задачи открываются в отдельных окнах "
                   "PowerShell и переживают закрытие пульта.")
    for name, rec in list(manager.tasks.items()):
        col_label, col_stop = st.columns([5, 1])
        col_label.write("**%s** — PID %s, запущена %s"
                        % (lc.TASKS[name]["label"], rec.get("pid"),
                           rec.get("started", "?")))
        if col_stop.button("Остановить", key="stop_" + name):
            flash(manager_call(manager.stop, name) or "Остановлена.")

    st.subheader("Запустить")
    col_left, col_right = st.columns(2)

    with col_left:
        with st.form("form_autorun"):
            st.markdown("**Автоперевод** — `autorun.py`, агент translator")
            count = st.number_input(
                "Сколько глав за прогон", min_value=1, max_value=len(todo) or 1,
                value=min(settings["defaults"]["autorun_count"], len(todo) or 1))
            prewave = st.checkbox(
                "Судить новые термины перед переводом", value=True,
                help="Выключать осознанно. Прогон 119–500 прошёл без суда — "
                     "13 терминов разъехались, у народа 亞特族群 стало 15 имён.")
            if st.form_submit_button("▶ Перевод"):
                settings["defaults"]["autorun_count"] = int(count)
                save_settings(settings)
                args = ["--count", str(int(count)),
                        "--model", settings["active_model"],
                        "--fixer-model", settings["fixer_model"]]
                if not prewave:
                    args.append("--no-prewave")
                flash(manager_call(manager.launch, "autorun", "autorun.py", args))

        with st.form("form_passive"):
            st.markdown("**Фоновая публикация** — `passive-update.py`")
            minutes = st.number_input(
                "Интервал, минут", min_value=1,
                value=int(settings["defaults"]["passive_minutes"]))
            if st.form_submit_button("▶ Публикация по кругу"):
                settings["defaults"]["passive_minutes"] = int(minutes)
                save_settings(settings)
                flash(manager_call(manager.launch, "passive_upload",
                                   "passive-update.py",
                                   ["--every", str(int(minutes))]))

    with col_right:
        with st.form("form_publish"):
            st.markdown("**Разовая публикация** — `publish_ranobelib.py --publish`")
            sure_pub = st.checkbox("Да, реально отправить все готовые главы на сайт")
            if st.form_submit_button("▶ Опубликовать") :
                if sure_pub:
                    flash(manager_call(manager.launch, "publish_once",
                                       "publish_ranobelib.py", ["--publish"]))
                else:
                    st.error("Поставь галочку подтверждения.")

        with st.form("form_pipeline"):
            st.markdown("**Полный конвейер** — `main.py all`: finalize → merge → "
                        "verify → publish → sync → epub")
            sure_all = st.checkbox("Да, прогнать все шаги по порядку")
            if st.form_submit_button("▶ Конвейер целиком"):
                if sure_all:
                    flash(manager_call(manager.launch, "pipeline", "main.py", ["all"]))
                else:
                    st.error("Поставь галочку подтверждения.")

    with st.expander("Журналы (хвосты)"):
        log_autorun, log_publish = st.tabs(["autorun.log", "publish.log"])
        log_autorun.code(tail(autorun.LOG_FILE), language=None)
        log_publish.code(tail(os.path.join(ROOT, "publish.log")), language=None)

    st.caption("Данные на %s — обновляются любым действием или кнопкой."
               % time.strftime("%H:%M:%S"))
    st.button("Обновить")

# ------------------------------------------------------- Модели и параметры
with tab_models:
    st.caption("Модели — идентификаторы для claude CLI (sonnet, opus, haiku или "
               "полный id вида claude-sonnet-5). Отдельный API-ключ не нужен: "
               "CLI работает через вход `claude login`.")
    raw_now = load_json(lc.SETTINGS_FILE, {}) or {}
    if any(k not in CONSOLE_KEYS for k in raw_now):
        st.info("В файле настроек ещё лежит секция забракованного шлюза "
                "(profiles и т.п.). При первом сохранении она целиком уедет в "
                "`%s` — ничего не потеряется." % os.path.basename(GATEWAY_BAK))

    with st.form("form_models"):
        col_a, col_b = st.columns(2)
        active = col_a.selectbox(
            "Переводчик и судья терминов (active_model)", model_names,
            index=model_names.index(settings["active_model"]))
        fixer = col_b.selectbox(
            "Инженер застрявших глав (fixer_model)", model_names,
            index=model_names.index(settings["fixer_model"]))
        col_c, col_d = st.columns(2)
        default_count = col_c.number_input(
            "Глав за прогон по умолчанию", min_value=1,
            value=int(settings["defaults"]["autorun_count"]))
        default_minutes = col_d.number_input(
            "Интервал публикации по умолчанию, минут", min_value=1,
            value=int(settings["defaults"]["passive_minutes"]))
        if st.form_submit_button("Сохранить"):
            settings.update(active_model=active, fixer_model=fixer)
            settings["defaults"]["autorun_count"] = int(default_count)
            settings["defaults"]["passive_minutes"] = int(default_minutes)
            save_settings(settings)
            flash("Настройки сохранены.")

    st.table([{"модель": m["name"], "описание": m.get("description", "")}
              for m in settings["models"]])

    col_add, col_del = st.columns(2)
    with col_add.form("form_add_model"):
        st.markdown("**Добавить модель**")
        new_name = st.text_input("ID для claude CLI (например, haiku)")
        new_desc = st.text_input("Описание", "пользовательская модель")
        if st.form_submit_button("Добавить"):
            name = new_name.strip()
            if not name or name in model_names:
                st.error("Название пустое или уже используется.")
            else:
                settings["models"].append(
                    {"name": name, "key_env": "ANTHROPIC_API_KEY",
                     "description": new_desc.strip()})
                save_settings(settings)
                flash("Модель «%s» добавлена." % name)

    removable = [n for n in model_names
                 if n not in (settings["active_model"], settings["fixer_model"])]
    with col_del.form("form_del_model"):
        st.markdown("**Убрать модель**")
        if removable:
            victim = st.selectbox("Какую", removable)
        else:
            st.caption("Убрать нечего: остальные заняты как переводчик или инженер.")
            victim = None
        if st.form_submit_button("Убрать") and victim:
            settings["models"] = [m for m in settings["models"]
                                  if m["name"] != victim]
            save_settings(settings)
            flash("Модель «%s» убрана." % victim)

# --------------------------------------------------------- Токен RanobeLIB
with tab_token:
    st.caption("Токен нужен только шагам publish и sync (выкладка на сайт). "
               "Без него они честно пропускаются, перевод и сборка EPUB работают. "
               "Файл `.ranobelib_token` в .gitignore и в git не попадает.")
    if os.path.exists(TOKEN_FILE):
        stat = os.stat(TOKEN_FILE)
        st.success("Токен на месте: %d байт, обновлён %s." %
                   (stat.st_size,
                    time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime))))
    else:
        st.warning("Файла с токеном нет — выкладка будет пропускаться.")

    with st.form("form_token"):
        new_token = st.text_input("Новый токен (вставить целиком)", type="password")
        if st.form_submit_button("Сохранить токен"):
            if new_token.strip():
                with open(TOKEN_FILE, "w", encoding="utf-8") as f:
                    f.write(new_token.strip())
                flash("Токен сохранён в .ranobelib_token.")
            else:
                st.error("Пустой ввод — файл не тронут.")
