"""Нормалізація пошукового запиту (п. 4.7, таблиця 4.13).

Розміщено в доменному шарі, а не в адаптері кешу: зведення словоформ до
основ і впорядкування слів — правило предметної області, а не деталь
зберігання (п. 3.8.5). Стемер Портера реалізовано тут же на стандартній
бібліотеці, тож правило залежностей не порушується (п. 3.2).
"""

from __future__ import annotations

import hashlib
import json
import unicodedata

from app.domain.values import DocumentKind, Page, SearchSpecification, SortOrder

#: Список стоп-слів _english_ пошукового рушія (аналізатор english) — 33 слова
ENGLISH_STOP_WORDS: frozenset[str] = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "if", "in",
        "into", "is", "it", "no", "not", "of", "on", "or", "such", "that", "the",
        "their", "then", "there", "these", "they", "this", "to", "was", "will", "with",
    }
)

_POSSESSIVE = ("'s", "’s")

# Межі слів — за правилами Unicode (UAX #29), як у стандартному токенізаторі
# аналізатора english: слово складається з літер і цифр, а один розділовий
# знак між ними слова не розриває, якщо стоїть між двома літерами
# (re:zero, x.y.z, village's) або двома цифрами (3.5, 1,000). Тому «3.5e» —
# одне слово і в запиті, і в індексі. Знаки перелічено для тексту після NFKC.
_MID_LETTER = frozenset(":·‧״")
_MID_NUM = frozenset(",;٬،؍߸⁄")
_MID_NUM_LET = frozenset(".'’‘")
_EXTEND_NUM_LET = frozenset("_‿⁀⁔")
_JOINS_LETTERS = _MID_LETTER | _MID_NUM_LET
_JOINS_DIGITS = _MID_NUM | _MID_NUM_LET
#: Ієрогліфи й хірагана — кожен знак окремим словом; катакана — суцільно
_SINGLE_RANGES = ((0x3005, 0x3007), (0x3021, 0x3029), (0x3038, 0x303B), (0x3040, 0x309A), (0x3400, 0x4DBF),
                  (0x4E00, 0x9FFF), (0xF900, 0xFAFF), (0x20000, 0x323AF))
_KATAKANA_RANGES = ((0x309B, 0x309C), (0x30A0, 0x30FA), (0x30FC, 0x30FF), (0x31F0, 0x31FF), (0x32D0, 0x32FE),
                    (0x3300, 0x3357), (0xFF66, 0xFF9D))
#: Писемності Південно-Східної Азії (тайська, лаоська, бірманська, кхмерська
#: тощо): суцільний відрізок їхніх літер і знаків — одне слово
_SOUTHEAST_ASIAN_RANGES = ((0x0E00, 0x0EFF), (0x1000, 0x109F), (0x1780, 0x17FF), (0x1950, 0x19DF),
                           (0x1A20, 0x1AAF), (0xA9E0, 0xA9FF), (0xAA60, 0xAADF))


def _char_class(ch: str) -> str:
    """Клас символу для меж слів: L — літера, N — цифра, K — катакана,
    A — писемність Південно-Східної Азії, E — з'єднувальний _, X — знак, що
    приєднується до попереднього (діакритика, невидимі символи форматування),
    S — окреме слово з одного знака, M — розділовий знак, що може стояти
    всередині слова, порожній — роздільник. AX — знак писемностей класу A:
    приєднується до попередньої літери, цифри чи знака слова, а інакше
    починає слово класу A."""
    code = ord(ch)
    if any(low <= code <= high for low, high in _SINGLE_RANGES):
        return "S"
    if any(low <= code <= high for low, high in _KATAKANA_RANGES):
        return "K"
    category = unicodedata.category(ch)
    if category[0] in "LM" and any(low <= code <= high for low, high in _SOUTHEAST_ASIAN_RANGES):
        return "A" if category[0] == "L" else "AX"
    if category[0] == "L" or category == "Nl":
        return "L"
    if category == "Nd":
        return "N"
    if category in ("Mn", "Mc", "Me") or (category == "Cf" and ch != "\u200b"):
        return "X"
    if ch in _EXTEND_NUM_LET:
        return "E"
    if ch in _JOINS_LETTERS or ch in _JOINS_DIGITS:
        return "M"
    return ""


