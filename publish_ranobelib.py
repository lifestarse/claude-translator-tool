#!/usr/bin/env python3
"""Выкладка готовых глав на RanobeLIB.

Формат запроса снят с живой заливки, а не угадан: сайт шлёт
`POST https://api.cdnlibs.org/api/chapters` с телом

    {"volume": "1", "number": "2", "name": "", "branch_id": null,
     "content": {"type": "doc", "content": [ ...узлы paragraph... ]}}

то есть документ ProseMirror, где каждый абзац — отдельный узел. Наши файлы
в `ru/` уже разбиты на абзацы и сверены с оригиналом по их числу, поэтому
преобразование прямое.

Номера томов и глав не выдумываются. В заголовках оригинала счёт `第N章`
перезапускается с единицы на каждом томе — 第1章 стоит у файлов 1, 25 и 77, —
и это ровно те два поля, которые просит сайт. Файл 50 = том 2, глава 26;
файл 120 = том 3, глава 44. Всего томов 8.

Разметку скрипт не считает сам — она целиком в `author_numbers.py`, том же
модуле, по которому `split_by_author.py` раскладывает папку `ru_by_author/`.
Иначе папка и сайт разошлись бы на первой же опечатке источника, а их там
хватает: `第1把39章`, `第106` без 章, `178章` без 第, потерянная первая цифра.
Спорные случаи разбираются там же: одиночный сбитый номер чинится по соседям,
а настоящий повтор (файлы 474–493 дважды заявляют 216–225) разводится дробным
номером у второй по порядку чтения главы — пара Том/Глава обязана указывать
ровно на одну главу.

Токен в коде не хранится и не запрашивается интерактивно — он читается из
переменной окружения RANOBELIB_TOKEN, которую выставляешь ты.

    python publish_ranobelib.py --status
    python publish_ranobelib.py --range 1 10          # пробный прогон
    python publish_ranobelib.py --range 1 1 --publish  # реальная отправка
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid

import author_numbers
from common import (
    ROOT,
    chapter_path,
    load_json,
    marker_count,
    read_text,
    ru_path,
    save_json,
    split_paragraphs,
    text_fingerprint,
    validate_translation,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

API = "https://api.cdnlibs.org/api"
SLUG = "SET-ME--slug-тайтла-на-ranobelib"  # из URL страницы тайтла
# Числовой id тайтла — префикс SLUG до «--». Пока SLUG не заполнен, модуль
# обязан импортироваться (его тянут main.py, passive-update.py, update_title.py
# ради --help и --status), поэтому ошибка поднимается только при обращении к
# сайту — в token_or_die.
_SLUG_ID = SLUG.split("--")[0]
MANGA_ID = int(_SLUG_ID) if _SLUG_ID.isdigit() else None
SITE_ID = "3"
# Команда перевода: id из URL страницы команды на сайте.
TEAM_ID = 0  # id команды переводчиков на сайте
TOKEN_ENV = "RANOBELIB_TOKEN"

LEDGER = os.path.join(ROOT, "ranobelib_published.json")
LOCK = os.path.join(ROOT, ".publish_lock")

# {индекс файла: (первый номер, последний)} для склеек. Заполняется в
# load_volume_map, читается в build_payload — считать разметку заново на каждой
# главе значило бы перечитывать все 4885 оригиналов.
SPANS = {}
VOLUME_MAP = os.path.join(ROOT, "volume_map.json")
OVERRIDES = os.path.join(ROOT, "ranobelib_numbers.json")
TOKEN_FILE = os.path.join(ROOT, ".ranobelib_token")

# «Глава 44: Путь» и «…, глава 44: Путь» — приставку с номером сайт покажет сам
# рядом с названием, поэтому в поле name она не нужна.
# Диапазон «Главы 11-12:» обязан сниматься целиком. Пока дефис стоял в списке
# завершающих знаков, шаблон обрывался на нём и оставлял в названии «12:».
TITLE_PREFIX_RE = re.compile(r"^.*?глав[аы]\s*\d+(?:\s*[-–—]\s*\d+)?\s*[:.]\s*", re.I)


# --------------------------------------------------------------------------
# Карта томов
# --------------------------------------------------------------------------

def load_volume_map(rebuild=False):
    """{индекс файла: (том, номер-строкой)}, дубли внутри склеек, разбор спорного.

    Разметка целиком лежит в `author_numbers.py` — том и номер обязаны совпадать
    с тем, что раскладывает `split_by_author.py`. Две независимые реализации
    одной нумерации разъехались бы на первой же опечатке источника, а разъезд
    здесь означает главу, лежащую на сайте под чужим номером.

    Столкновений на выходе больше нет: повтор номера в источнике разрешается
    дробным номером у второй по порядку чтения главы, а не отказом её публиковать.
    Возвращаемый пустой словарь столкновений оставлен ради формы вызова.
    """
    data = author_numbers.build()
    mapping, covered = data["map"], data["covered"]
    SPANS.clear()
    SPANS.update(data.get("spans") or {})
    save_json(VOLUME_MAP, {
        "note": "Том и номер главы по авторской нумерации (см. author_numbers.py). "
                "Файл — снимок для чтения человеком; источник истины — сам модуль.",
        "map": {str(k): [v[0], v[1]] for k, v in mapping.items()},
        "covered": {str(k): v for k, v in covered.items()},
        "repairs": [{"index": i, "was": w, "now": n, "why": why}
                    for i, w, n, why in data["repairs"]],
        "duplicates": [{"index": i, "claimed": b, "assigned": l, "taken_by": o}
                       for i, b, l, o in data["duplicates"]],
        "unnumbered": [{"index": i, "why": w} for i, w in data["unnumbered"]],
    })
    return mapping, {}, covered


# --------------------------------------------------------------------------
# Тело запроса
# --------------------------------------------------------------------------

def to_doc(paragraphs):
    """Абзацы -> документ ProseMirror в том виде, в каком его шлёт редактор."""
    return {
        "type": "doc",
        "content": [
            {"type": "paragraph", "attrs": {},
             "content": [{"type": "text", "text": p}]}
            for p in paragraphs if p.strip()
        ],
    }


def build_payload(idx, vol, num):
    """(payload, беда) — беда не None, если главу отправлять нельзя."""
    if not os.path.exists(ru_path(idx)):
        return None, "перевода нет"
    ru_paras = split_paragraphs(read_text(ru_path(idx)))
    source = read_text(chapter_path(idx)).strip()
    src_paras = split_paragraphs(source)
    if len(ru_paras) != len(src_paras):
        return None, "абзацев %d против %d в оригинале" % (len(ru_paras), len(src_paras))
    if marker_count(ru_paras) > marker_count(src_paras):
        return None, "остались служебные маркеры — сначала finalize.py"
    problems = validate_translation(source, read_text(ru_path(idx)))
    if problems:
        return None, "не прошла приёмку: %s" % "; ".join(problems[:2])

    title = ru_paras[0].strip()
    name = TITLE_PREFIX_RE.sub("", title).strip() or title
    # Склейка: одна страница источника на две главы. Номер у неё может быть
    # только один, и без пометки в названии читатель видит, что после 11-й идёт
    # 13-я, и решает, что глава потеряна. Она не потеряна — она внутри.
    span = SPANS.get(idx)
    if span and span[0] != span[1]:
        name = "%s (главы %d–%d)" % (name, span[0], span[1])
    # Четыре поля ниже в захваченном теле запроса не значились, и без них сайт
    # отвечал 500 без объяснений. Найдены перебором по ответам валидатора, каждое
    # вскрылось только после того, как было добавлено предыдущее:
    #   manga_id     — к какому тайтлу глава; иначе 500;
    #   teams        — команда переводчиков, пустой список = публикация от себя;
    #   expired_type — ранний доступ: 0 «нет». Ровно целочисленный ноль: строка
    #                  "0" проходит как «платно» и требует поле price;
    #   attachments  — картинки главы, у текста пустой список.
    return {
        "manga_id": MANGA_ID,
        "volume": str(vol),
        "number": str(num),
        "name": name,
        "branch_id": None,
        "teams": [TEAM_ID],
        "expired_type": 0,
        "attachments": [],
        "content": to_doc(ru_paras[1:]),
    }, None


# --------------------------------------------------------------------------
# Отправка
# --------------------------------------------------------------------------

def extract_token(raw):
    """Строка из хранилища браузера -> сам токен.

    Принимает три вида, потому что в браузере лежит именно третий, а руками
    выкусывать из него нужное — лишний шаг, на котором теряется то половина
    строки, то её конец:

      * голый токен;
      * «Bearer <токен>» — так он выглядит в заголовке запроса;
      * весь блок из Local Storage, `{"token": {"access_token": ...}, "auth": ...}`.

    Значение никуда не печатается ни при успехе, ни при разборе.
    """
    raw = raw.strip()
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except ValueError:
            raise SystemExit("Файл с токеном начинается с «{», но это не JSON — "
                             "похоже, строка скопирована не целиком.")
        tok = ((data.get("token") or {}).get("access_token") or "").strip()
        if not tok:
            raise SystemExit("В файле JSON, но поля token.access_token в нём нет. "
                             "Нужен ключ со словом auth из Local Storage сайта.")
        return tok
    if raw[:7].lower() == "bearer ":
        return raw[7:].strip()
    return raw


def token_expiry(path=None):
    """Когда протухнет токен, если это видно из файла. Иначе None.

    Смысл в том, чтобы отказ на трёхсотой главе не выглядел загадкой: у токена
    ranobelib срок около месяца, а полная выкладка идёт больше часа.
    """
    for candidate in ([path] if path else []) + [TOKEN_FILE]:
        if candidate and os.path.exists(candidate):
            raw = read_text(candidate).strip()
            if not raw.startswith("{"):
                return None
            try:
                blob = (json.loads(raw).get("token") or {})
            except ValueError:
                return None
            ts, ttl = blob.get("timestamp"), blob.get("expires_in")
            if isinstance(ts, int) and isinstance(ttl, int):
                return ts / 1000.0 + ttl
    return None


def token_or_die(path=None):
    """Токен из файла или из переменной окружения. В коде его нет.

    Файл предпочтительнее переменной: строка, набранная в консоли, остаётся в
    истории PowerShell, а файл можно удалить одной командой. Ни то, ни другое
    никуда не выводится — при ошибках печатается только длина.
    """
    if MANGA_ID is None or not TEAM_ID:
        raise SystemExit(
            "Не настроен тайтл: впиши SLUG (вида 12345--name) и TEAM_ID "
            "в начале publish_ranobelib.py.")
    for candidate in ([path] if path else []) + [TOKEN_FILE]:
        if candidate and os.path.exists(candidate):
            tok = extract_token(read_text(candidate))
            if tok:
                return tok
    tok = os.environ.get(TOKEN_ENV, "").strip()
    if tok:
        return tok
    raise SystemExit(
        "Токен не найден. Дай его одним из двух способов — в скрипт он не пишется:\n"
        "  1) положи строку в файл %s\n"
        "  2) или выставь переменную:  $env:%s = \"<токен>\"\n"
        "Взять его можно в браузере: F12 -> Application -> Local Storage ->\n"
        "ranobelib.me, ключ со словом auth/token; либо в Network у любого запроса\n"
        "к api.cdnlibs.org — заголовок Authorization, часть после «Bearer ».\n"
        "Выкусывать нужное из строки не надо: и голый токен, и «Bearer …», и весь\n"
        "блок из Local Storage целиком скрипт разберёт сам."
        % (os.path.basename(TOKEN_FILE), TOKEN_ENV)
    )


def api_request(method, url, token, payload=None, timeout=60):
    """(код, разобранный ответ). Заголовки восстановлены по устройству API:
    при захвате XHR они не записались, поэтому первую отправку делай одной
    главой — если набор неверен, это выяснится на ней, а не на трёх сотнях.
    """
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Site-Id", SITE_ID)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(body or "{}")
        except ValueError:
            return e.code, {"raw": body[:400]}


def remote_index(token):
    """{(том, номер): id главы на сайте}. Нужен, чтобы обновлять, а не заливать."""
    code, data = api_request("GET", "%s/manga/%s/chapters" % (API, SLUG), token)
    if code != 200:
        return None, "сайт ответил %d: %s" % (code, str(data)[:200])
    out = {}
    for c in data.get("data", []) or []:
        out[(str(c.get("volume")), str(c.get("number")))] = c.get("id")
    return out, None


def remote_text(token, vol, num):
    """Текст главы с сайта абзацами, или None. Ради сверки со своим файлом."""
    code, data = api_request(
        "GET", "%s/manga/%s/chapter?number=%s&volume=%s" % (API, SLUG, num, vol), token)
    if code != 200:
        return None
    doc = ((data.get("data") or {}).get("content") or {})
    if not isinstance(doc, dict):
        return None
    return ["".join(t.get("text", "") for t in (n.get("content") or []))
            for n in doc.get("content", [])]


def payload_paragraphs(payload):
    """Paragraph strings carried by a create-chapter request."""
    doc = payload.get("content") or {}
    return ["".join(t.get("text", "") for t in (node.get("content") or []))
            for node in doc.get("content", [])]


def confirm_recovered_post(payload, token):
    """Check that a 422 really means this exact chapter was accepted earlier.

    An interrupted POST can have succeeded on the server while its reply was
    lost.  A duplicate response is evidence, not proof: the same status can
    also be produced by an unrelated API validation problem.  Re-read the
    requested volume/number and require the published body to match before
    recording the chapter as sent.
    """
    try:
        remote = remote_text(token, payload["volume"], payload["number"])
    except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
        return False, "не удалось подтвердить состояние сайта: %s" % exc
    if remote is None:
        return False, "сайт не подтвердил главу по тому же тому и номеру"
    expected = [p.strip() for p in payload_paragraphs(payload)]
    actual = [p.strip() for p in remote]
    if actual != expected:
        return False, "глава с тем же томом и номером содержит другой текст"
    return True, None


def update_chapter(chid, payload, token):
    """Правка уже лежащей главы. В отличие от карточки тайтла, отвечает честно."""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request("%s/chapters/%d" % (API, chid), data=data, method="PUT")
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Site-Id", SITE_ID)
    req.add_header("Accept", "application/json")
    req.add_header("Content-Type", "application/json")
    for attempt in (1, 2, 3):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.status in (200, 201), r.status
        except urllib.error.HTTPError as e:
            return False, "%s %s" % (e.code, e.read().decode("utf-8", "replace")[:150])
        except (urllib.error.URLError, OSError) as e:
            # сетевой чих (таймаут чтения, обрыв) не должен ронять весь прогон:
            # PUT идемпотентен, повторяем с паузой
            if attempt == 3:
                return False, "сеть: %s" % e
            time.sleep(10 * attempt)


def sync(args, mapping, ledger):
    """Догнать сайт до того, что лежит в ru/ — обновить устаревшие главы.

    Отпечаток текста, а не время правки: `write_text` подменяет файл целиком при
    любой операции, так что mtime меняется и когда не изменилось ни слова, а
    главы, восстановленные из карантина, наоборот сохраняют старый mtime. Хэш
    абзацев отвечает ровно на нужный вопрос — «тот ли это текст, что уехал».

    У глав, залитых до появления отпечатков, его в журнале нет. Для них текст
    забирается с сайта и сверяется напрямую: один лишний запрос на главу против
    молчаливого пропуска устаревшей главы.
    """
    token = token_or_die(args.token_file)
    index, err = remote_index(token)
    if err:
        raise SystemExit("Не удалось прочитать список глав с сайта — %s" % err)
    print("[+] На сайте: %d глав, в журнале: %d" % (len(index), len(ledger["published"])))

    todo = sorted(int(k) for k in ledger["published"] if int(k) in mapping)
    if args.range:
        lo, hi = args.range
        todo = [i for i in todo if lo <= i <= hi]

    checked = fetched = updated = failed = 0
    for idx in todo:
        vol, num = mapping[idx]
        rec = ledger["published"][str(idx)]
        local_fp = text_fingerprint(ru_path(idx))
        if local_fp is None:
            continue
        checked += 1
        if rec.get("fingerprint") == local_fp:
            continue

        stale = True
        if not rec.get("fingerprint"):
            # Отпечатка нет — узнаём правду у сайта, а не гадаем.
            site_paras = remote_text(token, vol, num)
            fetched += 1
            if site_paras is not None:
                ours = split_paragraphs(read_text(ru_path(idx)))[1:]
                stale = [p.strip() for p in site_paras] != [p.strip() for p in ours]
            if not stale:
                # A preview must be read-only.  Previously `--sync` without
                # `--publish` still filled this cache, so `main.py all --dry`
                # quietly changed the publication ledger.
                if args.publish:
                    rec["fingerprint"] = local_fp
                    save_json(LEDGER, ledger)
                continue

        chid = index.get((str(vol), num))
        if not chid:
            print("[!] гл.%04d -> том %s глава %s: на сайте не найдена" % (idx, vol, num))
            continue
        payload, why = build_payload(idx, vol, num)
        if why:
            print("[!] гл.%04d: %s" % (idx, why))
            continue
        if not args.publish:
            print("[ ] гл.%04d -> том %s глава %s: устарела, будет обновлена" % (idx, vol, num))
            updated += 1
            continue
        ok, answer = update_chapter(chid, payload, token)
        if not ok:
            print("[x] гл.%04d: отказ %s" % (idx, answer))
            failed += 1
            continue
        rec["fingerprint"] = local_fp
        save_json(LEDGER, ledger)
        updated += 1
        print("[+] гл.%04d -> том %s глава %s обновлена" % (idx, vol, num))
        time.sleep(args.delay)

    print("")
    print("=== СВЕРКА ===")
    print("Проверено         : %d" % checked)
    print("Запрошено с сайта : %d (главы без отпечатка в журнале)" % fetched)
    print("Обновлено         : %d%s" % (updated, "" if args.publish else " (пробный прогон)"))
    print("Отказов           : %d" % failed)


def remote_chapters(token):
    """Что уже лежит на сайте: множество пар (том, номер).

    Журнал на диске врёт при любой заливке мимо скрипта — например, главы 1 и 2
    ты залил руками. Источник истины про «уже есть» только сам сайт.
    """
    code, data = api_request("GET", "%s/manga/%s/chapters" % (API, SLUG), token)
    if code != 200:
        return None, "сайт ответил %d: %s" % (code, str(data)[:200])
    out = set()
    for c in data.get("data", []) or []:
        try:
            out.add((str(c.get("volume")), str(c.get("number"))))
        except (TypeError, ValueError):
            continue
    return out, None


def post_chapter(payload, token, retries=5):
    """Отправка одной главы. 429 и обрывы связи пережидаются, глава не теряется."""
    wait = 10
    for attempt in range(1, retries + 1):
        try:
            code, data = api_request("POST", "%s/chapters" % API, token, payload)
        except (urllib.error.URLError, OSError) as e:
            print("    связь оборвалась (%s), жду %d с" % (e, wait))
            time.sleep(wait)
            wait = min(wait * 2, 300)
            continue
        if code in (200, 201):
            return True, data
        if code == 429:
            print("    сайт просит притормозить (429), жду %d с" % wait)
            time.sleep(wait)
            wait = min(wait * 2, 600)
            continue
        # «Номер тома уже существует» после обрыва связи МОЖЕТ означать, что
        # первая попытка дошла, а ответ потерялся.  Но это также может быть
        # несвязанной ошибкой в данных, поэтому перед записью в журнал сверяем
        # с сайтом том, номер и сам текст.
        if code == 422 and "уже существует" in json.dumps(data, ensure_ascii=False):
            confirmed, why = confirm_recovered_post(payload, token)
            if confirmed:
                return True, {"note": "глава уже на сайте, ответ первой попытки потерялся"}
            return False, {"code": code, "answer": data, "recovery": why}
        return False, {"code": code, "answer": data}
    return False, {"code": "нет ответа", "answer": "исчерпаны попытки"}


# --------------------------------------------------------------------------

def take_lock():
    """Не дать двум выкладкам работать разом. Возвращает функцию снятия замка.

    Не паранойя, а разбор случившегося: второй запущенный прогон убил первый.
    На Windows `os.replace` не может подменить файл, открытый другим процессом,
    поэтому запись журнала падала с WinError 32 — и прогон умирал молча, посреди
    успешной заливки. Заодно два экземпляра шлют одну главу дважды.
    """
    owner = "%d:%s" % (os.getpid(), uuid.uuid4().hex)
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            pid = read_text(LOCK).strip()
        except OSError:
            pid = "неизвестен"
        raise SystemExit(
            "Выкладка уже идёт (процесс %s). Двум прогонам разом нельзя: они\n"
            "дерутся за журнал и шлют главы по второму разу. Если прогон умер\n"
            "по-настоящему — удали %s." % (pid, os.path.basename(LOCK)))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(owner)
    except BaseException:
        try:
            os.remove(LOCK)
        except OSError:
            pass
        raise

    def release():
        try:
            # Do not remove a lock created by a later publisher after someone
            # manually cleared a stale file while this process was exiting.
            if read_text(LOCK).strip() == owner:
                os.remove(LOCK)
        except OSError:
            pass
    return release


def main():
    ap = argparse.ArgumentParser(description="Выкладка глав на RanobeLIB")
    ap.add_argument("--range", type=int, nargs=2, metavar=("LO", "HI"))
    ap.add_argument("--limit", type=int, help="сколько глав максимум за прогон")
    ap.add_argument("--publish", action="store_true",
                    help="реально отправить (без флага — только показать)")
    ap.add_argument("--delay", type=float, default=6.0, help="пауза между главами, с")
    ap.add_argument("--rebuild-map", action="store_true", help="пересобрать карту томов")
    ap.add_argument("--token-file", help="файл с токеном (по умолчанию .ranobelib_token)")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--sync", action="store_true",
                    help="обновить главы, чей текст на сайте устарел")
    args = ap.parse_args()

    release = take_lock() if args.publish else (lambda: None)
    try:
        run(args)
    finally:
        release()


def run(args):
    mapping, collisions, covered = load_volume_map(rebuild=args.rebuild_map)
    ledger = load_json(LEDGER, {"published": {}}) or {"published": {}}

    ready = sorted(
        i for i in mapping
        if os.path.exists(ru_path(i))
    )
    if args.range:
        lo, hi = args.range
        ready = [i for i in ready if lo <= i <= hi]
    if args.limit:
        ready = ready[:args.limit]

    if args.sync:
        sync(args, mapping, ledger)
        return

    if args.status:
        print("Переведено и размечено : %d" % len(ready))
        print("Отмечено отправленными : %d" % len(ledger["published"]))
        print("Дублей внутри склеек   : %d" % len(covered))
        for i, owner in list(covered.items())[:10]:
            print("    гл.%04d уже входит в склейку гл.%04d" % (i, owner))
        return

    print("К отправке: %d глав%s" % (len(ready), "" if args.publish else " (пробный прогон)"))
    print("")

    token = token_or_die(args.token_file) if args.publish else None
    already = set()
    if args.publish:
        exp = token_expiry(args.token_file)
        if exp:
            left = exp - time.time()
            print("[+] Токен действителен ещё %.1f сут (до %s)"
                  % (left / 86400.0, time.strftime("%d.%m.%Y %H:%M", time.localtime(exp))))
            if left < 3600:
                raise SystemExit("До конца срока меньше часа — полная выкладка идёт дольше. "
                                 "Обнови строку в .ranobelib_token.")
        already, err = remote_chapters(token)
        if err:
            raise SystemExit("Не удалось прочитать список глав с сайта — %s\n"
                             "Отправку не начинаю: без него нельзя понять, что уже залито." % err)
        print("[+] На сайте уже: %d глав" % len(already))
        print("")

    sent = skipped = failed = 0
    for idx in ready:
        vol, num = mapping[idx]
        tag = "гл.%04d -> том %d, глава %s" % (idx, vol, num)

        if idx in covered:
            print("[!] %s: текст уже входит в склейку гл.%04d, пропуск"
                  % (tag, covered[idx]))
            skipped += 1
            continue
        if str(idx) in ledger["published"]:
            skipped += 1
            continue
        if args.publish and (str(vol), str(num)) in already:
            print("[=] %s: уже на сайте" % tag)
            ledger["published"][str(idx)] = {"volume": vol, "number": num, "note": "было до скрипта"}
            save_json(LEDGER, ledger)
            skipped += 1
            continue

        payload, why = build_payload(idx, vol, num)
        if why:
            print("[!] %s: %s" % (tag, why))
            skipped += 1
            continue

        if not args.publish:
            print("[ ] %s | «%s» | абзацев %d"
                  % (tag, payload["name"][:50], len(payload["content"]["content"])))
            continue

        ok, answer = post_chapter(payload, token)
        if not ok:
            print("[x] %s: отказ %s" % (tag, str(answer)[:220]))
            failed += 1
            # Первый же отказ останавливает прогон. Если сайт не принимает главу
            # по причине, общей для всех — не тот заголовок, модерация, права, —
            # продолжать значит триста раз получить тот же ответ.
            break
        sent += 1
        # Отпечаток текста, который реально уехал. Без него `--sync` не может
        # отличить главу, правленную после заливки, от нетронутой, и вынужден
        # перечитывать сайт по главе за раз.
        ledger["published"][str(idx)] = {"volume": vol, "number": num,
                                         "name": payload["name"],
                                         "fingerprint": text_fingerprint(ru_path(idx))}
        save_json(LEDGER, ledger)
        print("[+] %s | «%s»" % (tag, payload["name"][:50]))
        time.sleep(args.delay)

    print("")
    print("=== ИТОГ ===")
    print("Отправлено : %d" % sent)
    print("Пропущено  : %d" % skipped)
    print("Отказов    : %d" % failed)
    if not args.publish:
        print("")
        print("Это пробный прогон, на сайт ничего не ушло.")
        print("Реальная отправка — тот же вызов с флагом --publish.")


if __name__ == "__main__":
    main()
