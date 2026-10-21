"""Нормалізація пошукового запиту й ключ кешу (п. 4.7, таблиця 4.13)."""

from __future__ import annotations

import re

import pytest

from app.domain.services import QueryNormalizer
from app.domain.services.query_normalizer import ENGLISH_STOP_WORDS, porter_stem, split_words
from app.domain.values import AdventureFilters, DocumentKind, MonsterFilters, Page, SortOrder

N = QueryNormalizer()


def test_table_4_13_example() -> None:
    """Кроки 1–5 для «Cursed villages: the CULT of swamp!»."""
    raw = "Cursed villages: the CULT of swamp!"
    assert N.normalize(raw) == "cult curs swamp villag"
    # у рушій — вихідні форми, по одній на основу, у порядку основ
    assert N.search_text(raw) == "cult cursed swamp villages"


def test_word_order_and_word_forms_give_one_cache_key() -> None:
    keys = {N.cache_key(DocumentKind.ADVENTURE, q, AdventureFilters(), Page(), SortOrder.RELEVANCE)
            for q in ("cursed village", "village cursed", "cursed villages", "  CURSED   Village ")}
    assert len(keys) == 1
    (key,) = keys
    assert re.fullmatch(r"search:v1:adventures:[0-9a-f]{40}", key)


def test_same_key_means_same_engine_query() -> None:
    assert N.search_text("villages village cursed") == N.search_text("cursed village villages")


@pytest.mark.parametrize("change", [
    {"page": Page(number=2)},
    {"sort": SortOrder.RATING},
    {"spec": AdventureFilters(level=3)},
    {"kind": DocumentKind.MONSTER, "spec": MonsterFilters()},
])
def test_key_covers_kind_filters_page_and_sort(change) -> None:
    base = {"kind": DocumentKind.ADVENTURE, "raw": "cursed village", "spec": AdventureFilters(),
            "page": Page(), "sort": SortOrder.RELEVANCE}
    assert N.cache_key(**base) != N.cache_key(**{**base, **change})


def test_filter_lists_are_canonical() -> None:
    first = AdventureFilters(tags=("horror", "one-shot"), level=3)
    second = AdventureFilters(level=3, tags=("one-shot", "horror"))
    assert first.cache_fragment() == second.cache_fragment()


def test_stop_words_and_possessive() -> None:
    assert len(ENGLISH_STOP_WORDS) == 33
    assert N.normalize("the dragon's lair") == N.normalize("dragon lair")
    assert N.is_empty("The of AND to") and N.is_empty("") and not N.is_empty("goblin")


def test_other_languages_are_tokenized_but_not_stemmed() -> None:
    """Обмеження роботи (п. 4.6): для інших мов розбиття працює, зведення до основ — ні."""
    assert N.normalize("Прокляте село") == "прокляте село"


@pytest.mark.parametrize("word, stem", [
    ("caresses", "caress"), ("ponies", "poni"), ("cats", "cat"), ("feed", "feed"), ("agreed", "agre"),
    ("motoring", "motor"), ("hopping", "hop"), ("filing", "file"), ("happy", "happi"), ("relational", "relat"),
    ("digitizer", "digit"), ("hopefulness", "hope"), ("electrical", "electr"), ("adjustment", "adjust"),
    ("adoption", "adopt"), ("controll", "control"), ("generalization", "gener"), ("archaeology", "archaeolog"),
    ("is", "is"), ("goblins", "goblin"), ("villages", "villag"),
])
def test_porter_stemmer_reference_words(word: str, stem: str) -> None:
    """Еталонні пари алгоритму Портера в редакції автора, яку реалізує
    фільтр porter_stem рушія (у тому числі «logi» → «log»)."""
    assert porter_stem(word) == stem


#: Межі слів, звірені з токенізатором standard рушія через API _analyze
@pytest.mark.parametrize("text, words", [
    ("3.5e v2.0 1,000 1.5", ["3.5e", "v2.0", "1,000", "1.5"]),
    ("u.s.a. e.g. x.y.z", ["u.s.a", "e.g", "x.y.z"]),
    ("re:zero 10:30 a:1 a.1 1.a", ["re:zero", "10", "30", "a", "1", "a", "1", "1", "a"]),
    ("one-shot goblin/kobold orc,elf", ["one", "shot", "goblin", "kobold", "orc", "elf"]),
    ("snake_case _foo __init__ _ a_.b", ["snake_case", "_foo", "__init__", "a_", "b"]),
    ("dragon’s cats’ 'quoted' rock'n'roll a''b", ["dragon’s", "cats", "quoted", "rock'n'roll", "a", "b"]),
    ("a\u200bb a\u00adb a\u2060b", ["a", "b", "a\u00adb", "a\u2060b"]),
    ("日本語テキスト ひらがな", ["日", "本", "語", "テキスト", "ひ", "ら", "が", "な"]),
])
def test_word_boundaries_follow_the_engine_tokenizer(text: str, words: list[str]) -> None:
    assert split_words(text) == words


def test_dotted_numbers_and_abbreviations_stay_whole() -> None:
    """«3.5e» — одне слово і в запиті, і в індексі, тож запит його знаходить."""
    assert N.normalize("D&D 3.5e: Tomb of Horrors") == "3.5e d horror tomb"
    assert N.search_text("D&D 3.5e: Tomb of Horrors") == "3.5e d horrors tomb"
    assert N.normalize("U.S.A.") == "u.s.a"


def test_lowercase_is_per_character_like_the_engine() -> None:
    assert N.normalize("İstanbul") == "istanbul"
    assert N.normalize("ΟΔΟΣ") == "οδοσ"
