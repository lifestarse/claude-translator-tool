"""Ветка сообщений внутри одного абзаца → отдельные строки при выводе.

Источник часто склеивает в один абзац чат, комментарии под постом или
системные окна: так их распознал скриншот или выгрузил сайт. Переводчик
держит абзацы 1:1 с оригиналом, поэтому в тексте остаётся стена из десятка
[реплик] с маркерами ответов «└». Разбивать сам перевод нельзя — сломается
выравнивание с оригиналом, на котором держится вся приёмка. Поэтому строки
расставляются только при выводе: каждое [сообщение] — своей строкой, ответ
«└» — с отступом, а короткая метка вроде [ЛУЧШЕЕ] остаётся при своём
сообщении.

Абзац с текстом вне скобок («[Тема дня] Корейская Башня Магов…») и
одиночное окно («[Негативный статус: Агрессия]») не трогаются.
"""

import re

REPLY = ("└", "ㄴ", "↳")
# Метка, а не сообщение: короткое слово прописными — [ЛУЧШЕЕ], [BEST], [HOT].
TAG_RE = re.compile(r"^\[[A-ZА-ЯЁ0-9 !+#]{1,16}\]$")


def _blocks(p):
    """[(префикс, [блок])] верхнего уровня или None, если вне скобок есть текст."""
    out, i, n = [], 0, len(p)
    while i < n:
        j = p.find("[", i)
        prefix = p[i:] if j < 0 else p[i:j]
        if prefix.strip() and prefix.strip() not in REPLY:
            return None
        if j < 0:
            break
        depth, k = 0, j
        while k < n:
            if p[k] == "[":
                depth += 1
            elif p[k] == "]":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        if depth:
            return None
        out.append((prefix.strip(), p[j:k + 1]))
        i = k + 1
    return out


def chat_lines(p):
    """[(строка, ответ ли)], если абзац — ветка из двух и больше сообщений; иначе None."""
    p = p.strip()
    if not p.startswith(("[",) + REPLY) or not p.endswith("]"):
        return None
    blocks = _blocks(p)
    if not blocks or len(blocks) < 2:
        return None
    lines = []                       # [ответ ли, [блоки], есть ли сообщение]
    for prefix, block in blocks:
        tag = bool(TAG_RE.match(block))
        if not lines or prefix in REPLY or lines[-1][2]:
            lines.append([prefix in REPLY, [block], not tag])
        else:
            lines[-1][1].append(block)
            lines[-1][2] = lines[-1][2] or not tag
    if len(lines) < 2:
        return None
    return [(("└ " if reply else "") + " ".join(bl), reply) for reply, bl, _ in lines]
