#!/usr/bin/env python3
"""Одна точка входа для всего, что делается после перевода.

`autorun.py` кладёт готовые главы в `ru/` и на этом останавливается. Дальше
нужны ещё пять шагов, каждый своим скриптом со своими флагами, и порядок между
ними не произвольный. Здесь они собраны в одну команду.

    python main.py              интерактивный диспетчер задач
    python main.py status       состояние: что готово, что ждёт
    python main.py all          весь конвейер по порядку
    python main.py epub         один шаг
    python main.py all --dry    показать, что было бы сделано, ничего не трогая

Порядок шагов — не вкусовщина, каждый следующий опирается на предыдущий:

  1. finalize  снять служебные маркеры [N] и починить разрывы внутри абзаца.
               До этого глава не проходит приёмку, а выкладка её не отправляет.
  2. merge     склеить абзацы, разрезанные источником посреди фразы. Обязан
               идти ДО выкладки: иначе главы уедут на сайт поломанными и их
               придётся обновлять вторым заходом.
  3. verify    структурная приёмка. Стоит перед отправкой, чтобы брак не уехал
               читателю, а не после, когда он уже там.
  4. publish   залить новые главы.
  5. sync      обновить те, чей текст изменился после заливки. Отдельный шаг
               от publish: тот отправляет только отсутствующие на сайте.
  6. epub      собрать книгу. Последним — чтобы вобрала все правки выше.

Шаги 4 и 5 требуют токена в `.ranobelib_token`. Без него они не валят прогон,
а честно сообщают и пропускаются: сборка книги от сайта не зависит.
"""

import argparse
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
LOCK = os.path.join(ROOT, ".publish_lock")
TOKEN_FILE = os.path.join(ROOT, ".ranobelib_token")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def run(script, *args, allow_fail=False):
    """(код возврата, вывод). Вывод и показывается, и возвращается — сводка в
    конце строится по нему, а смотреть на молчащий экран полчаса невесело."""
    cmd = [sys.executable, "-u", os.path.join(ROOT, script)] + [str(a) for a in args]
    print("    $ python %s %s" % (script, " ".join(str(a) for a in args)))
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace")
    lines = []
    for line in proc.stdout:
        lines.append(line.rstrip())
        print("    " + line.rstrip())
    proc.wait()
    if proc.returncode and not allow_fail:
        print("    [!] %s вышел с кодом %d" % (script, proc.returncode))
    return proc.returncode, "\n".join(lines)


def have_token():
    return os.path.exists(TOKEN_FILE) or os.environ.get("RANOBELIB_TOKEN", "").strip()


def publishing_busy():
    """Выкладка уже идёт в другом окне — второй прогон запускать нельзя."""
    return os.path.exists(LOCK)


# --------------------------------------------------------------------------
# Шаги
# --------------------------------------------------------------------------

def step_finalize(dry):
    return run("finalize.py", *(["--dry-run"] if dry else []), allow_fail=True)


def step_merge(dry):
    return run("tools/merge_broken_paragraphs.py",
               *([] if dry else ["--apply"]), allow_fail=True)


def step_verify(dry):
    return run("verify.py", allow_fail=True)


def step_publish(dry):
    if not have_token():
        print("    [пропуск] нет токена: положи строку в .ranobelib_token")
        return 0, ""
    if publishing_busy():
        print("    [пропуск] выкладка уже идёт (есть .publish_lock)")
        return 0, ""
    return run("publish_ranobelib.py", *([] if dry else ["--publish"]), allow_fail=True)


def step_sync(dry):
    if not have_token():
        print("    [пропуск] нет токена")
        return 0, ""
    if publishing_busy():
        print("    [пропуск] выкладка уже идёт")
        return 0, ""
    return run("publish_ranobelib.py", "--sync", *([] if dry else ["--publish"]),
               allow_fail=True)


def step_epub(dry):
    if dry:
        print("    [пробный прогон] книга не собирается")
        return 0, ""
    return run("build_epub.py", allow_fail=True)


STEPS = [
    ("finalize", "снять маркеры [N], починить разрывы", step_finalize),
    ("merge", "склеить абзацы, разрезанные источником", step_merge),
    ("verify", "структурная приёмка", step_verify),
    ("publish", "залить новые главы на RanobeLIB", step_publish),
    ("sync", "обновить на сайте изменившиеся главы", step_sync),
    ("epub", "собрать книгу", step_epub),
]
STEP_MAP = {name: fn for name, _, fn in STEPS}


# --------------------------------------------------------------------------

def run_steps(steps, dry):
    """Run ordered pipeline steps and stop before a failed prerequisite.

    Publishing after a failed validation is worse than stopping the whole
    pipeline: it turns a local repair into a reader-visible mistake.  The
    individual commands still print their diagnostics; this function only
    makes their exit status control the next step.
    """
    for n, (name, desc, fn) in enumerate(steps, 1):
        print("")
        print("=" * 66)
        print("[%d/%d] %s — %s%s" % (n, len(steps), name, desc,
                                     " (пробный прогон)" if dry else ""))
        print("=" * 66)
        rc, _ = fn(dry)
        if rc:
            print("    [!] конвейер остановлен: шаг %s завершился с кодом %d"
                  % (name, rc))
            return rc
    return 0


# --------------------------------------------------------------------------

def status():
    """Короткая сводка без единой правки на диске."""
    print("=== СОСТОЯНИЕ ===")
    print("")
    rc, out = run("autorun.py", "--status", allow_fail=True)
    print("")
    rc, out = run("publish_ranobelib.py", "--status", allow_fail=True)
    print("")
    print("Токен RanobeLIB   : %s" % ("есть" if have_token() else "НЕТ, выкладка недоступна"))
    print("Выкладка сейчас   : %s" % ("идёт" if publishing_busy() else "не запущена"))
    books = sorted((f for f in os.listdir(ROOT) if f.endswith(".epub")),
                   key=lambda f: os.path.getmtime(os.path.join(ROOT, f)))
    if books:
        newest = books[-1]
        age = (time.time() - os.path.getmtime(os.path.join(ROOT, newest))) / 3600
        print("Последняя книга   : %s (%.1f ч назад)" % (newest, age))
    print("")
    print("Дальше: python main.py all")


def main():
    ap = argparse.ArgumentParser(
        description="Всё, что делается после autorun.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Шаги:\n" + "\n".join("  %-9s %s" % (n, d) for n, d, _ in STEPS))
    ap.add_argument("step", nargs="?", default="console",
                    choices=["console", "status", "all"] + [n for n, _, _ in STEPS])
    ap.add_argument("--dry", action="store_true",
                    help="показать, что было бы сделано, ничего не меняя")
    args = ap.parse_args()

    if args.step == "console":
        from launcher_console import main as launcher_main
        launcher_main()
        return

    if args.step == "status":
        status()
        return

    steps = STEPS if args.step == "all" else [
        (n, d, f) for n, d, f in STEPS if n == args.step]

    started = time.time()
    rc = run_steps(steps, args.dry)
    if rc:
        raise SystemExit(rc)

    print("")
    print("=" * 66)
    print("Готово за %.1f мин." % ((time.time() - started) / 60))
    if args.dry:
        print("Это был пробный прогон — на диске и на сайте ничего не менялось.")


if __name__ == "__main__":
    main()
