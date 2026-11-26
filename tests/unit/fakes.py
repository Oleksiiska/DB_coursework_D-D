"""Заглушки вихідних портів для модульних тестів.

Демонструють головну практичну властивість гексагональної архітектури:
сценарії використання перевіряються без жодного сховища.
"""

from __future__ import annotations

import json
import time
from typing import AsyncIterator, Generic, Sequence, TypeVar

from app.domain.entities import Adventure, SearchLogEntry
from app.domain.services import NicheAggregator, ShardKeyService
from app.domain.values import (
    CatalogDimension,
    CatalogScope,
    DocumentKind,
    MergedAggregate,
    QueuedBatch,
    ReadSnapshot,
    SearchHits,
    SearchSpecification,
    SortOrder,
    SourceBatch,
    SourceStats,
    ZoneBounds,
    ZonePartial,
)

T = TypeVar("T")


class InMemoryRepository(Generic[T]):
    """Задовольняє контракт `CrudRepository` без успадкування."""

    def __init__(self, id_field: str, items: list[T] | None = None) -> None:
        self._id_field = id_field
        self._items: dict[str, T] = {self._key(i): i for i in (items or [])}
        self.calls: list[str] = []

    def _key(self, entity: T) -> str:
        return ":".join(str(getattr(entity, name)) for name in self._id_field.split("+"))

    async def get(self, entity_id: str) -> T | None:
        self.calls.append("get")
        return self._items.get(entity_id)

    async def get_many(self, entity_ids: Sequence[str]) -> list[T]:
        self.calls.append("get_many")
        return [self._items[i] for i in entity_ids if i in self._items]

    async def add(self, entity: T) -> None:
        self._items[self._key(entity)] = entity

    async def add_many(self, entities: Sequence[T]) -> int:
        for entity in entities:
            await self.add(entity)
        return len(entities)

    async def update(self, entity: T) -> bool:
        key = self._key(entity)
        existed = key in self._items
        self._items[key] = entity
        return existed

    async def delete(self, entity_id: str) -> bool:
        return self._items.pop(entity_id, None) is not None

    async def count(self, spec: SearchSpecification | None = None) -> int:
        if spec is None or spec.is_empty():
            return len(self._items)
        return sum(1 for item in self._items.values() if item.matches(spec))  # type: ignore[attr-defined]


