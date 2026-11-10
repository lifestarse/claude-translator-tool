#!/usr/bin/env python3
"""Переименование сущности в готовом переводе морфологией, без агентов.

Дешёвый первый проход перед флотом termfix: имя меняется локальным
морфоанализатором (pymorphy3) с учётом падежа и числа, а по синтаксическому
дереву (natasha) правится род согласованных с ним слов — определений и
сказуемого. Секунды на главу, ни одного токена.

    python morph_rename.py --from Защитник --from Стражник --to Страж --range 37 54
    python morph_rename.py --from "Пылевой крокодил" --to "Пеплозев" --range 1 500 --apply
    python morph_rename.py --from Страж --to Тень --gender femn --range 1 90 --apply --resolve

Без --apply ничего не пишется: показывается план. Зона глав обязательна —
правило «по всему корпусу» однажды уже вандалило текст.

Чего морфология НЕ умеет, и поэтому не трогает, а откладывает:

  * местоимения («Страж вошёл. Он сел») — дерево строится в пределах одного
    предложения, кто такой «он», оно не знает;
  * придаточные с «который»;
  * места, где падеж определяется неоднозначно или слово не склоняется
    словарём.

Абзац с любым таким местом остаётся как был и попадает в `morph_doubt.json`.
С флагом --resolve эти абзацы уходят дешёвой модели (роль haiku активного
провайдера, см. llm.py) — по одному короткому запросу на абзац, параллельно.
Всё остальное из отложенного — работа для агентов termfix.

Каждая глава после правки проходит приёмку; провал — глава не записывается.
Список замен пишется в `replacement_morph.txt` — пролистай его глазами:
парсер ошибается уверенно, и его ошибки видны только человеку.

Нужны: pip install pymorphy3 natasha
"""

import argparse
import difflib
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor

