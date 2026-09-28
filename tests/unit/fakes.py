"""Заглушки вихідних портів для модульних тестів.

Демонструють головну практичну властивість гексагональної архітектури:
сценарії використання перевіряються без жодного сховища.
"""

from __future__ import annotations

from typing import AsyncIterator, Generic, Sequence, TypeVar

from app.domain.entities import Adventure, SearchLogEntry
from app.domain.values import (
    AdventureFilters,
    DocumentKind,
    Page,
    QueuedBatch,
    SearchHits,
    SearchSpecification,
    SortOrder,
)

T = TypeVar("T")


class InMemoryRepository(Generic[T]):
    """Задовольняє контракт `CrudRepository` без успадкування."""

    def __init__(self, id_field: str, items: list[T] | None = None) -> None:
        self._id_field = id_field
        self._items: dict[str, T] = {getattr(i, id_field): i for i in (items or [])}
        self.calls: list[str] = []

    async def get(self, entity_id: str) -> T | None:
        self.calls.append("get")
        return self._items.get(entity_id)

    async def get_many(self, entity_ids: Sequence[str]) -> list[T]:
        self.calls.append("get_many")
        return [self._items[i] for i in entity_ids if i in self._items]

    async def add(self, entity: T) -> None:
        self._items[getattr(entity, self._id_field)] = entity

    async def add_many(self, entities: Sequence[T]) -> int:
        for entity in entities:
            await self.add(entity)
        return len(entities)

    async def update(self, entity: T) -> bool:
        key = getattr(entity, self._id_field)
        existed = key in self._items
        self._items[key] = entity
        return existed

    async def delete(self, entity_id: str) -> bool:
        return self._items.pop(entity_id, None) is not None

    async def count(self, spec: SearchSpecification | None = None) -> int:
        return len(self._items)


class InMemoryAdventureRepository(InMemoryRepository[Adventure]):
    """Задовольняє контракт `AdventureRepository`."""

    def __init__(self, items: list[Adventure] | None = None) -> None:
        super().__init__("adventure_id", items)

    async def find_by_filters(
        self, filters: AdventureFilters, page: Page, sort: SortOrder = SortOrder.RELEVANCE
    ) -> list[Adventure]:
        return list(self._items.values())

    async def find_by_author(self, author_id: str) -> list[Adventure]:
        return [a for a in self._items.values() if author_id in a.author_ids]

    async def find_chain(self, setting_id: str, level_from: int, level_to: int) -> list[Adventure]:
        return []


class FakeSearchIndex:
    def __init__(self, ids: list[str] | None = None) -> None:
        self._ids = tuple(ids or [])
        self.queries: list[str] = []

    async def search(self, text, spec, page, sort=SortOrder.RELEVANCE) -> SearchHits:
        self.queries.append(text)
        return SearchHits(ids=self._ids, total=len(self._ids))

    async def index_many(self, documents: Sequence[dict]) -> int:
        return len(documents)

    async def delete(self, document_id: str) -> bool:
        return True


class FakeCacheStore:
    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}

    async def get(self, key: str) -> bytes | None:
        return self.store.get(key)

    async def set(self, key: str, value: bytes, ttl_seconds: int | None = None) -> None:
        self.store[key] = value

    async def delete(self, key: str) -> bool:
        return self.store.pop(key, None) is not None

    async def incr(self, key: str, amount: int = 1) -> int:
        current = int(self.store.get(key, b"0")) + amount
        self.store[key] = str(current).encode()
        return current

    async def push_capped(self, key: str, value: bytes, max_length: int) -> None:
        pass


class FakeMetricsSink:
    def __init__(self) -> None:
        self.entries: list[SearchLogEntry] = []
        self.counters: dict[str, int] = {}

    async def record_query(self, entry: SearchLogEntry) -> None:
        self.entries.append(entry)

    async def inc(self, counter: str, amount: int = 1) -> None:
        self.counters[counter] = self.counters.get(counter, 0) + amount

    async def observe_latency(self, operation: str, milliseconds: int) -> None:
        pass

    async def snapshot(self) -> dict:
        return dict(self.counters)


class FakeGraphRepository:
    def __init__(self, similar: list[str] | None = None) -> None:
        self._similar = similar or []

    async def similar_adventures(self, adventure_id: str, limit: int = 10) -> list[str]:
        return self._similar[:limit]

    async def adventures_with_monster(self, monster_id: str) -> list[str]:
        return []

    async def co_authors(self, author_id: str) -> list[str]:
        return []

    async def campaign_chain(self, setting_id: str, level_from: int, level_to: int) -> list[str]:
        return []

    async def merge_nodes_and_edges(self, batch: Sequence[dict]) -> int:
        return len(batch)


class FakeQueue:
    """Задовольняє обидва порти черги: `EventPublisher` і `EventConsumer`."""

    def __init__(self) -> None:
        self.published: list[dict] = []
        self.acknowledged: list[str] = []

    async def publish(self, topic: str, batch: Sequence[dict]) -> None:
        self.published.extend(batch)

    async def queue_depth(self, topic: str) -> int:
        return len(self.published)

    async def consume(self, topic: str, batch_size: int = 500) -> AsyncIterator[QueuedBatch]:
        for number, start in enumerate(range(0, len(self.published), batch_size)):
            yield QueuedBatch(batch_id=str(number), documents=self.published[start : start + batch_size])

    async def acknowledge(self, topic: str, batch_id: str) -> None:
        self.acknowledged.append(batch_id)


class FakeDataQualityLog:
    def __init__(self) -> None:
        self.rejected_documents: list[tuple[dict, list[str]]] = []
        self.unknown_terms: list[tuple[str, str]] = []

    async def rejected(self, document: dict, reasons: list[str]) -> None:
        self.rejected_documents.append((document, reasons))

    async def unknown_term(self, vocabulary: str, term: str) -> None:
        self.unknown_terms.append((vocabulary, term))


class ListSource:
    """Джерело з наперед заданого переліку — для тестів конвеєра."""

    def __init__(self, documents: list[dict], name: str = "list") -> None:
        self._documents = documents
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    async def stream(self, batch_size: int = 500) -> AsyncIterator[list[dict]]:
        for start in range(0, len(self._documents), batch_size):
            yield self._documents[start : start + batch_size]

    async def set_rate(self, documents_per_second: int) -> None:
        pass


def repository_registry() -> dict[DocumentKind, InMemoryRepository]:
    """Реєстр репозиторіїв на кожен вид документа."""
    registry: dict[DocumentKind, InMemoryRepository] = {
        kind: InMemoryRepository(f"{kind.value}_id") for kind in DocumentKind
    }
    registry[DocumentKind.ADVENTURE] = InMemoryAdventureRepository()
    return registry
