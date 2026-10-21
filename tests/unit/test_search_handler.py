"""Модульні тести сценаріїв пошуку — без жодного сховища."""

from __future__ import annotations

import pytest

from app.application.dto import SearchQueryDTO
from app.application.ports import SearchUseCase
from app.application.queries import (
    CachedSearchHandler,
    SearchAdventuresHandler,
    SearchEncountersHandler,
    SearchMonstersHandler,
)
from app.domain.services import QueryNormalizer
from tests.unit.fakes import (
    FakeCacheStore,
    FakeMetricsSink,
    FakeSearchIndex,
    InMemoryAdventureRepository,
)

HANDLERS = (SearchAdventuresHandler, SearchMonstersHandler, SearchEncountersHandler)


def build(handler_type=SearchAdventuresHandler, **overrides) -> CachedSearchHandler:
    return handler_type(
        index=overrides.get("index", FakeSearchIndex()),
        repository=overrides.get("repository", InMemoryAdventureRepository()),
        cache=overrides.get("cache", FakeCacheStore()),
        metrics=overrides.get("metrics", FakeMetricsSink()),
        normalizer=overrides.get("normalizer", QueryNormalizer()),
    )


@pytest.mark.parametrize("handler_type", HANDLERS)
def test_search_handlers_build_with_fakes_only(handler_type) -> None:
    """Обробник створюється без підключення до баз даних."""
    assert isinstance(build(handler_type), SearchUseCase)


@pytest.mark.parametrize("handler_type", HANDLERS)
def test_search_handlers_share_template(handler_type) -> None:
    """Порядок кроків задано один раз у базовому класі."""
    assert issubclass(handler_type, CachedSearchHandler)
    assert "handle" not in handler_type.__dict__


def test_search_kinds_are_distinct() -> None:
    """Кожен обробник шукає свій вид документів і має свій тип фільтрів."""
    assert len({h.KIND for h in HANDLERS}) == len(HANDLERS)
    assert len({h.FILTERS_TYPE for h in HANDLERS}) == len(HANDLERS)


async def test_cache_hit_skips_storage() -> None:
    """При влучанні в кеш звернень до індексу й сховища немає."""
    index = FakeSearchIndex()
    repository = InMemoryAdventureRepository()
    handler = build(index=index, repository=repository)

    query = SearchQueryDTO(raw_text="cursed village")
    await handler.handle(query)
    await handler.handle(query)

    assert len(index.queries) == 1
    assert repository.calls.count("get_many") == 1


async def test_query_is_recorded() -> None:
    """Кожен запит потрапляє в журнал — живить панель моніторингу."""
    metrics = FakeMetricsSink()
    handler = build(metrics=metrics)
    await handler.handle(SearchQueryDTO(raw_text="swamp cult"))
    assert len(metrics.entries) == 1


def test_word_order_gives_same_normal_form() -> None:
    """«cursed village» і «village cursed» — одна нормальна форма (п. 4.7)."""
    normalizer = QueryNormalizer()
    assert normalizer.normalize("cursed village") == normalizer.normalize("village cursed")


# --- поведінка шаблону пошуку (п. 4.6–4.7) ---

from app.application.dto import AdventureCardDTO  # noqa: E402
from app.domain.errors import ValidationError  # noqa: E402
from app.domain.values import AdventureFilters, MonsterFilters  # noqa: E402
from tests.unit.fakes import BrokenCacheStore  # noqa: E402
from workers.storage_check import StorageCheck  # noqa: E402


def _stocked(ids_count: int = 2):
    keys = __import__("app.domain.services", fromlist=["ShardKeyService"]).ShardKeyService()
    adventures = [StorageCheck.sample(keys) for _ in range(ids_count)]
    return adventures, FakeSearchIndex([a.adventure_id for a in adventures]), InMemoryAdventureRepository(adventures)


async def test_engine_receives_surface_forms_and_cards_keep_index_order() -> None:
    adventures, index, repository = _stocked()
    result = await build(index=index, repository=repository).handle(SearchQueryDTO(raw_text="Cursed villages of the swamp"))
    assert index.queries == ["cursed swamp villages"]
    assert [c.adventure_id for c in result.items] == [a.adventure_id for a in adventures]
    assert isinstance(result.items[0], AdventureCardDTO) and result.normalized_query == "curs swamp villag"


async def test_result_lives_in_cache_for_60_seconds_even_when_empty() -> None:
    cache = FakeCacheStore()
    await build(cache=cache).handle(SearchQueryDTO(raw_text="clockwork submarine"))
    assert list(cache.ttl.values()) == [60]


async def test_empty_query_without_filters_skips_the_engine() -> None:
    index = FakeSearchIndex(["x"])
    metrics = FakeMetricsSink()
    result = await build(index=index, metrics=metrics).handle(SearchQueryDTO(raw_text="the of and"))
    assert index.queries == [] and result.total == 0 and len(metrics.entries) == 1


async def test_filters_without_text_go_to_the_engine_as_match_all() -> None:
    index = FakeSearchIndex()
    await build(index=index).handle(SearchQueryDTO(raw_text="", filters=AdventureFilters(level=3)))
    assert index.queries == [""] and index.specs[0] == AdventureFilters(level=3)


async def test_unavailable_cache_does_not_stop_the_search() -> None:
    adventures, index, repository = _stocked(1)
    result = await build(index=index, repository=repository, cache=BrokenCacheStore()).handle(
        SearchQueryDTO(raw_text="cursed village"))
    assert result.total == 1 and not result.cache_hit


async def test_filters_must_match_search_kind() -> None:
    with pytest.raises(ValidationError):
        await build().handle(SearchQueryDTO(raw_text="goblin", filters=MonsterFilters()))


def test_request_parameters_become_typed_filters() -> None:
    query = SearchQueryDTO.from_request({"kind": "adventures", "q": "heist", "level": "3", "tags": "one-shot,horror",
                                         "is_free": "true", "sort": "rating", "page": "2"})
    assert query.filters == AdventureFilters(level=3, tags=("horror", "one-shot"), is_free=True)
    assert query.page.number == 2 and query.sort.value == "rating"
    with pytest.raises(ValidationError):
        SearchQueryDTO.from_request({"kind": "adventures", "level": "three"})


async def test_log_line_matches_table_4_17(caplog) -> None:
    """Формат запису INFO — як у прикладі таблиці 4.17."""
    import logging
    import re

    handler = build(index=FakeSearchIndex(ids=[f"a{i}" for i in range(37)]))
    with caplog.at_level(logging.INFO, logger="search"):
        await handler.handle(SearchQueryDTO.from_request({"kind": "adventures", "q": "cursed village swamp cult"}))
    line = next(r.getMessage() for r in caplog.records if r.name == "search")
    assert re.fullmatch(r"adventures «cursed village swamp cult» — FOUND, 37 результатів, \d+ мс, кеш: ні", line)
