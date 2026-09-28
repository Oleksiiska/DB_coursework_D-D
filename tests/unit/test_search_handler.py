"""Модульні тести сценаріїв пошуку — без жодного сховища.

Тести поведінки написані наперед і позначені `xfail`: методи обробника
ще не реалізовано. Вони фіксують очікувану поведінку.
"""

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


@pytest.mark.xfail(reason="сценарій ще не реалізовано", raises=NotImplementedError)
async def test_cache_hit_skips_storage() -> None:
    """При влучанні в кеш звернень до індексу й сховища немає."""
    index = FakeSearchIndex()
    repository = InMemoryAdventureRepository()
    handler = build(index=index, repository=repository)

    query = SearchQueryDTO(raw_text="прокляте село")
    await handler.handle(query)
    await handler.handle(query)

    assert len(index.queries) == 1
    assert repository.calls.count("get_many") == 1


@pytest.mark.xfail(reason="сценарій ще не реалізовано", raises=NotImplementedError)
async def test_query_is_recorded() -> None:
    """Кожен запит потрапляє в журнал — живить панель моніторингу."""
    metrics = FakeMetricsSink()
    handler = build(metrics=metrics)
    await handler.handle(SearchQueryDTO(raw_text="культ у болотах"))
    assert len(metrics.entries) == 1


@pytest.mark.xfail(reason="службу ще не реалізовано", raises=NotImplementedError)
def test_word_order_gives_same_normal_form() -> None:
    """«прокляте село» і «село прокляте» — одна нормальна форма."""
    normalizer = QueryNormalizer()
    assert normalizer.normalize("прокляте село") == normalizer.normalize("село прокляте")