def _joined(previous: str, current: str) -> bool:
    """Чи продовжує символ класу current слово, останній символ якого має
    клас previous; знак класу S слова не продовжує й сам не продовжується."""
    if previous in ("L", "N") and current in ("L", "N"):
        return True
    if current == "E":
        return previous in ("L", "N", "K", "E")
    return (previous == current and current in ("K", "A")) or (previous == "E" and current in ("L", "N", "K"))


def split_words(text: str) -> list[str]:
    """Слова тексту за правилами меж слів (крок 2 таблиці 4.13).

    Слово береться найдовшим можливим. Якщо з поточного символу слова не
    утворити (лише «_» без літер і цифр) або розділовий знак не з'єднав
    двох частин, пошук слова починається з наступного символу — так само
    діє токенізатор рушія.
    """
    words: list[str] = []
    word: list[str] = []
    last = ""  # клас останнього символу слова без урахування класу X
    has_base = False  # чи є в слові літера, цифра або окремий знак
    start = 0  # позиція першого символу слова
    middle = -1  # позиція розділового знака, що чекає на продовження слова
    i, n = 0, len(text)

    def flush() -> int | None:
        """Завершує слово; повертає позицію для повторного пошуку, якщо слова не вийшло."""
        nonlocal last, has_base
        restart = None
        if has_base:
            words.append("".join(word))
        elif len(word) > 1:
            restart = start + 1
        word.clear()
        last, has_base = "", False
        return restart

    while i < n:
        ch = text[i]
        kind = _char_class(ch)
        if kind == "AX":
            kind = "X" if word else "A"
        if kind == "X":
            if word and middle < 0:
                word.append(ch)
            i += 1
            continue
        if middle >= 0:
            mark = text[middle]
            if kind == last and (
                (kind == "L" and mark in _JOINS_LETTERS) or (kind == "N" and mark in _JOINS_DIGITS)
            ):
                word.extend(text[middle:i])
                word.append(ch)
                middle = -1
                i += 1
                continue
            i, middle = middle + 1, -1
            flush()
            continue
        if kind in ("L", "N", "K", "A", "E", "S"):
            if word and (kind == "S" or not _joined(last, kind)):
                restart = flush()
                if restart is not None:
                    i = restart
                    continue
            if not word:
                start = i
            word.append(ch)
            last = kind
            has_base = has_base or kind != "E"
        elif kind == "M" and last in ("L", "N"):
            middle = i
        else:
            restart = flush()
            if restart is not None:
                i = restart
                continue
        i += 1
    if middle >= 0:
        i, middle = middle + 1, -1
        flush()
        return words + split_words(text[i:])
    restart = flush()
    if restart is not None:
        return words + split_words(text[restart:])
    return words


def _lower(text: str) -> str:
    """Нижній регістр посимвольно, як у фільтрі lowercase рушія: «İ» → «i»,
    кінцева «Σ» → «σ» без урахування контексту."""
    return "".join("i" if ch == "İ" else ch.lower() for ch in text)


