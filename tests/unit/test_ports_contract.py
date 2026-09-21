"""Перевірка, що заглушки задовольняють контракти портів.

Протоколи оголошені як `runtime_checkable`, тому відповідність
перевіряється без успадкування — це і є структурна типізація.
"""

from __future__ import annotations

from app.domain.ports import (
    AdventureRepository,
    CacheStore,
    CrudRepository,
    DocumentSource,
    EventPublisher,
    GraphRepository,
    MetricsSink,
    SearchIndex,
)
from tests.unit.fakes import (
    FakeCacheStore,
    FakeEventPublisher,
    FakeGraphRepository,
    FakeMetricsSink,
    FakeSearchIndex,
    InMemoryAdventureRepository,
    ListSource,
)


def test_in_memory_repository_satisfies_crud_port() -> None:
    assert isinstance(InMemoryAdventureRepository(), CrudRepository)


def test_in_memory_repository_satisfies_adventure_port() -> None:
    assert isinstance(InMemoryAdventureRepository(), AdventureRepository)


def test_fakes_satisfy_their_ports() -> None:
    assert isinstance(FakeSearchIndex(), SearchIndex)
    assert isinstance(FakeCacheStore(), CacheStore)
    assert isinstance(FakeMetricsSink(), MetricsSink)
    assert isinstance(FakeGraphRepository(), GraphRepository)
    assert isinstance(FakeEventPublisher(), EventPublisher)
    assert isinstance(ListSource([]), DocumentSource)


def test_all_sources_share_one_port() -> None:
    """Три різні джерела задовольняють один контракт (п. 4.3.3).

    Саме це дозволяє конвеєру не розрізняти походження документа,
    а логіці валідації та індексації існувати в єдиному екземплярі.
    """
    from pathlib import Path

    from app.infrastructure.sources import (
        CrawlerSource,
        CsvSource,
        GeneratorSource,
        MarkovTextBuilder,
        PolitenessPolicy,
    )

    sources = [
        CsvSource(Path("monsters.csv"), kind="monsters"),
        GeneratorSource([], MarkovTextBuilder(), [], []),
        CrawlerSource("https://example.org", "dnd", PolitenessPolicy()),
    ]
    for source in sources:
        assert isinstance(source, DocumentSource), type(source).__name__
