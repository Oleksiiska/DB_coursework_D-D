"""Перевірка, що реалізації задовольняють контракти портів.

Протоколи оголошені як `runtime_checkable`, тому відповідність
перевіряється без успадкування — це і є структурна типізація.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.domain.ports import (
    AdventureRepository,
    CacheStore,
    CrudRepository,
    DataQualityLog,
    DocumentSource,
    EventConsumer,
    EventPublisher,
    GraphRepository,
    MetricsSink,
    SearchIndex,
)
from tests.unit.fakes import (
    FakeCacheStore,
    FakeDataQualityLog,
    FakeGraphRepository,
    FakeMetricsSink,
    FakeQueue,
    FakeSearchIndex,
    InMemoryAdventureRepository,
    InMemoryRepository,
    ListSource,
)


def test_fakes_satisfy_their_ports() -> None:
    assert isinstance(InMemoryRepository("id"), CrudRepository)
    assert isinstance(InMemoryAdventureRepository(), AdventureRepository)
    assert isinstance(FakeSearchIndex(), SearchIndex)
    assert isinstance(FakeCacheStore(), CacheStore)
    assert isinstance(FakeMetricsSink(), MetricsSink)
    assert isinstance(FakeGraphRepository(), GraphRepository)
    assert isinstance(FakeDataQualityLog(), DataQualityLog)
    assert isinstance(ListSource([]), DocumentSource)


def test_generic_crud_repository_is_not_an_adventure_repository() -> None:
    """Узагальнений репозиторій не має спеціалізованих запитів пригод."""
    assert not isinstance(InMemoryRepository("id"), AdventureRepository)


def test_all_sources_share_one_port() -> None:
    """Три різні джерела задовольняють один контракт."""
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


def test_queue_adapter_implements_both_queue_ports() -> None:
    """Один адаптер — два вузькі контракти (розділення інтерфейсів)."""
    from app.infrastructure.messaging import QueueAdapter

    adapter = QueueAdapter(client=None)
    assert isinstance(adapter, EventPublisher)
    assert isinstance(adapter, EventConsumer)
    assert isinstance(FakeQueue(), EventPublisher) and isinstance(FakeQueue(), EventConsumer)


def test_infrastructure_adapters_satisfy_ports() -> None:
    from app.infrastructure.observability import FileDataQualityLog
    from app.infrastructure.persistence import (
        DataclassDocumentMapper,
        GraphAdapter,
        MongoAdventureRepository,
        MongoCrudRepository,
        RedisCacheAdapter,
        RedisMetricsAdapter,
        SearchIndexAdapter,
    )

    from app.domain.entities import Adventure, Author

    client: Any = None
    documents: Any = {"dnd": {"authors": object(), "adventures": object()}}
    authors = DataclassDocumentMapper(Author, ("author_id",))
    adventures = DataclassDocumentMapper(Adventure, ("adventure_id",))
    assert isinstance(MongoCrudRepository(documents, "authors", authors), CrudRepository)
    assert isinstance(MongoAdventureRepository(documents, "adventures", adventures), AdventureRepository)
    assert isinstance(SearchIndexAdapter(client, "adventures_idx"), SearchIndex)
    assert isinstance(GraphAdapter(client), GraphRepository)
    assert isinstance(RedisCacheAdapter(client), CacheStore)
    assert isinstance(RedisMetricsAdapter(client), MetricsSink)
    assert isinstance(FileDataQualityLog(Path("a"), Path("b")), DataQualityLog)