from common import (
    ROOT,
    chapter_path,
    read_text,
    ru_path,
    save_json,
    validate_translation,
    write_text,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPORT_FILE = os.path.join(ROOT, "replacement_morph.txt")
DOUBT_FILE = os.path.join(ROOT, "morph_doubt.json")

CASES = {"Nom": "nomn", "Gen": "gent", "Dat": "datv", "Acc": "accs",
         "Ins": "ablt", "Loc": "loct", "Par": "gent", "Voc": "nomn"}
GENDERS = {"Masc": "masc", "Fem": "femn", "Neut": "neut"}
NUMBERS = {"Sing": "sing", "Plur": "plur"}
GENDER_RU = {"masc": "мужской", "femn": "женский", "neut": "средний"}
AGREEING = {"ADJF", "ADJS", "PRTF", "PRTS", "VERB"}
PRONOUNS = {
    "masc": {"он", "его", "него", "ему", "нему", "им", "ним", "нём", "нем"},
    "femn": {"она", "её", "ее", "неё", "нее", "ей", "ней", "ею", "нею"},
    "neut": {"оно", "его", "него", "ему", "нему", "им", "ним", "нём", "нем"},
}


def recase(word, like):
    """Регистр первой буквы — как у образца."""
    if like[:1].isupper():
        return word[:1].upper() + word[1:]
    return word


class Engine(object):
    """Морфология и синтаксис русского текста. Модели грузятся один раз."""

    def __init__(self):
        try:
            import pymorphy3
            from natasha import (Doc, NewsEmbedding, NewsMorphTagger,
                                 NewsSyntaxParser, Segmenter)
        except ImportError as exc:
            raise SystemExit("Не хватает библиотеки (%s). Поставь: "
                             "pip install pymorphy3 natasha" % exc.name)
        self.morph = pymorphy3.MorphAnalyzer()
        self.Doc = Doc
        emb = NewsEmbedding()
        self.segmenter = Segmenter()
        self.tagger = NewsMorphTagger(emb)
        self.parser = NewsSyntaxParser(emb)

    def parse(self, text):
        doc = self.Doc(text)
        doc.segment(self.segmenter)
        doc.tag_morph(self.tagger)
        doc.parse_syntax(self.parser)
        return doc

    def lemmas(self, word):
        return {p.normal_form.replace("ё", "е") for p in self.morph.parse(word)}


class Name(object):
    """Имя сущности в именительном падеже: «Страж», «Пылевой крокодил»,
    «Владыка демонов». Определения до главного слова склоняются вместе с ним,
    всё после главного слова остаётся как есть."""

    def __init__(self, engine, text, gender=None, indeclinable=False):
        self.engine = engine
        self.text = text.strip()
        self.words = self.text.split()
        self.indeclinable = indeclinable
        self.head = 0
        for i, w in enumerate(self.words):
            if any(p.tag.POS == "NOUN" and p.tag.case == "nomn"
                   for p in engine.morph.parse(w)):
                self.head = i
                break
        self.lemma_seq = [self._lemma(w) for w in self.words]
        head_parse = self._head_parse(gender)
        self.head_parse = head_parse
        self.gender = gender or (head_parse.tag.gender if head_parse else None)
        self.animacy = head_parse.tag.animacy if head_parse else None
        tail = self.words[self.head][-1:].lower()
        known = head_parse is not None and engine.morph.word_is_known(self.words[self.head])
        if not indeclinable and (head_parse is None or (not known and (
                tail in "оеиуэюы" or (self.gender == "femn" and tail not in "аяь")))):
            # Иноязычные имена на гласную и женские на согласную не склоняются.
            # Словарным словам («Существо», «Тень») верим как есть.
            self.indeclinable = True

    def _lemma(self, word):
        parses = self.engine.morph.parse(word)
        for p in parses:
            if p.tag.case == "nomn":
                return p.normal_form.replace("ё", "е")
        return parses[0].normal_form.replace("ё", "е")

    def _head_parse(self, gender):
        best = None
        for p in self.engine.morph.parse(self.words[self.head]):
            if p.tag.POS != "NOUN" or p.tag.case != "nomn" or p.tag.number != "sing":
                continue
            if gender and p.tag.gender != gender:
                continue
            if best is None:
                best = p
        return best

    def inflect(self, case, number):
        """Имя в нужном падеже и числе; None — словарь не справился."""
        out = []
        for i, w in enumerate(self.words):
            if i > self.head or self.indeclinable and i == self.head:
                out.append(w)
                continue
            if i == self.head:
                form = self.head_parse.inflect({case, number})
            else:
                form = None
                grams = {case, number}
                if number == "sing" and self.gender:
                    grams.add(self.gender)
                if case == "accs" and self.animacy and (number == "plur" or self.gender == "masc"):
                    grams.add(self.animacy)
                for p in self.engine.morph.parse(w):
                    if p.tag.POS in ("ADJF", "PRTF") and p.tag.case == "nomn":
                        form = p.inflect(grams)
                        break
            if form is None:
                return None
            out.append(recase(form.word, w))
        return " ".join(out)


class Plan(object):
    """Что сделать с одним абзацем."""

    def __init__(self):
        self.edits = []      # (start, stop, новое, старое)
        self.doubts = []     # причины, по которым абзац отложен
        self.found = 0


def match_at(engine, tokens, i, old):
    """Совпадает ли старое имя с токенами, начиная с i."""
    if i + len(old.words) > len(tokens):
        return False
    for k, lemma in enumerate(old.lemma_seq):
        tok = tokens[i + k]
        if k and tokens[i + k - 1].stop != tok.start - 1:
            return False
        if lemma not in engine.lemmas(tok.text):
            return False
    # Имя с заглавной ищется только с заглавной: «Страж» не должен цеплять
    # нарицательное «страж» в середине фразы.
    if old.words[0][:1].isupper() and not tokens[i].text[:1].isupper():
        return False
    return True


def regender(engine, tok, old_gender, new_gender):
    """Форма слова в новом роде; None — не получилось однозначно."""
    forms = set()
    want_case = CASES.get(tok.feats.get("Case"))
    for p in engine.morph.parse(tok.text):
        if p.tag.POS not in AGREEING or p.tag.gender != old_gender:
            continue
        if want_case and p.tag.case and p.tag.case != want_case:
            continue
        grams = {new_gender}
        if p.tag.case == "accs" and p.tag.animacy and new_gender == "masc":
            grams.add(p.tag.animacy)
        f = p.inflect(grams)
        if f is not None:
            forms.add(f.word)
    if len(forms) != 1:
        return None
    return recase(forms.pop(), tok.text)


def plan_paragraph(engine, text, olds, new):
    plan = Plan()
    doc = engine.parse(text)
    tokens = doc.tokens
    by_id = {t.id: t for t in tokens}
    children = {}
    for t in tokens:
        children.setdefault(t.head_id, []).append(t)
    taken = set()

    i = 0
    while i < len(tokens):
        old = next((o for o in olds if match_at(engine, tokens, i, o)), None)
        if old is None:
            i += 1
            continue
        plan.found += 1
        span = tokens[i:i + len(old.words)]
        head = span[old.head]
        i += len(old.words)

        # --- падеж и число -------------------------------------------------
        options = set()
        old_gender = None
        for p in engine.morph.parse(head.text):
            if old.lemma_seq[old.head] != p.normal_form.replace("ё", "е"):
                continue
            if p.tag.case and p.tag.number:
                options.add((p.tag.case, p.tag.number))
                old_gender = old_gender or p.tag.gender
        want = (CASES.get(head.feats.get("Case")), NUMBERS.get(head.feats.get("Number")))
        narrowed = {o for o in options if o == want} or \
            {o for o in options if o[0] == want[0]} or options
        forms = {new.inflect(c, n) for c, n in narrowed}
        if len(forms) != 1 or None in forms:
            plan.doubts.append("падеж «%s» не определился однозначно" % head.text)
            continue
        # У одушевлённых мужского рода родительный и винительный совпадают
        # («боялись Стража» / «увидели Стража»), а у нового имени могут
        # различаться («Тени» / «Тень»). Разметчик тут ошибается уверенно —
        # это управление глагола, а не форма слова. Не гадаем.
        twin = {"gent", "accs"} - {next(iter(narrowed))[0]}
        if len(twin) == 1 and any(o[0] in twin for o in options):
            other = {new.inflect(c, n) for c, n in options if c in twin}
            if other - forms:
                plan.doubts.append("«%s»: родительный или винительный" % head.text)
                continue
        number = next(iter(narrowed))[1]
        start, stop = span[0].start, span[-1].stop
        new_form = forms.pop()
        plan.edits.append((start, stop, new_form, text[start:stop]))
        k = tokens.index(span[0])
        if k and tokens[k - 1].stop == start - 1:
            prep = fix_preposition(tokens[k - 1].text, new_form)
            if prep != tokens[k - 1].text:
                plan.edits.append((tokens[k - 1].start, tokens[k - 1].stop, prep,
                                   tokens[k - 1].text))
        for t in span:
            taken.add(t.id)

        # --- род согласованных слов ----------------------------------------
        old_gender = GENDERS.get(head.feats.get("Gender")) or old_gender
        if number != "sing" or not new.gender or not old_gender or old_gender == new.gender:
            continue
        targets = []
        for t in span:
            for ch in children.get(t.id, []):
                if ch.id in taken:
                    continue
                if ch.rel == "acl:relcl":
                    plan.doubts.append("придаточное при «%s»" % head.text)
                elif ch.rel in ("amod", "det", "acl") and ch.feats.get("Gender"):
                    targets.append(ch)
        if head.rel in ("nsubj", "nsubj:pass") and head.head_id in by_id:
            pred = by_id[head.head_id]
            preds = [pred] + [c for c in children.get(pred.id, [])
                              if c.rel == "conj" and not any(
                                  g.rel.startswith("nsubj") for g in children.get(c.id, []))]
            for pr in preds:
                if pr.feats.get("Gender"):
                    targets.append(pr)
                targets += [c for c in children.get(pr.id, [])
                            if c.rel in ("aux", "aux:pass", "cop") and c.feats.get("Gender")]
        for t in targets:
            if GENDERS.get(t.feats.get("Gender")) != old_gender:
                continue
            form = regender(engine, t, old_gender, new.gender)
            if form is None:
                plan.doubts.append("род «%s» не переставился" % t.text)
            elif form != t.text:
                plan.edits.append((t.start, t.stop, form, t.text))
        # Местоимения того же рода после имени: чьи они, дерево не знает.
        for t in tokens:
            if t.start > stop and t.text.lower() in PRONOUNS.get(old_gender, ()):
                plan.doubts.append("местоимение «%s» после имени" % t.text)
                break
    return plan


VOWELS = "аеёиоуыэюя"


def fix_preposition(prep, word):
    """с/со, в/во, к/ко, о/об перед новым словом: «со Стражем» -> «с Тенью»."""
    low, w = prep.lower(), word.lower()
    cluster = len(w) > 1 and w[0] not in VOWELS and w[1] not in VOWELS
    if low in ("с", "со"):
        out = "со" if (cluster and w[0] in "сзшж") or w[0] == "щ" or w.startswith("мн") else "с"
    elif low in ("в", "во"):
        out = "во" if (cluster and w[0] in "вф") or w.startswith("мн") else "в"
    elif low in ("к", "ко"):
        out = "ко" if w.startswith("мн") else "к"
    elif low in ("о", "об"):
        out = "об" if w[0] in "аиоуэ" else "о"
    else:
        return prep
    return recase(out, prep)


def apply_edits(text, edits):
    for start, stop, new, _old in sorted(set(edits), reverse=True):
        text = text[:start] + new + text[stop:]
    return text


def prefilter(olds):
    """Грубый отсев абзацев без имени — чтобы не гонять парсер по всему тексту."""
    stems = []
    for o in olds:
        w = o.lemma_seq[o.head]
        stems.append(w[:max(3, len(w) - 2)])
    rx = re.compile("|".join(re.escape(s) for s in stems), re.I)
    return lambda para: bool(rx.search(para.replace("ё", "е").replace("Ё", "Е")))


# --------------------------------------------------------------------------
# Отложенные абзацы -> дешёвая модель
# --------------------------------------------------------------------------

PROMPT = """В абзаце русского перевода сущность названа неверно. Замени её имя: {olds} -> «{new}»{gender}.

Правила:
- новое имя ставь в тот же падеж и число, в каком стояло старое;
- согласуй с новым именем род зависимых слов: определений, причастий, сказуемых в прошедшем времени;
- местоимения, которые относятся именно к этой сущности, приведи к новому роду; чужие местоимения не трогай;
- больше ничего не меняй: ни слова, ни знака.

Ответь ТОЛЬКО исправленным абзацем, без пояснений и кавычек.

Абзац:
{para}"""


def ask_model(para, olds, new, model):
    import llm
    prompt = PROMPT.format(
        olds=", ".join("«%s»" % o.text for o in olds), new=new.text,
        gender=" (род: %s)" % GENDER_RU[new.gender] if new.gender in GENDER_RU else "",
        para=para)
    try:
        proc = llm.run_agent(prompt, None, model, timeout=300)
        data = json.loads(proc.stdout or "")
    except Exception as exc:    # noqa: BLE001 — любая поломка = абзац не тронут
        return None, "нет ответа модели: %s" % exc
    if proc.returncode or data.get("is_error"):
        return None, "ошибка модели: %s" % str(data.get("result"))[:120]
    out = str(data.get("result") or "").strip()
    if not out or "\n\n" in out:
        return None, "ответ не похож на один абзац"
    if not 0.7 <= len(out) / max(len(para), 1) <= 1.4:
        return None, "длина ответа ушла далеко от исходной"
    if difflib.SequenceMatcher(None, para, out).ratio() < 0.6:
        return None, "ответ слишком далёк от исходного абзаца"
    return out, ""


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Переименование сущности морфологией (pymorphy3 + natasha)")
    ap.add_argument("--from", dest="olds", action="append", required=True, metavar="ИМЯ",
                    help="старое имя в именительном падеже; можно несколько раз")
    ap.add_argument("--to", dest="new", required=True, metavar="ИМЯ",
                    help="новое имя в именительном падеже")
    ap.add_argument("--gender", choices=sorted(GENDER_RU),
                    help="род нового имени, если словарь его не знает (masc/femn/neut)")
    ap.add_argument("--indeclinable", action="store_true", help="новое имя не склоняется")
    ap.add_argument("--range", type=int, nargs=2, metavar=("LO", "HI"))
    ap.add_argument("--chapters", type=int, nargs="+")
    ap.add_argument("--apply", action="store_true", help="записать (без флага — только план)")
    ap.add_argument("--resolve", action="store_true",
                    help="отложенные абзацы отдать дешёвой модели")
    ap.add_argument("--model", default="haiku", help="роль модели для --resolve")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    if not args.range and not args.chapters:
        raise SystemExit("Нужна зона глав: --range LO HI или --chapters N N N. "
                         "Правило без зоны по корпусу не запускается.")
    zone = sorted(set(args.chapters or range(args.range[0], args.range[1] + 1)))

    engine = Engine()
    new = Name(engine, args.new, args.gender, args.indeclinable)
    olds = [Name(engine, o) for o in args.olds]
    print("Новое имя : «%s», род %s%s" % (
        new.text, GENDER_RU.get(new.gender, "не определён (задай --gender)"),
        ", не склоняется" if new.indeclinable else ""))
    if not new.indeclinable:
        print("Склонение : " + ", ".join(
            str(new.inflect(c, "sing")) for c in ("gent", "datv", "accs", "ablt", "loct")))
    has_name = prefilter(olds)

    report, doubts = [], []
    changed = failed = total = 0
    for idx in zone:
        path = ru_path(idx)
        if not os.path.exists(path):
            continue
        text = read_text(path)
        parts = re.split(r"(\n\s*\n)", text)
        pending = []        # (номер части, абзац) — отложено для модели
        touched = False
        for n in range(0, len(parts), 2):
            para = parts[n]
            if not para.strip() or not has_name(para):
                continue
            plan = plan_paragraph(engine, para, olds, new)
            if not plan.found:
                continue
            if plan.doubts:
                pending.append((n, para, sorted(set(plan.doubts))))
                continue
            fixed = apply_edits(para, plan.edits)
            if fixed != para:
                parts[n] = fixed
                touched = True
                total += len(plan.edits)
                for _s, _e, new_w, old_w in sorted(set(plan.edits)):
                    report.append("гл.%04d абз.%d: %s -> %s" % (idx, n // 2 + 1, old_w, new_w))

        if pending and args.resolve:
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                answers = list(pool.map(
                    lambda item: ask_model(item[1], olds, new, args.model), pending))
            rest = []
            for (n, para, why), (out, err) in zip(pending, answers):
                if out is None or plan_paragraph(engine, out, olds, new).found:
                    rest.append((n, para, why + [err or "модель оставила старое имя"]))
                    continue
                parts[n] = out
                touched = True
                total += 1
                report.append("гл.%04d абз.%d [модель]:\n    было : %s\n    стало: %s"
                              % (idx, n // 2 + 1, para, out))
            pending = rest
        for n, para, why in pending:
            doubts.append({"chapter": idx, "paragraph": n // 2 + 1, "why": why, "text": para})

        if not touched:
            continue
        fresh = "".join(parts)
        src = chapter_path(idx)
        if os.path.exists(src):
            source = read_text(src)
            if not validate_translation(source, text) and validate_translation(source, fresh):
                failed += 1
                print("[!] гл.%04d: правка ломает приёмку — не записана" % idx)
                continue
        changed += 1
        if args.apply:
            write_text(path, fresh)

    print("")
    print("Глав с правкой : %d%s" % (changed, "" if args.apply else " (план, не записано)"))
    print("Замен          : %d" % total)
    print("Отложено абзацев: %d%s" % (
        len(doubts), "" if not doubts or args.resolve else
        "  — добавь --resolve, чтобы отдать их дешёвой модели"))
    if failed:
        print("Не прошли приёмку: %d глав" % failed)
    if report:
        write_text(REPORT_FILE, "%s -> %s\n\n%s\n" % (
            ", ".join(args.olds), args.new, "\n".join(report)))
        print("Список замен   : %s — пролистай глазами" % os.path.basename(REPORT_FILE))
    if doubts:
        save_json(DOUBT_FILE, doubts)
        print("Отложенное     : %s — для агентов termfix или ручной правки"
              % os.path.basename(DOUBT_FILE))
    if not args.apply and changed:
        print("Записать: тот же вызов с --apply")


if __name__ == "__main__":
    main()
