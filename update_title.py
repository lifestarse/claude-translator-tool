#!/usr/bin/env python3
"""Правка описания тайтла на RanobeLIB из `title_summary.md`.

Текст держится отдельным файлом, а не в коде: описание переписывается после
каждого отказа модерации, и править markdown проще, чем строковый литерал.
Абзацы разделяются пустой строкой, как везде в проекте.

Две вещи, найденные перебором и стоившие десятка запросов.

**Вложенный документ приходится разворачивать в скобочную запись.** Описание
хранится как ProseMirror — тот же формат, что и тело главы. В обычной форме
`summary` схлопывается в строку, и валидатор отвечает «Имеется неизвестный блок
"null"», хотя с самим текстом всё в порядке.

**500 здесь не означает, что правка не прошла.** Сайт отвечает `Server Error`
уже ПОСЛЕ сохранения — падает, видимо, при сборке ответа. Судить об успехе по
коду ответа нельзя: скрипт перечитывает описание с сайта и сравнивает с тем,
что отправлял. Это единственная надёжная проверка.

    python update_title.py            # показать, что отправится
    python update_title.py --apply    # отправить и сверить
"""

import argparse
import os
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

from common import read_text, split_paragraphs
from publish_ranobelib import API, SITE_ID, SLUG, MANGA_ID, token_or_die

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SUMMARY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "title_summary.md")
# Возрастное ограничение сайт требует при каждой правке: без него валидатор
# отвечает «обязательно для заполнения, когда "Модерация" не указано».
CAUTION = 4  # 18+, как стоит в карточке


def to_doc(paragraphs):
    return {"type": "doc", "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": p}]}
        for p in paragraphs
    ]}


def flatten(obj, prefix=""):
    """Вложенный документ -> пары ключ-значение в скобочной записи."""
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out += flatten(v, "%s[%s]" % (prefix, k) if prefix else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out += flatten(v, "%s[%d]" % (prefix, i))
    else:
        out.append((prefix, obj))
    return out


def put(fields, token):
    body = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request("%s/manga/%d" % (API, MANGA_ID), data=body, method="PUT")
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Site-Id", SITE_ID)
    req.add_header("Accept", "application/json")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read().decode("utf-8")[:300]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]


def remote_summary(token):
    from publish_ranobelib import api_request
    code, data = api_request(
        "GET", "%s/manga/%s?fields%%5B%%5D=summary&fields%%5B%%5D=moderated" % (API, SLUG), token)
    if code != 200:
        return None, None
    d = data.get("data", {})
    s = d.get("summary")
    if isinstance(s, dict):
        text = "\n\n".join(
            "".join(t.get("text", "") for t in (n.get("content") or []))
            for n in s.get("content", []))
    else:
        text = s or ""
    return text, d.get("moderated")


def main():
    ap = argparse.ArgumentParser(description="Обновить описание тайтла")
    ap.add_argument("--apply", action="store_true", help="отправить (без флага — показать)")
    ap.add_argument("--file", default=SUMMARY_FILE)
    args = ap.parse_args()

    if not os.path.exists(args.file):
        raise SystemExit("Нет файла описания %s — напиши описание тайтла "
                         "(абзацы через пустую строку) и запусти снова." % args.file)
    paras = split_paragraphs(read_text(args.file))
    print("Абзацев: %d, символов: %d" % (len(paras), sum(len(p) for p in paras)))
    print("")
    for p in paras:
        print("  " + p[:100] + ("…" if len(p) > 100 else ""))
    print("")

    if not args.apply:
        print("Показ без отправки. Отправить — тот же вызов с флагом --apply.")
        return

    token = token_or_die()
    code, body = put([("caution", CAUTION)] + flatten(to_doc(paras), "summary"), token)
    print("Ответ сайта: %s %s" % (code, body[:120]))

    # Код ответа ничего не доказывает — сверяемся с тем, что реально лежит.
    got, moderated = remote_summary(token)
    ok = got is not None and got.strip() == "\n\n".join(paras).strip()
    print("")
    print("На сайте лежит наш текст: %s" % ("да" if ok else "НЕТ"))
    print("Состояние модерации     : %s" % json.dumps(moderated, ensure_ascii=False))
    if not ok:
        raise SystemExit("Описание не обновилось — правь через форму на сайте.")


if __name__ == "__main__":
    main()
