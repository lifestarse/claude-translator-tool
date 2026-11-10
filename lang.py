#!/usr/bin/env python3
"""Профиль языка оригинала: всё, что в конвейере зависит от языка исходника.

Конвейер вырос на китайском, и «китайскость» была размазана по коду: диапазон
иероглифов в приёмке, норма объёма 2.0–4.5, шаблон `第N章`, n-граммы иероглифов
в экстракторе терминов. Здесь это собрано в одно место. Язык задаётся строкой
в BOOK.md:

    - Язык оригинала (код): ko

Поддерживаются: zh ja ko en es fr de pt it nl pl tr vi id th ar fa he hi uk.
Неизвестный код = общий профиль (алфавитный язык с пробелами).

    python lang.py              показать действующий профиль
    python lang.py --measure    замерить коэффициент объёма на готовых главах

ВАЖНО про нормы объёма. Откалибрована на живом корпусе только китайская
(2.0–4.5). Остальные — оценки с запасом. Переведи десяток глав, запусти
`python lang.py --measure` и впиши замеренную норму в BOOK.md:

    - Норма объёма (мин-макс): 1.3-2.6
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
BOOK_FILE = os.path.join(ROOT, "BOOK.md")

HAN = "㐀-䶿一-鿿豈-﫿"
HAN_CORE = "㐀-䶿一-鿿"
KANA = "ぁ-ゟ゠-ヺー"
HANGUL = "가-힣ᄀ-ᇿ㄰-㆏"
LATIN = "A-Za-zÀ-ÖØ-öø-ɏḀ-ỿ"

# Конец фразы / открытие реплики в оригинале — для склейки разорванных абзацев
# и нарезки цитат. None = в письменности нет надёжного знака конца фразы.
END_CJK = "。！？!?…」』”’）)】～~—.：:；;"
OPEN_CJK = "「『“‘（(【"
END_ALPHA = ".!?…\"”’»)]:;—–-~。؟۔।॥"
OPEN_ALPHA = "\"“‘«([—–-"

# Слово «глава» на распространённых языках — для номера в заголовке.
CHAPTER_WORDS = (
    r"chapters?|chap\.?|ch\.|episodes?|ep\.?|part|cap[ií]tulos?|cap\.|chapitres?|"
    r"kapitel|capitolo|hoofdstuk|rozdzia[łl]|b[öo]l[üu]m|ch[ưu][ơo]ng|bab|"
    r"глава|розділ|الفصل|فصل|บทที่|ตอนที่|अध्याय|פרק"
)

# (регэксп, пометка о починке). Первый сработавший даёт номер; порядок — от
# точного к снисходительному.
NUM_ZH = (
    (re.compile(r"第\s*(\d+)\s*[章張]"), None),
    # 第1把39章 — знак вклинился внутрь числа. Цифры склеиваются: 1+39=139.
    (re.compile(r"第\s*(\d+)\D{1,2}(\d+)\s*[章張]"), "цифры разорваны иероглифом"),
    (re.compile(r"第\s*(\d+)"), "потерян знак 章"),
    (re.compile(r"(?<![\d.])(\d+)\s*[章張]"), "потерян знак 第"),
)
NUM_JA = (
    (re.compile(r"第\s*(\d+)\s*[話章回節话]"), None),
    (re.compile(r"(?<![\d.])(\d+)\s*[話章回]"), None),
    (re.compile(r"第\s*(\d+)"), "потерян счётный знак"),
    (re.compile(r"(?:%s)\s*(\d+)" % CHAPTER_WORDS, re.I), None),
)
NUM_KO = (
    (re.compile(r"제?\s*(\d+)\s*(?:화|장|회|편|부)(?![가-힣])"), None),
    (re.compile(r"(?:%s)\s*(\d+)" % CHAPTER_WORDS, re.I), None),
    (re.compile(r"^\W*(\d+)\s*[.:)\]]"), "номер без счётного слова"),
)
NUM_GENERIC = (
    (re.compile(r"(?:%s)\s*[#№]?\s*(\d+)" % CHAPTER_WORDS, re.I), None),
    (re.compile(r"^\W*[#№]?\s*(\d+)\s*(?:[.:)\]\-–—]|$)"), "номер без слова «глава»"),
)

MERGE_ZH = re.compile(r"第\s*(\d+)\s*章\s*[+＋]\s*(\d+)\s*章")
MERGE_JA = re.compile(r"第?\s*(\d+)\s*[話章回]?\s*[+＋~～・\-–]\s*第?\s*(\d+)\s*[話章回]")
MERGE_KO = re.compile(r"(\d+)\s*(?:화|장|회)?\s*[+＋~～\-–]\s*(\d+)\s*(?:화|장|회)")
MERGE_GENERIC = re.compile(
    r"(?:%s)\s*(\d+)\s*(?:[+＋&~\-–—]|and|y|et|und|e|и)\s*(?:(?:%s)\s*)?(\d+)"
    % (CHAPTER_WORDS, CHAPTER_WORDS), re.I)

# Глаголы именования: «назвали X», «по имени X». Автор сам объявляет свои
# термины — это точнее любой статистики.
NAMING_ZH = ("(?:叫做|叫作|稱為|稱作|稱呼為|被稱為|被稱作|命名為|名字為|名為|簡稱|"
             "取名為|起名為|起了個名字叫|起了名字叫|稱之為|称为|称作|被称为|命名为|名为|简称)")
GENERIC_ZH = (
    "東西", "玩意", "地方", "感覺", "樣子", "名字", "什麽", "什麼", "時候",
    "事情", "傢伙", "傢夥", "家夥", "一個", "這個", "那個", "自己",
)
NAMING_ALPHA = {
    "en": r"called|named|known as|dubbed|christened|nicknamed",
    "es": r"llamad[oa]s?|llamaban|llam[óo]|conocid[oa]s? como|apodad[oa]s?|nombrad[oa]s?",
    "fr": r"appel[ée]e?s?|nomm[ée]e?s?|surnomm[ée]e?s?|baptis[ée]e?s?|sous le nom de",
    "de": r"genannt|namens|nannten?|getauft|hie[ßs]|bekannt als",
    "pt": r"chamad[oa]s?|conhecid[oa]s? como|apelidad[oa]s?|nomead[oa]s?",
    "it": r"chiamat[oaie]|dett[oaie]|soprannominat[oaie]|not[oaie] come",
    "nl": r"genaamd|geheten|bekend als|noemden?",
    "pl": r"zwan[yae]|nazwan[yae]|znan[yae] jako|o imieniu",
    "tr": r"adlı|denilen|denen|olarak bilinen|adında",
    "id": r"bernama|disebut|dijuluki|dikenal sebagai|dinamai",
    "vi": r"gọi là|tên là|được gọi là|mang tên",
    "uk": r"названий|названа|названі|на ім['’]я|відомий як|звали",
}

# Корейские падежные частицы — снимаются со слова, чтобы «린은», «린이», «린의»
# считались одним кандидатом «린». Длинные раньше коротких.
KO_PARTICLES = sorted((
    "에게서", "으로부터", "한테서", "이라고", "이라는", "에서는", "에게는", "으로는",
    "이었다", "이라도", "까지", "부터", "에서", "에게", "한테", "께서", "으로", "이라",
    "라고", "라는", "처럼", "보다", "마다", "조차", "마저", "이나", "이다", "였다",
    "은", "는", "이", "가", "을", "를", "의", "에", "도", "와", "과", "로", "만", "야", "아",
), key=len, reverse=True)


# Языки, для которых есть словарная лемматизация simplemma (pip install simplemma).
SIMPLEMMA = {"en", "es", "fr", "de", "pt", "it", "nl", "pl", "tr", "id", "uk", "fa", "hi"}
JA_SKIP = ("数", "非自立", "代名詞", "接尾", "副詞可能")


class Morph(object):
    """Морфология языка оригинала — необязательные библиотеки.

    Зачем она нужна. Термин в глоссарии записан в словарной форме, а в тексте
    стоит в косвенной: немецкое Wächters, польское Wiedźmina, корейское 린은.
    Без морфологии такие вхождения считаются разными кандидатами, а термин
    глоссария не находится в главе и не попадает в задание переводчику.

        ko               kiwipiepy   — разбор на морфемы, имена без частиц
        ja               janome      — слова вместо n-грамм знаков
        en es fr de pt   simplemma   — словарная форма слова
        it nl pl tr id
        uk fa hi

    Китайскому, вьетнамскому и тайскому морфология не нужна (слова не
    изменяются); для арабского и иврита подходящей лёгкой библиотеки нет.
    Библиотека не установлена — конвейер работает как раньше, грубее.
    """

    def __init__(self, code):
        self.code = code
        self.backend = None
        self._kiwi = self._janome = self._simplemma = None
        try:
            if code == "ko":
                from kiwipiepy import Kiwi
                self._kiwi, self.backend = Kiwi(), "kiwipiepy"
            elif code == "ja":
                from janome.tokenizer import Tokenizer
                self._janome, self.backend = Tokenizer(), "janome"
            elif code in SIMPLEMMA:
                import simplemma
                simplemma.lemmatize("test", lang=code)
                self._simplemma, self.backend = simplemma, "simplemma"
        except Exception:   # noqa: BLE001 — нет библиотеки или её данных
            self.backend = None

    @property
    def wanted(self):
        """Какую библиотеку стоит поставить для этого языка (или None)."""
        return {"ko": "kiwipiepy", "ja": "janome"}.get(
            self.code, "simplemma" if self.code in SIMPLEMMA else None)

    def lemma(self, word):
        """Словарная форма слова; без библиотеки — само слово."""
        if self._simplemma is None:
            return word
        out = self._simplemma.lemmatize(word, lang=self.code)
        return out[:1].upper() + out[1:] if word[:1].isupper() else out

    def nouns(self, text):
        """Существительные и их цепочки (ko, ja) — кандидаты в термины."""
        run = []
        if self._kiwi is not None:
            for t in self._kiwi.tokenize(text):
                if t.tag in ("NNP", "NNG") and (not run or run[-1][1] == t.start):
                    run.append((t.form, t.start + t.len))
                    continue
                if run:
                    yield "".join(f for f, _ in run)
                run = [(t.form, t.start + t.len)] if t.tag in ("NNP", "NNG") else []
            if run:
                yield "".join(f for f, _ in run)
        elif self._janome is not None:
            for t in self._janome.tokenize(text):
                pos = t.part_of_speech.split(",")
                if pos[0] == "名詞" and pos[1] not in JA_SKIP:
                    run.append(t.surface)
                    continue
                if run:
                    yield "".join(run)
                run = []
            if run:
                yield "".join(run)

    def forms(self, text):
        """Множество словарных форм слов текста, в нижнем регистре."""
        if self._simplemma is None:
            return frozenset()
        return frozenset(self._simplemma.lemmatize(w, lang=self.code).lower()
                         for w in set(re.findall(r"[^\W\d_]+", text)))


class Profile(object):
    """Свойства языка оригинала. Поля — данные, методы — то, что код конвейера
    раньше делал китайскими регэкспами напрямую."""

    def __init__(self, code, name, from_, adj_f, script=None, script_name="",
                 ratio=(0.65, 1.7), short_para=40, max_leftover=0,
                 extract="capitalized", extract_chars=None, ngrams=(2, 3, 4, 5),
                 nums=NUM_GENERIC, merge=MERGE_GENERIC,
                 end=END_ALPHA, open_=OPEN_ALPHA, transcription="",
                 calibrated=False):
        self.code = code
        self.name = name                # «китайский»
        self.from_ = from_              # «с китайского»
        self.adj_f = adj_f              # «китайской» (веб-новеллы)
        # Знаки письменности оригинала, которых в русском тексте быть не должно.
        self.script_class = script
        self.script_re = re.compile("[%s]" % script) if script else None
        self.script_name = script_name  # «китайских иероглифов»
        self.max_leftover = max_leftover
        self.min_ratio, self.max_ratio = ratio
        self.calibrated = calibrated
        # Абзац короче этого в поабзацной сверке объёма не участвует:
        # «?» -> «Что?» — огромный коэффициент и совершенно верный перевод.
        self.short_para = short_para
        self.extract = extract          # ngram | words | capitalized
        self.extract_chars = extract_chars or script or LATIN
        self.ngrams = ngrams
        self.nums = nums
        self.merge_re = merge
        self.end = end
        self.open = open_
        self.transcription = transcription
        self._morph = None

    @property
    def morph(self):
        """Морфология оригинала; библиотеки грузятся при первом обращении."""
        if self._morph is None:
            self._morph = Morph(self.code)
        return self._morph

    def term_in(self, term, text, forms=None):
        """Встречается ли термин глоссария в тексте оригинала.

        Сначала дословно. Если не нашёлся, а язык склоняется и библиотека
        стоит, — по словарным формам: «Wächter» найдётся в «des Wächters».
        """
        if term in text:
            return True
        if self.morph.backend != "simplemma":
            return False
        forms = forms if forms is not None else self.morph.forms(text)
        words = re.findall(r"[^\W\d_]+", term)
        return bool(words) and all(self.morph.lemma(w).lower() in forms for w in words)

    # --- приёмка ----------------------------------------------------------

    @property
    def para_low(self):
        return round(self.min_ratio * 0.175, 3)

    @property
    def para_high(self):
        return round(self.max_ratio * 2.0, 3)

    _LATIN_PHRASE = re.compile(
        r"(?:\b[%s][%s'’\-]*[ ,;:]+){3,}[%s]+" % (LATIN, LATIN, LATIN))

    def leftover(self, translated):
        """Сколько оригинала осталось в переводе непереведённым.

        Для письменностей, отличных от кириллицы и латиницы, — число знаков.
        Для латинских языков знак ничего не говорит (имена, бренды, аббревиатуры
        законно остаются латиницей), поэтому считаются фразы от четырёх
        латинских слов подряд: это уже непереведённое предложение.
        """
        if self.script_re is not None:
            return len(self.script_re.findall(translated))
        return len(self._LATIN_PHRASE.findall(translated))

    @property
    def leftover_name(self):
        return self.script_name or "фраз на латинице"

    def has_source_script(self, text):
        """Похоже ли, что строка осталась на языке оригинала (для русского
        варианта термина, пришедшего от агента)."""
        if self.script_re is not None:
            return bool(self.script_re.search(text))
        return not re.search("[а-яёА-ЯЁ]", text)

    # --- заголовки --------------------------------------------------------

    def parse_number(self, head):
        """(номер, пометка) по заголовку главы. (None, причина) — номера нет."""
        for rx, note in self.nums:
            m = rx.search(head)
            if m:
                return int("".join(g for g in m.groups() if g)), note
        return None, "в заголовке нет номера главы"

    def merge_span(self, head):
        """(первый, последний) для склейки двух глав в одном заголовке."""
        m = self.merge_re.search(head)
        if not m:
            return None
        a, b = int(m.group(1)), int(m.group(2))
        return (a, b) if b > a else None

    def chapter_numbers(self, title):
        """Все номера глав, заявленные заголовком, по порядку."""
        span = self.merge_span(title)
        if span:
            return list(range(span[0], span[1] + 1)) if span[1] - span[0] < 5 \
                else [span[0], span[1]]
        n, _ = self.parse_number(title)
        return [] if n is None else [n]

    # --- термины ----------------------------------------------------------

    def term_pattern(self, lo=2, hi=8):
        """Регэксп-фрагмент «термин оригинала» для разбора пар термин=перевод."""
        if self.extract == "ngram" and self.code != "th":
            return "[%s]{%d,%d}" % (self.extract_chars, lo, hi)
        c = self.extract_chars
        return r"[%s][%s'’·\- ]{0,38}[%s]|[%s]{2}" % (c, c, c, c)

    def iter_candidates(self, text):
        """Кандидаты в термины из сырого текста — на вход частотному отбору."""
        if self.code in ("ko", "ja") and self.morph.backend:
            # Морфоразбор точнее и n-грамм, и снятия частиц по списку.
            for term in self.morph.nouns(text):
                if len(term) >= 2:
                    yield term
        elif self.extract == "ngram":
            if self.code == "ja":
                for run in re.findall("[゠-ヺー]{3,}", text):
                    yield run
                runs = re.findall("[%s]+" % HAN_CORE, text)
            else:
                runs = re.findall("[%s]+" % self.extract_chars, text)
            for run in runs:
                for length in self.ngrams:
                    for i in range(len(run) - length + 1):
                        yield run[i:i + length]
        elif self.extract == "words":
            for word in re.findall("[%s]{2,}" % self.extract_chars, text):
                if self.code == "ko":
                    for p in KO_PARTICLES:
                        if word.endswith(p) and len(word) - len(p) >= 2:
                            word = word[:-len(p)]
                            break
                yield word
        else:
            for term in self._capitalized(text):
                # Одно слово — в словарную форму: Wächters и Wächter одно и то же.
                yield self.morph.lemma(term) if " " not in term else term

    _TOKEN = re.compile(r"[^\W\d_]+(?:['’\-][^\W\d_]+)*")

    def _capitalized(self, text):
        """Цепочки слов с заглавной не в начале предложения: «Black Tower»."""
        run, prev_end = [], None
        for m in self._TOKEN.finditer(text):
            tok = m.group(0)
            gap = text[prev_end:m.start()] if prev_end is not None else "\n"
            cap = tok[0].isupper() and len(tok) > 1
            if run and (gap != " " or not cap or len(run) >= 4):
                yield " ".join(run)
                run = []
            if cap:
                # Начало предложения: заглавная там ничего не значит.
                initial = not run and (gap == "\n" or re.search(r"[.!?…:\n\"“«‘(—–]\s*$", gap))
                if not initial:
                    run.append(tok)
            prev_end = m.end()
        if run:
            yield " ".join(run)

    def naming_res(self):
        """[(регэксп, в_кавычках)] — группа 1 есть объявленный автором термин."""
        if self.code == "zh":
            cjk = "[%s]" % HAN_CORE
            return [
                (re.compile(NAMING_ZH + r"\s*[‘'\"「『](" + cjk + r"{2,8})[’'\"」』]"), True),
                (re.compile(NAMING_ZH + r"\s*(" + cjk + r"{2,6})"), False),
            ]
        if self.code == "ja":
            return [(re.compile(
                r"[「『]([^」』\n]{2,20})[」』]\s*(?:と呼|と名付|と名づ|という名|と称)"), True)]
        if self.code == "ko":
            return [(re.compile(
                r"[‘'\"“「『<《]([^’'\"”」』>》\n]{2,20})[’'\"”」』>》]\s*"
                r"(?:이?라고|이?라는|이?라\s)\s*(?:불|부르|이름|칭|명명|하는|한다)"), True)]
        verbs = NAMING_ALPHA.get(self.code)
        if not verbs:
            return []
        lead = r"(?:%s)\s+(?:\w{1,5}\s+){0,2}?" % verbs
        return [
            (re.compile(lead + r"[“\"‘'«„]([^”\"’'»“\n]{2,40})[”\"’'»“]", re.I), True),
            (re.compile(r"(?:%s)\s+((?:[A-ZÀ-ÞА-ЯІЇЄҐ][\w'’\-]+ ?){1,4})" % verbs), False),
        ]

    def clean_term(self, term):
        """Обрезать захват до имени или отвергнуть (None)."""
        term = term.strip(" \t.,;:!?…\"'“”‘’«»")
        if self.code == "zh":
            # 的 и 了 отмечают ровно то место, где имя кончилось.
            for stop in ("的", "了"):
                if stop in term:
                    term = term.split(stop, 1)[0]
            for g in GENERIC_ZH:
                if term == g or term.startswith(g) or term.endswith(g):
                    return None
        if self.extract == "capitalized":
            # «the Ashen Maw» -> «Ashen Maw»: артикль в глоссарий не идёт.
            term = re.sub(r"^(?:the|an?|el|la|los|las|le|les|der|die|das|o|os|as|il|lo|gli|het|de)\s+",
                          "", term, flags=re.I)
        return term if len(term) >= 2 else None

    # --- пунктуация -------------------------------------------------------

    def sentence_split_re(self):
        if self.end is None:
            return re.compile(r"(?<=\n)")
        marks = "".join(ch for ch in "。！？!?….؟۔।" if ch in self.end)
        return re.compile("(?<=[%s])" % re.escape(marks))


_ALPHA = dict(ratio=(0.65, 1.7), short_para=40)

PROFILES = {p.code: p for p in (
    Profile("zh", "китайский", "с китайского", "китайской",
            script=HAN, script_name="китайских иероглифов", max_leftover=2,
            ratio=(2.0, 4.5), short_para=12, calibrated=True,
            extract="ngram", extract_chars=HAN_CORE, nums=NUM_ZH, merge=MERGE_ZH,
            end=END_CJK, open_=OPEN_CJK,
            transcription="система Палладия (пиньинь → кириллица): 林 → Линь, 張偉 → Чжан Вэй"),
    Profile("ja", "японский", "с японского", "японской",
            script=KANA + HAN, script_name="знаков каны и иероглифов", max_leftover=2,
            ratio=(1.2, 3.4), short_para=16,
            extract="ngram", extract_chars=HAN_CORE, ngrams=(2, 3, 4),
            nums=NUM_JA, merge=MERGE_JA, end=END_CJK, open_=OPEN_CJK,
            transcription="система Поливанова: し → си, ち → ти, つ → цу; 田中 → Танака"),
    Profile("ko", "корейский", "с корейского", "корейской",
            script=HANGUL + HAN, script_name="знаков хангыля", max_leftover=2,
            ratio=(1.0, 3.0), short_para=20,
            extract="words", extract_chars=HANGUL, nums=NUM_KO, merge=MERGE_KO,
            transcription="система Концевича: 김 → Ким, 박지훈 → Пак Чихун "
                          "(фамилия отдельно, имя слитно)"),
    Profile("en", "английский", "с английского", "английской",
            transcription="практическая транскрипция: John → Джон, Smith → Смит", **_ALPHA),
    Profile("es", "испанский", "с испанского", "испанской", **_ALPHA),
    Profile("fr", "французский", "с французского", "французской", **_ALPHA),
    Profile("de", "немецкий", "с немецкого", "немецкой", **_ALPHA),
    Profile("pt", "португальский", "с португальского", "португальской", **_ALPHA),
    Profile("it", "итальянский", "с итальянского", "итальянской", **_ALPHA),
    Profile("nl", "нидерландский", "с нидерландского", "нидерландской", **_ALPHA),
    Profile("pl", "польский", "с польского", "польской", **_ALPHA),
    Profile("tr", "турецкий", "с турецкого", "турецкой", **_ALPHA),
    Profile("vi", "вьетнамский", "с вьетнамского", "вьетнамской", **_ALPHA),
    Profile("id", "индонезийский", "с индонезийского", "индонезийской", **_ALPHA),
    Profile("uk", "украинский", "с украинского", "украинской",
            script="іїєґІЇЄҐ", script_name="украинских букв (і, ї, є, ґ)", max_leftover=3,
            ratio=(0.75, 1.4), short_para=40, extract="capitalized",
            extract_chars="А-Яа-яІЇЄҐіїєґ'’"),
    Profile("th", "тайский", "с тайского", "тайской",
            script="฀-๿", script_name="тайских знаков", max_leftover=2,
            ratio=(0.7, 2.4), short_para=30,
            extract="ngram", ngrams=(3, 4, 5, 6), end=None),
    Profile("ar", "арабский", "с арабского", "арабской",
            script="؀-ۿݐ-ݿ", script_name="арабских букв", max_leftover=2,
            ratio=(0.8, 2.3), short_para=30, extract="words"),
    Profile("fa", "персидский", "с персидского", "персидской",
            script="؀-ۿݐ-ݿ", script_name="знаков арабского письма", max_leftover=2,
            ratio=(0.8, 2.3), short_para=30, extract="words"),
    Profile("he", "иврит", "с иврита", "ивритоязычной",
            script="֐-׿", script_name="еврейских букв", max_leftover=2,
            ratio=(0.8, 2.4), short_para=30, extract="words"),
    Profile("hi", "хинди", "с хинди", "хиндиязычной",
            script="ऀ-ॿ", script_name="знаков деванагари", max_leftover=2,
            ratio=(0.7, 2.2), short_para=30, extract="words"),
)}

DEFAULT_TRANSCRIPTION = ("практическая транскрипция, принятая для этого языка; "
                         "устоявшиеся русские формы имён сохраняются")


def _book_field(label):
    try:
        with open(BOOK_FILE, encoding="utf-8") as f:
            book = f.read()
    except OSError:
        return ""
    m = re.search(r"^-\s*%s[^:\n]*:[ \t]*(.*)$" % label, book, re.M)
    return m.group(1).strip() if m else ""


def load():
    """Действующий профиль: TRANSLATOR_SRC_LANG, иначе BOOK.md, иначе zh."""
    code = (os.environ.get("TRANSLATOR_SRC_LANG") or _book_field("Язык оригинала")
            or "zh").strip().lower()[:2]
    if code in PROFILES:
        prof = PROFILES[code]
    else:
        prof = Profile(code, "язык «%s»" % code, "с языка оригинала", "иноязычной", **_ALPHA)
    if not prof.transcription:
        prof.transcription = DEFAULT_TRANSCRIPTION
    m = re.match(r"\s*(\d+(?:[.,]\d+)?)\s*[-–—]\s*(\d+(?:[.,]\d+)?)", _book_field("Норма объёма"))
    if m:
        lo, hi = (float(x.replace(",", ".")) for x in m.groups())
        if 0 < lo < hi:
            prof.min_ratio, prof.max_ratio, prof.calibrated = lo, hi, True
    return prof


LANG = load()


def _measure():
    from common import CHAPTERS_DIR, chapter_path, read_text, ru_path
    ratios = []
    for name in sorted(os.listdir(CHAPTERS_DIR)) if os.path.isdir(CHAPTERS_DIR) else []:
        m = re.fullmatch(r"chapter_(\d+)\.txt", name)
        if m and os.path.exists(ru_path(int(m.group(1)))):
            idx = int(m.group(1))
            src, ru = read_text(chapter_path(idx)), read_text(ru_path(idx))
            if src.strip() and ru.strip():
                ratios.append(len(ru) / len(src))
    if len(ratios) < 5:
        raise SystemExit("Готовых пар оригинал-перевод: %d. Нужно хотя бы 5 "
                         "(лучше 20+), чтобы замер что-то значил." % len(ratios))
    ratios.sort()

    def pct(p):
        return ratios[min(len(ratios) - 1, int(p * len(ratios)))]

    lo, hi = round(ratios[0] * 0.75, 2), round(ratios[-1] * 1.3, 2)
    print("Пар: %d | мин %.2f | 5%% %.2f | медиана %.2f | 95%% %.2f | макс %.2f"
          % (len(ratios), ratios[0], pct(0.05), pct(0.5), pct(0.95), ratios[-1]))
    print("Действующая норма: %.2f-%.2f" % (LANG.min_ratio, LANG.max_ratio))
    print("Предлагаемая строка для BOOK.md (секция «Название»):")
    print("    - Норма объёма (мин-макс): %s-%s" % (lo, hi))
    print("Замер честен, только если эти главы вычитаны человеком: норма, снятая "
          "с пересказов, узаконит пересказы.")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if "--measure" in sys.argv[1:]:
        return _measure()
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print(__doc__)
        return
    p = LANG
    print("Язык оригинала : %s (%s)" % (p.name, p.code))
    print("Норма объёма   : %.2f-%.2f%s" % (
        p.min_ratio, p.max_ratio,
        "" if p.calibrated else "  <- ОЦЕНКА, откалибруй: python lang.py --measure"))
    print("Остатки        : не более %d %s" % (p.max_leftover, p.leftover_name))
    print("Кандидаты      : %s" % {"ngram": "n-граммы знаков", "words": "слова",
                                   "capitalized": "слова с заглавной"}[p.extract])
    print("Транскрипция   : %s" % p.transcription)
    if p.morph.backend:
        print("Морфология     : %s" % p.morph.backend)
    elif p.morph.wanted:
        print("Морфология     : нет — поставь: pip install %s (термины в косвенных "
              "формах будут находиться точнее)" % p.morph.wanted)
    else:
        print("Морфология     : этому языку не нужна или лёгкой библиотеки нет")
    print("Доступные коды : %s" % " ".join(sorted(PROFILES)))


if __name__ == "__main__":
    main()
