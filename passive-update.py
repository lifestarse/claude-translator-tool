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

# Склейка разорванных абзацев живёт в tools/merge_broken_paragraphs.py. Файла
# в каркасе может не быть — тогда работаем без склейки и говорим об этом при
# старте, а не падаем на импорте: раньше без него не работали даже --help/--dry.
MERGER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "tools", "merge_broken_paragraphs.py")
merger = None
if os.path.exists(MERGER_PATH):
    _spec = importlib.util.spec_from_file_location("merge_broken_paragraphs", MERGER_PATH)
    merger = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(merger)

# Сколько кругов не трогать главу после отказа. Без этого next_chapter на
# каждом круге брал ту же первую неотправляемую главу, и фоновая выкладка
# стояла до вмешательства человека.
DEFER_ROUNDS = 8

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
    """Довести главу до отправляемого вида. (готова, текст-из-памяти или None).

    В пробном прогоне ru/ не трогается: финализированный текст возвращается
    вызывающему, а склейка только считается. Раньше --dry переписывал корпус.
    """
    status, detail, text = finalize.finalize_one(idx)
    if status == "fail":
        log("    гл.%04d: финализация не далась — %s" % (idx, detail))
        return False, None
    if status != "ok":
        text = None
    elif dry:
        log("    гл.%04d: будет финализирована (%s), на диск не пишу" % (idx, detail))
    else:
        write_text(ru_path(idx), text + "\n")
    if merger is not None:
        # В пробном прогоне склейка только считается по файлу на диске.
        merges, _ = merger.fix_chapter(idx, apply=not dry)
        if merges:
            log("    гл.%04d: %s разорванных абзацев: %d"
                % (idx, "нашлось бы" if dry else "склеено", merges))
    return True, (text if dry else None)


def next_chapter(mapping, covered, ledger, deferred=()):
    """Первая по порядку чтения глава, которой ещё нет на сайте и которая не
    отложена после отказа."""
    for idx in sorted(mapping):
        if idx in covered or str(idx) in ledger["published"] or idx in deferred:
            continue
        if os.path.exists(ru_path(idx)):
            return idx
    return None


def publish_one(idx, mapping, ledger, token, dry, text=None):
    vol, num = mapping[idx]
    tag = "гл.%04d -> том %s, глава %s" % (idx, vol, num)

    payload, why = pub.build_payload(idx, vol, num, text=text)
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
        # Связь, сбой сайта, протухший токен — глава не виновата. Откладывать её
        # нельзя: за время сбоя очередь перескочила бы через десяток глав подряд
        # и на сайте они встали бы вразнобой. None — повторить её же на след. круге.
        code = answer.get("code") if isinstance(answer, dict) else None
        if code == "нет ответа" or code in (401, 403) \
                or (isinstance(code, int) and code >= 500):
            return None
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

    if not args.dry:
        pub.require_site_settings()
    token = None if args.dry else pub.token_or_die(args.token_file)
    step = max(args.every, 0.5) * 60

    log("шаг %.0f мин%s" % (args.every, ", пробный прогон" if args.dry else ""))
    if merger is None:
        # Как и шаг merge в main.py: приёмку главы проходят (абзацы 1:1 с
        # источником), но разрыв посреди фразы уедет читателю как есть.
        log("[!] нет %s — склейка разорванных абзацев отключена, главы уходят "
            "с разрывами источника как есть" % "tools/merge_broken_paragraphs.py")
    deferred = {}   # {индекс: круг, после которого глава снова в очереди}
    round_no = 0
    exp = pub.token_expiry(args.token_file) if not args.dry else None
    if exp:
        log("токен годен ещё %.1f сут" % ((exp - time.time()) / 86400))

    try:
        while True:
            round_no += 1
            for i in [i for i, until in deferred.items() if until < round_no]:
                del deferred[i]
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
                        idx = next_chapter(mapping, covered, ledger, deferred)
                        if idx is None:
                            log("готовых глав нет — жду перевода%s"
                                % ("; отложены после отказа: %s"
                                   % ", ".join("%04d" % i for i in sorted(deferred))
                                   if deferred else ""))
                        else:
                            ready, text = prepare(idx, args.dry)
                            sent = publish_one(idx, mapping, ledger, token,
                                               args.dry, text) if ready else False
                            if sent is False:
                                # Следующие главы уйдут раньше этой, и на сайте
                                # будет дыра в порядке чтения — говорим громко.
                                deferred[idx] = round_no + DEFER_ROUNDS
                                log("[!] гл.%04d застряла — откладываю на %d кругов; "
                                    "пока её нет, на сайте дыра в нумерации"
                                    % (idx, DEFER_ROUNDS))
                    finally:
                        release()

            if args.once:
                return
            time.sleep(step)
    except KeyboardInterrupt:
        log("остановлено")


if __name__ == "__main__":
    main()
