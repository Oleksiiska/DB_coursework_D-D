"""Сценарій «статистика в реальному часі» для панелі моніторингу (група 10)."""

from __future__ import annotations

import json
import logging
import statistics
from datetime import datetime, timezone

from app.application.dto import LiveStatsDTO, QueryLogItemDTO
from app.domain.ports import AdventureRepository, CacheStore, EventPublisher, MetricsSink

log = logging.getLogger("api")


class GetLiveStatsHandler:
    """Зріз стану системи з трьох джерел (п. 4.9).

    Лічильники, швидкості й журнал останніх запитів дає MetricsSink, глибину
    черги — EventPublisher, а кількість документів пригод — сама MongoDB
    через AdventureRepository.count_by_shard(). Результат підрахунку
    зберігається в CacheStore на 1 с, тож кілька відкритих панелей не
    множать звернень до сховища.
    """

    #: Скільки останніх запитів показує панель (таблиця 4.16)
    RECENT_QUERIES_LIMIT = 20
    SHARDS_CACHE_KEY = "stats:adventures_by_shard"
    SHARDS_CACHE_TTL_SECONDS = 1
    #: Спільні ключі конфігурації: цільова швидкість (ConfigureRateUseCase)
    #: і кількість живих екземплярів генератора, яку зберігає сам генератор
    RATE_KEY = "config:generator_rate"
    INSTANCES_KEY = "config:generator_instances"
    QUEUE_TOPIC = "documents"
    #: Етапи обходу краулера в порядку виконання (п. 4.8)
    CRAWLER_STAGES = ("lists", "cards", "authors", "tags")
    #: Походження зібраних пригод (каталог) і згенерованих
    COLLECTED_ORIGINS = ("csv", "crawler")
    GENERATED_ORIGINS = ("generated",)

    def __init__(
        self,
        metrics: MetricsSink,
        cache: CacheStore,
        publisher: EventPublisher,
        adventures: AdventureRepository,
        queue_limit: int = 50,
        default_rate: int = 100,
    ) -> None:
        self._metrics = metrics
        self._cache = cache
        self._publisher = publisher
        self._adventures = adventures
        self._queue_limit = queue_limit
        self._default_rate = default_rate

    async def handle(self) -> LiveStatsDTO:
        """Лічильники, швидкості, глибина черги, документи на шардах,
        частка влучань у кеш, процентилі затримки, стрічка запитів."""
        snapshot = await self._metrics.snapshot()
        counters: dict[str, int] = snapshot.get("counters", {})
        queries = [self._query(item) for item in snapshot.get("recent_queries", [])]
        latencies = sorted(q.latency_ms for q in queries)
        shards = await self._shards()
        return LiveStatsDTO(
            generated_at=datetime.now(timezone.utc),
            counters=counters,
            rates=snapshot.get("rates", {}),
            target_rate=await self._int_value(self.RATE_KEY, self._default_rate),
            generator_instances=await self._int_value(self.INSTANCES_KEY, 0),
            queue_depth=await self._publisher.queue_depth(self.QUEUE_TOPIC),
            queue_limit=self._queue_limit,
            shards=shards,
            adventures_total=sum(shards.values()),
            cache_hit_ratio=sum(q.cache_hit for q in queries) / len(queries) if queries else None,
            latency_p50_ms=statistics.median(latencies) if latencies else None,
            latency_p95_ms=self._percentile(latencies, 0.95),
            recent_queries=queries[: self.RECENT_QUERIES_LIMIT],
            fill=self._fill(counters),
            crawler_stage=self._crawler_stage(counters),
        )

    # --- джерела ---

    async def _shards(self) -> dict[str, int]:
        payload = await self._cache.get(self.SHARDS_CACHE_KEY)
        if payload is not None:
            return json.loads(payload)
        shards = await self._adventures.count_by_shard()
        await self._cache.set(self.SHARDS_CACHE_KEY, json.dumps(shards).encode("utf-8"), self.SHARDS_CACHE_TTL_SECONDS)
        return shards

    async def _int_value(self, key: str, default: int) -> int:
        payload = await self._cache.get(key)
        return default if payload is None else int(payload)

    # --- перетворення ---

    @staticmethod
    def _query(item: dict) -> QueryLogItemDTO:
        return QueryLogItemDTO(
            raw_query=item.get("raw_query", ""),
            kind=item.get("kind", "adventure"),
            status=item.get("status", "EMPTY"),
            results_count=int(item.get("results_count", 0)),
            latency_ms=int(item.get("latency_ms", 0)),
            cache_hit=bool(item.get("cache_hit", False)),
            created_at=datetime.fromisoformat(item["created_at"]) if item.get("created_at") else datetime.now(timezone.utc),
        )

    @staticmethod
    def _percentile(sorted_values: list[int], share: float) -> float | None:
        if not sorted_values:
            return None
        position = min(len(sorted_values) - 1, max(0, round(share * len(sorted_values) + 0.5) - 1))
        return float(sorted_values[position])

    def _fill(self, counters: dict[str, int]) -> dict[str, dict[str, float | None]]:
        """Частка пригод із заповненим полем окремо за походженням (таблиця 4.14)."""

        def share(field: str, origins: tuple[str, ...]) -> float | None:
            total = sum(counters.get(f"ingest.filled.adventures.{o}", 0) for o in origins)
            filled = sum(counters.get(f"ingest.filled.{field}.{o}", 0) for o in origins)
            return None if total == 0 else filled / total

        return {
            field: {"collected": share(field, self.COLLECTED_ORIGINS), "generated": share(field, self.GENERATED_ORIGINS)}
            for field in LiveStatsDTO.FILL_FIELDS
        }

    def _crawler_stage(self, counters: dict[str, int]) -> dict | None:
        """Поточний етап обходу: перший етап, у якому оброблено не всі
        виявлені адреси, інакше — останній розпочатий."""
        current = None
        for stage in self.CRAWLER_STAGES:
            discovered = counters.get(f"crawler.stage.{stage}.discovered", 0)
            done = counters.get(f"crawler.stage.{stage}.done", 0)
            if discovered == 0:
                continue
            current = {"stage": stage, "done": done, "total": discovered}
            if done < discovered:
                break
        return current