class InMemoryAdventureRepository(InMemoryRepository[Adventure]):
    """Задовольняє контракт `AdventureRepository`.

    Аналітику ніш рахує еталонна доменна служба: зона — діапазон shard_key,
    шард зони — «shard<номер>». `failing_zones` імітує недоступні шарди.
    """

    def __init__(self, items: list[Adventure] | None = None, failing_zones: set[int] | None = None,
                 snapshot: str | None = "1732464000.1") -> None:
        super().__init__("adventure_id", items)
        self.failing_zones = set(failing_zones or ())
        self.snapshot_token = snapshot
        self.niche_calls: list[tuple] = []
        self._keys = ShardKeyService()
        self._aggregator = NicheAggregator()

    async def read_snapshot(self, zone: ZoneBounds | None = None) -> ReadSnapshot | None:
        if self.snapshot_token is None or (zone is not None and zone.index in self.failing_zones):
            return None
        return ReadSnapshot(token=self.snapshot_token)

    def _in_zone(self, adventure: Adventure, zone: ZoneBounds) -> bool:
        key = adventure.shard_key or self._keys.key_value(adventure.adventure_id)
        return (zone.lower is None or key >= zone.lower) and (zone.upper is None or key < zone.upper)

    async def niche_partial(self, zone: ZoneBounds, dimension: CatalogDimension, scope: CatalogScope,
                            snapshot: ReadSnapshot | None = None) -> ZonePartial:
        self.niche_calls.append(("partial", zone.index, dimension, scope, snapshot))
        if zone.index in self.failing_zones:
            raise ConnectionError(f"adventures: зона {zone.index + 1} ({zone.label}): shard unreachable (код 133)")
        groups, documents = self._aggregator.map(
            (a for a in self._items.values() if self._in_zone(a, zone)), dimension, scope)
        return ZonePartial(zone=zone, groups=groups, documents=documents, shard=f"shard{zone.index + 1}", elapsed_ms=5)

    async def niche_merged(self, dimension: CatalogDimension, scope: CatalogScope,
                           snapshot: ReadSnapshot | None = None) -> MergedAggregate:
        self.niche_calls.append(("merged", dimension, scope, snapshot))
        if self.failing_zones:
            raise ConnectionError("adventures: вбудована агрегація: shard unreachable (код 133)")
        groups, documents = self._aggregator.map(self._items.values(), dimension, scope)
        return MergedAggregate(groups=groups, documents=documents, elapsed_ms=7,
                               shards=("shard1", "shard2", "shard3"), shards_part=("$project", "$group"),
                               merger_part=("$mergeCursors", "$group"), merge_type="mongos")

    async def find_by_author(self, author_id: str, limit: int = 50) -> list[Adventure]:
        return [a for a in self._items.values() if author_id in a.author_ids][:limit]

    async def count_by_shard(self) -> dict[str, int]:
        return {"shard1": len(self._items)}

    async def author_stats(self, author_ids: Sequence[str]) -> dict[str, dict]:
        stats: dict[str, dict] = {}
        for author_id in author_ids:
            works = [a for a in self._items.values() if author_id in a.author_ids]
            if not works:
                continue
            ratings = [w.rating_avg for w in works if w.rating_avg is not None]
            years = [w.published_year for w in works if w.published_year is not None]
            stats[author_id] = {
                "adventures_count": len(works),
                "avg_rating": sum(ratings) / len(ratings) if ratings else None,
                "first_published_year": min(years) if years else None,
                "co_author_ids": sorted({a for w in works for a in w.author_ids} - {author_id}),
            }
        return stats


class FakeSearchIndex:
    def __init__(self, ids: list[str] | None = None, tag_counts: dict[str, int] | None = None) -> None:
        self._ids = tuple(ids or [])
        self._tag_counts = tag_counts or {}
        self.queries: list[str] = []
        self.specs: list[SearchSpecification] = []
        self.indexed: list[dict] = []

    async def search(self, text, spec, page, sort=SortOrder.RELEVANCE) -> SearchHits:
        self.queries.append(text)
        self.specs.append(spec)
        return SearchHits(ids=self._ids, total=len(self._ids), tag_counts=dict(self._tag_counts))

    async def index_many(self, documents: Sequence[dict]) -> int:
        self.indexed.extend(documents)
        return len(documents)

    async def delete(self, document_id: str) -> bool:
        return True


class FakeCacheStore:
    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.ttl: dict[str, int | None] = {}
        self.members: dict[str, dict[str, float]] = {}

    async def get(self, key: str) -> bytes | None:
        return self.store.get(key)

    async def set(self, key: str, value: bytes, ttl_seconds: int | None = None) -> None:
        self.store[key] = value
        self.ttl[key] = ttl_seconds

    async def delete(self, key: str) -> bool:
        return self.store.pop(key, None) is not None

    async def incr(self, key: str, amount: int = 1) -> int:
        current = int(self.store.get(key, b"0")) + amount
        self.store[key] = str(current).encode()
        return current

    async def push_capped(self, key: str, value: bytes, max_length: int) -> None:
        pass

    async def heartbeat(self, group: str, member: str, window_seconds: int) -> int:
        now = time.time()
        members = self.members.setdefault(group, {})
        members[member] = now
        for name, seen in list(members.items()):
            if seen < now - window_seconds:
                del members[name]
        return len(members)


