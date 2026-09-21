"""Заглушки вихідних портів для модульних тестів.

Демонструють головну практичну властивість гексагональної архітектури:
сценарії використання перевіряються без жодного сховища.
"""

from __future__ import annotations

from typing import AsyncIterator, Sequence

from app.domain.entities import Adventure, SearchLogEntry
from app.domain.values import Filters, Page, ShardKey, Snippet


class InMemoryAdventureRepository:
    """Задовольняє контракт `AdventureRepository` без успадкування."""

    def __init__(self, items: list[Adventure] | None = None) -> None:
        self._items: dict[str, Adventure] = {a.adventure_id: a for a in (items or [])}
        self.calls: list[str] = []

    async def get(self, entity_id: str) -> Adventure | None:
        self.calls.append("get")
        return self._items.get(entity_id)

    async def get_many(self, entity_ids: Sequence[str]) -> list[Adventure]:
        self.calls.append("get_many")
        return [self._items[i] for i in entity_ids if i in self._items]

    async def add(self, entity: Adventure) -> None:
        self._items[entity.adventure_id] = entity

    async def add_many(self, entities: Sequence[Adventure]) -> int:
        for entity in entities:
            self._items[entity.adventure_id] = entity
        return len(entities)

    async def update(self, entity: Adventure) -> bool:
        existed = entity.adventure_id in self._items
        self._items[entity.adventure_id] = entity
        return existed

    async def delete(self, entity_id: str) -> bool:
        return self._items.pop(entity_id, None) is not None

    async def count(self, filters: Filters | None = None) -> int:
        return len(self._items)

    async def find_by_filters(self, filters: Filters, page: Page) -> list[Adventure]:
        return list(self._items.values())

    async def find_by_author(self, author_id: str) -> list[Adventure]:
        return [a for a in self._items.values() if author_id in a.author_ids]

    async def find_chain(self, setting_id: str, level_from: int, level_to: int) -> list[Adventure]:
        return []


class FakeSearchIndex:
    def __init__(self, ids: list[str] | None = None) -> None:
        self._ids = ids or []
        self.queries: list[str] = []

    async def search(self, text, filters, page):
        self.queries.append(text)
        snippets: dict[str, list[Snippet]] = {}
        return self._ids, snippets, len(self._ids)

    async def index_many(self, documents: Sequence[dict]) -> int:
        return len(documents)

    async def delete(self, document_id: str) -> bool:
        return True


class FakeCacheStore:
    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.hits = 0
        self.misses = 0

    async def get(self, key: str) -> bytes | None:
        value = self.store.get(key)
        if value is None:
            self.misses += 1
        else:
            self.hits += 1
        return value

    async def set(self, key: str, value: bytes, ttl_seconds: int | None = None) -> None:
        self.store[key] = value

    async def delete(self, key: str) -> bool:
        return self.store.pop(key, None) is not None

    async def incr(self, key: str, amount: int = 1) -> int:
        current = int(self.store.get(key, b"0"))
        current += amount
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


class FakeEventPublisher:
    def __init__(self) -> None:
        self.published: list[dict] = []

    async def publish(self, topic: str, batch: Sequence[dict]) -> None:
        self.published.extend(batch)

    async def queue_depth(self, topic: str) -> int:
        return len(self.published)


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


class FixedShardKeyProvider:
    def shard_key(self, entity_id: str) -> ShardKey:
        return ShardKey(value=entity_id, shard_index=0)
