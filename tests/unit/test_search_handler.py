"""Модульні тести сценарію пошуку — без жодного сховища.

Тести написані наперед і поки не проходять: методи обробника
оголошені заглушками. Вони фіксують очікувану поведінку.
"""

from __future__ import annotations

import pytest

from app.application.dto import SearchQueryDTO
from app.application.queries import SearchAdventuresHandler
from app.domain.services import QueryNormalizer
from tests.unit.fakes import (
    FakeCacheStore,
    FakeMetricsSink,
    FakeSearchIndex,
    InMemoryAdventureRepository,
)


def build_handler(**overrides) -> SearchAdventuresHandler:
    return SearchAdventuresHandler(
        index=overrides.get("index", FakeSearchIndex()),
        cache=overrides.get("cache", FakeCacheStore()),
        repository=overrides.get("repository", InMemoryAdventureRepository()),
        metrics=overrides.get("metrics", FakeMetricsSink()),
        normalizer=overrides.get("normalizer", QueryNormalizer()),
    )


def test_handler_builds_with_fakes_only() -> None:
    """Обробник створюється без підключення до баз даних."""
    assert build_handler() is not None


@pytest.mark.xfail(reason="сценарій ще не реалізовано", raises=NotImplementedError)
@pytest.mark.asyncio
async def test_cache_hit_skips_storage() -> None:
    """При влучанні в кеш звернень до індексу й сховища немає."""
    index = FakeSearchIndex()
    repository = InMemoryAdventureRepository()
    handler = build_handler(index=index, repository=repository)

    query = SearchQueryDTO(raw_text="прокляте село")
    await handler.handle(query)
    await handler.handle(query)

    assert len(index.queries) == 1
    assert repository.calls.count("get_many") == 1


@pytest.mark.xfail(reason="сценарій ще не реалізовано", raises=NotImplementedError)
@pytest.mark.asyncio
async def test_query_is_recorded() -> None:
    """Кожен запит потрапляє в журнал — живить панель моніторингу."""
    metrics = FakeMetricsSink()
    handler = build_handler(metrics=metrics)
    await handler.handle(SearchQueryDTO(raw_text="культ у болотах"))
    assert len(metrics.entries) == 1


@pytest.mark.xfail(reason="службу ще не реалізовано", raises=NotImplementedError)
def test_word_order_gives_same_cache_key() -> None:
    """«прокляте село» і «село прокляте» — один ключ кешу."""
    normalizer = QueryNormalizer()
    assert normalizer.normalize("прокляте село") == normalizer.normalize("село прокляте")
