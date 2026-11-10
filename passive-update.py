#!/usr/bin/env python3
"""Фоновая выкладка: одна глава раз в N минут, пока есть что выкладывать.

Пакетный прогон `publish_ranobelib.py --publish` вываливает на читателя сотню
глав разом, а потом сутки тишины. Здесь другое поведение: скрипт живёт в фоне и
подкладывает по главе с ровным шагом, ровно в темпе, в котором их успевает
переводить `autorun.py`. Кончились готовые — ждёт, не выходя.

    python passive-update.py                 по главе раз в 15 минут
    python passive-update.py --every 30      раз в полчаса
    python passive-update.py --once --dry    один круг вхолостую, для проверки

Останавливается по Ctrl+C, замок при этом снимается.

Три вещи, которые он делает сам, и все три оплачены прошлыми граблями.

**Готовит главу перед отправкой.** Свежий перевод бывает не финализирован
(остались маркеры `[N]`) или несёт разрыв абзаца, доставшийся от источника.
Без подготовки такая глава молча пропускалась бы на каждом круге и не уехала
никогда.

**Ждёт чужой замок, а не падает на нём.** Пакетная выкладка и этот скрипт
делят один журнал; два писателя разом ломают его на Windows.

**Восстанавливается после спорного ответа API.** Ответ 422 считается успехом
только после сверки тома, номера и текста с сайтом: иначе локальный журнал
может отметить как опубликованную чужую или вовсе не принятую главу.
"""

import argparse
import datetime as dt
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import finalize                                     # noqa: E402
import publish_ranobelib as pub                     # noqa: E402
from common import load_json, ru_path, save_json, write_text  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "merge_broken_paragraphs",
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "tools", "merge_broken_paragraphs.py"))
merger = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(merger)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def log(msg):
    print("%s  %s" % (dt.datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def wait_for_lock(timeout=1800):
    """Дождаться, пока освободится пакетная выкладка. Возвращает False, если
    ждём слишком долго — значит замок протух, и решать должен человек."""
    waited = 0
    while os.path.exists(pub.LOCK):
        if waited == 0:
            log("пакетная выкладка занята, жду")
        if waited >= timeout:
            log("замок держится больше %d мин — пропускаю круг" % (timeout // 60))
            return False
        time.sleep(15)
        waited += 15
    return True


def prepare(idx, dry=False):
    """Довести главу до отправляемого вида. True, если готова.

    В пробном прогоне на диск ничего не пишется: финализация и склейка только
    проверяются."""
    status, detail, text = finalize.finalize_one(idx)
    if status == "fail":
        log("    гл.%04d: финализация не далась — %s" % (idx, detail))
        return False
    if status == "ok" and not dry:
        write_text(ru_path(idx), text + "\n")
    merges, _ = merger.fix_chapter(idx, apply=not dry)
    if merges:
        log("    гл.%04d: склеено разорванных абзацев: %d" % (idx, merges))
    return True


def next_chapter(mapping, covered, ledger, blocked=()):
    """Первая по порядку чтения глава, которой ещё нет на сайте.

    `blocked` — главы, которые в этом запуске уже не прошли подготовку или
    отправку. Без пропуска такая глава вставала бы первой на каждом круге и
    навсегда запирала очередь за собой."""
    for idx in sorted(mapping):
        if idx in blocked or idx in covered or str(idx) in ledger["published"]:
            continue
        if os.path.exists(ru_path(idx)):
            return idx
    return None


def publish_one(idx, mapping, ledger, token, dry):
    vol, num = mapping[idx]
    tag = "гл.%04d -> том %s, глава %s" % (idx, vol, num)

    payload, why = pub.build_payload(idx, vol, num)
    if why:
        log("[!] %s: %s" % (tag, why))
        return False

    if dry:
        log("[ ] %s | «%s» | абзацев %d"
            % (tag, payload["name"][:50], len(payload["content"]["content"])))
        return True

    ok, answer = pub.post_chapter(payload, token)
    if not ok:
        log("[x] %s: отказ %s" % (tag, str(answer)[:200]))
        return False

    from common import text_fingerprint
    ledger["published"][str(idx)] = {"volume": vol, "number": num,
                                     "name": payload["name"],
                                     "fingerprint": text_fingerprint(ru_path(idx))}
    save_json(pub.LEDGER, ledger)
    log("[+] %s | «%s»" % (tag, payload["name"][:50]))
    return True


def main():
    ap = argparse.ArgumentParser(description="Фоновая выкладка по главе раз в N минут")
    ap.add_argument("--every", type=float, default=15, metavar="N",
                    help="шаг в минутах (по умолчанию 15)")
    ap.add_argument("--once", action="store_true", help="один круг и выход")
    ap.add_argument("--dry", action="store_true", help="ничего не отправлять")
    ap.add_argument("--token-file")
    args = ap.parse_args()

    token = None if args.dry else pub.token_or_die(args.token_file)
    step = max(args.every, 0.5) * 60

    log("шаг %.0f мин%s" % (args.every, ", пробный прогон" if args.dry else ""))
    exp = pub.token_expiry(args.token_file) if not args.dry else None
    if exp:
        log("токен годен ещё %.1f сут" % ((exp - time.time()) / 86400))

    blocked = set()
    try:
        while True:
            if wait_for_lock():
                # Проверка выше только уменьшает шум в журнале. Сам захват
                # обязан быть атомарным: между проверкой и записью другой
                # издатель мог успеть взять lock.
                try:
                    release = pub.take_lock()
                except SystemExit:
                    log("пакетная выкладка успела занять lock — пропускаю круг")
                else:
                    try:
                        mapping, _coll, covered = pub.load_volume_map()
                        ledger = load_json(pub.LEDGER, {"published": {}}) or {"published": {}}
                        idx = next_chapter(mapping, covered, ledger, blocked)
                        if idx is None:
                            log("готовых глав нет — жду перевода")
                        elif not (prepare(idx, args.dry)
                                  and publish_one(idx, mapping, ledger, token, args.dry)):
                            blocked.add(idx)
                            log("    гл.%04d отложена до перезапуска; пропущены: %s"
                                % (idx, ", ".join(str(i) for i in sorted(blocked))))
                    finally:
                        release()

            if args.once:
                return
            time.sleep(step)
    except KeyboardInterrupt:
        log("остановлено")


if __name__ == "__main__":
    main()