class BrokenCacheStore(FakeCacheStore):
    """Кеш, недоступний на кожному зверненні."""

    async def get(self, key: str) -> bytes | None:
        raise ConnectionError("cache is down")

    async def set(self, key: str, value: bytes, ttl_seconds: int | None = None) -> None:
        raise ConnectionError("cache is down")


class FakeMetricsSink:
    def __init__(self) -> None:
        self.entries: list[SearchLogEntry] = []
        self.counters: dict[str, int] = {}
        self.latencies: list[tuple[str, int]] = []

    async def record_query(self, entry: SearchLogEntry) -> None:
        self.entries.append(entry)

    async def inc(self, counter: str, amount: int = 1) -> None:
        self.counters[counter] = self.counters.get(counter, 0) + amount

    async def observe_latency(self, operation: str, milliseconds: int) -> None:
        self.latencies.append((operation, milliseconds))

    async def snapshot(self) -> dict:
        return {
            "counters": dict(self.counters),
            "rates": {},
            "recent_queries": [
                {"raw_query": e.raw_query, "kind": e.kind.value, "status": e.status.value,
                 "results_count": e.results_count, "latency_ms": e.latency_ms, "cache_hit": e.cache_hit,
                 "created_at": e.created_at.isoformat()}
                for e in reversed(self.entries)
            ],
            "latency": {},
        }


class FakeGraphRepository:
    def __init__(self, similar: list[str] | None = None, chain: list[str] | None = None) -> None:
        self._similar = similar or []
        self._chain = chain or []
        self.merged: list[dict] = []

    async def similar_adventures(self, adventure_id: str, limit: int = 10) -> list[str]:
        return self._similar[:limit]

    async def adventures_with_monster(self, monster_id: str, limit: int = 20) -> list[tuple[str, str]]:
        return []

    async def co_authors(self, author_id: str) -> list[str]:
        return []

    async def seen_together(self, monster_id: str, limit: int = 10) -> list[tuple[str, int]]:
        return []

    async def campaign_chain(
        self, setting_id: str, level_from: int, level_to: int, through: str | None = None
    ) -> list[str]:
        return list(self._chain)

    async def merge_nodes_and_edges(self, batch: Sequence[dict]) -> int:
        self.merged.extend(batch)
        return len(batch)


class FakeQueue:
    """Задовольняє обидва порти черги: `EventPublisher` і `EventConsumer`."""

    def __init__(self) -> None:
        self.published: list[dict] = []
        self.batches: list[list[dict]] = []
        self.acknowledged: list[str] = []
        self.depth = 0

    async def publish(self, topic: str, batch: Sequence[dict]) -> None:
        self.published.extend(batch)
        self.batches.append(list(batch))

    async def queue_depth(self, topic: str) -> int:
        return self.depth

    async def consume(self, topic: str, batch_size: int = 500) -> AsyncIterator[QueuedBatch]:
        for number, batch in enumerate(self.batches):
            yield QueuedBatch(batch_id=str(number), documents=json.loads(json.dumps(batch, default=str)))

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
        self.rates: list[float] = []

    @property
    def name(self) -> str:
        return self._name

    async def stream(self, batch_size: int = 500) -> AsyncIterator[SourceBatch]:
        for start in range(0, len(self._documents), batch_size):
            chunk = self._documents[start : start + batch_size]
            yield SourceBatch(chunk, SourceStats(requested=len(chunk), succeeded=len(chunk)))

    async def set_rate(self, documents_per_second: int) -> None:
        self.rates.append(documents_per_second)


def repository_registry() -> dict[DocumentKind, InMemoryRepository]:
    """Реєстр репозиторіїв на кожен вид документа."""
    registry: dict[DocumentKind, InMemoryRepository] = {
        kind: InMemoryRepository(f"{kind.value}_id") for kind in DocumentKind
    }
    registry[DocumentKind.PARTICIPATION] = InMemoryRepository("encounter_id+instance_label")
    registry[DocumentKind.ADVENTURE] = InMemoryAdventureRepository()
    return registry