class QueryNormalizer:
    """Доменна служба без стану.

    Кроки (таблиця 4.13): NFKC і нижній регістр → слова з літер і цифр за
    правилами меж слів, як у токенізаторі рушія → без присвійного 's і
    стоп-слів → основи за алгоритмом Портера → без повторів, за алфавітом. Результат — normalized_query, текстова
    частина ключа кешу; у рушій передаються вихідні форми слів, по одній на
    основу, тож однаковий ключ завжди означає однаковий запит до індексу.
    """

    #: Префікс простору ключів кешу
    CACHE_PREFIX = "search"
    #: Версія правил нормалізації: зі зміною правил ключі за старими правилами
    #: перестають запитуватися й зникають за часом життя
    RULES_VERSION = "v1"

    def normalize(self, raw: str) -> str:
        """Нормальна форма запиту: основи слів без повторів, за алфавітом.

        Запити «cursed village», «village cursed» і «cursed villages»
        дають однакову форму «curs villag».
        """
        return " ".join(sorted(self._stems(raw)))

    def search_text(self, raw: str) -> str:
        """Текст для пошукового індексу: вихідні форми слів після кроку 3,
        по одній на кожну основу, у порядку основ.

        З кількох форм однієї основи береться найменша за алфавітом, тож
        текст для рушія залежить лише від нормальної форми запиту.
        """
        stems = self._stems(raw)
        return " ".join(min(stems[stem]) for stem in sorted(stems))

    def cache_key(
        self,
        kind: DocumentKind,
        raw: str,
        spec: SearchSpecification,
        page: Page,
        sort: SortOrder,
    ) -> str:
        """Складений ключ: префікс із версією правил, вид пошуку й SHA-1 від
        нормалізованого тексту, канонічного запису фільтрів, номера сторінки
        й порядку сортування (п. 4.7), наприклад search:v1:adventures:<хеш>.

        Вид входить у ключ, щоб пошук пригод і пошук монстрів за тим
        самим текстом не перетиналися в кеші; сторінка й сортування —
        щоб різні сторінки й порядки видачі кешувалися окремо.
        """
        payload = json.dumps(
            [self.normalize(raw), spec.cache_fragment(), page.number, sort.value],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()
        return f"{self.CACHE_PREFIX}:{self.RULES_VERSION}:{kind.plural}:{digest}"

    def is_empty(self, raw: str) -> bool:
        """Чи не лишилося після нормалізації жодного слова."""
        return not self._stems(raw)

    # --- кроки нормалізації ---

    @staticmethod
    def _stems(raw: str) -> dict[str, set[str]]:
        """Основа → вихідні форми слів, що до неї зводяться."""
        text = _lower(unicodedata.normalize("NFKC", raw or ""))
        stems: dict[str, set[str]] = {}
        for word in split_words(text):
            if word.endswith(_POSSESSIVE):
                word = word[:-2]
            if not word or word in ENGLISH_STOP_WORDS:
                continue
            stems.setdefault(porter_stem(word), set()).add(word)
        return stems


# --------------------------------------------------------------------------
# Стемер Портера [Porter, 1980] у редакції автора алгоритму — тій самій, що
# реалізує фільтр porter_stem аналізатора english пошукового рушія: слова з
# одного-двох символів не змінюються, у кроці 2 «bli» → «ble» і «logi» → «log».
# Збіг основ зі стемером рушія перевіряє інтеграційний тест через _analyze.
# --------------------------------------------------------------------------

_STEP2 = {
    "a": (("ational", "ate"), ("tional", "tion")),
    "c": (("enci", "ence"), ("anci", "ance")),
    "e": (("izer", "ize"),),
    "l": (("bli", "ble"), ("alli", "al"), ("entli", "ent"), ("eli", "e"), ("ousli", "ous")),
    "o": (("ization", "ize"), ("ation", "ate"), ("ator", "ate")),
    "s": (("alism", "al"), ("iveness", "ive"), ("fulness", "ful"), ("ousness", "ous")),
    "t": (("aliti", "al"), ("iviti", "ive"), ("biliti", "ble")),
    "g": (("logi", "log"),),
}
_STEP3 = {
    "e": (("icate", "ic"), ("ative", ""), ("alize", "al")),
    "i": (("iciti", "ic"),),
    "l": (("ical", "ic"), ("ful", "")),
    "s": (("ness", ""),),
}
_STEP4 = {
    "a": ("al",),
    "c": ("ance", "ence"),
    "e": ("er",),
    "i": ("ic",),
    "l": ("able", "ible"),
    "n": ("ant", "ement", "ment", "ent"),
    "o": ("ion", "ou"),
    "s": ("ism",),
    "t": ("ate", "iti"),
    "u": ("ous",),
    "v": ("ive",),
    "z": ("ize",),
}


def _is_consonant(word: str, i: int) -> bool:
    ch = word[i]
    if ch in "aeiou":
        return False
    if ch == "y":
        return i == 0 or not _is_consonant(word, i - 1)
    return True


def _measure(stem: str) -> int:
    """Кількість послідовностей «голосні — приголосні» у формі [C](VC){m}[V]."""
    count, i, n = 0, 0, len(stem)
    while i < n and _is_consonant(stem, i):
        i += 1
    while i < n:
        while i < n and not _is_consonant(stem, i):
            i += 1
        if i >= n:
            break
        count += 1
        while i < n and _is_consonant(stem, i):
            i += 1
    return count


def _has_vowel(stem: str) -> bool:
    return any(not _is_consonant(stem, i) for i in range(len(stem)))


def _ends_double_consonant(word: str) -> bool:
    return len(word) >= 2 and word[-1] == word[-2] and _is_consonant(word, len(word) - 1)


def _ends_cvc(word: str) -> bool:
    """Закінчення «приголосна — голосна — приголосна», остання не w, x, y."""
    n = len(word)
    return (
        n >= 3
        and _is_consonant(word, n - 1)
        and not _is_consonant(word, n - 2)
        and _is_consonant(word, n - 3)
        and word[-1] not in "wxy"
    )


def _replace(word: str, suffix: str, replacement: str, min_measure: int) -> str | None:
    """Заміна суфікса, якщо він є; None — суфікса немає."""
    if not word.endswith(suffix):
        return None
    stem = word[: len(word) - len(suffix)]
    return stem + replacement if _measure(stem) > min_measure else word


def porter_stem(word: str) -> str:
    """Основа англійського слова за алгоритмом Портера."""
    if len(word) <= 2:
        return word

    # Крок 1a: множина
    if word.endswith("sses"):
        word = word[:-2]
    elif word.endswith("ies"):
        word = word[:-2]
    elif word.endswith("s") and not word.endswith("ss"):
        word = word[:-1]

    # Крок 1b: -eed, -ed, -ing
    if word.endswith("eed"):
        if _measure(word[:-3]) > 0:
            word = word[:-1]
    else:
        for suffix in ("ed", "ing"):
            if word.endswith(suffix) and _has_vowel(word[: -len(suffix)]):
                word = word[: -len(suffix)]
                if word.endswith(("at", "bl", "iz")):
                    word += "e"
                elif _ends_double_consonant(word) and word[-1] not in "lsz":
                    word = word[:-1]
                elif _measure(word) == 1 and _ends_cvc(word):
                    word += "e"
                break

    # Крок 1c: кінцеве y → i, якщо в основі є голосна
    if word.endswith("y") and _has_vowel(word[:-1]):
        word = word[:-1] + "i"

    # Крок 2: подвійні суфікси
    if len(word) > 1:
        for suffix, replacement in _STEP2.get(word[-2], ()):
            result = _replace(word, suffix, replacement, 0)
            if result is not None:
                word = result
                break

    # Крок 3: -ic-, -ful, -ness тощо
    for suffix, replacement in _STEP3.get(word[-1], ()):
        result = _replace(word, suffix, replacement, 0)
        if result is not None:
            word = result
            break

    # Крок 4: -ant, -ence тощо в контексті <c>vcvc<v>
    if len(word) > 1:
        for suffix in _STEP4.get(word[-2], ()):
            if not word.endswith(suffix):
                continue
            stem = word[: len(word) - len(suffix)]
            if suffix == "ion" and not stem.endswith(("s", "t")):
                continue
            if _measure(stem) > 1:
                word = stem
            break

    # Крок 5a: кінцеве e
    if word.endswith("e"):
        stem = word[:-1]
        measure = _measure(stem)
        if measure > 1 or (measure == 1 and not _ends_cvc(stem)):
            word = stem

    # Крок 5b: -ll → -l
    if word.endswith("ll") and _measure(word[:-1]) > 1:
        word = word[:-1]
    return word
