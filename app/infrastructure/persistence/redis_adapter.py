"""Адаптери кешу та лічильників поверх сховища в пам'яті (Redis, п. 4.7–4.8).

Той самий екземпляр Redis зберігає чергу пакетів і лічильники, тому
час життя мають лише записи кешу й посекундні кошики швидкостей: за
політики volatile-lru під тиском пам'яті витісняються тільки вони.
"""

from __future__ import annotations

import json
import logging
import statistics
import time
from typing import Any

from app.domain.entities import SearchLogEntry

log = logging.getLogger("db.redis")


class RedisCacheAdapter:
    """Реалізує порт `CacheStore`: кеш результатів, спільна конфігурація
    швидкості й реєстр екземплярів генератора."""

    HEARTBEAT_KEY = "heartbeat:{}"

    def __init__(self, client: Any) -> None:
        self._client = client

    async def get(self, key: str) -> bytes | None:
        started = time.perf_counter()
        value = await self._client.get(key)
        if log.isEnabledFor(logging.DEBUG):
            log.debug("get %s — %s, %.1f мс", key, "влучання" if value is not None else "промах",
                      (time.perf_counter() - started) * 1000)
        return value

    async def set(self, key: str, value: bytes, ttl_seconds: int | None = None) -> None:
        """Без часу життя ключ не витісняється (параметри швидкості, конфігурація)."""
        await self._client.set(key, value, ex=ttl_seconds)

    async def delete(self, key: str) -> bool:
        return bool(await self._client.delete(key))

    async def incr(self, key: str, amount: int = 1) -> int:
        return int(await self._client.incrby(key, amount))

    async def push_capped(self, key: str, value: bytes, max_length: int) -> None:
        async with self._client.pipeline(transaction=True) as pipe:
            pipe.lpush(key, value)
            pipe.ltrim(key, 0, max_length - 1)
            await pipe.execute()

    async def heartbeat(self, group: str, member: str, window_seconds: int) -> int:
        """Впорядкована множина з часом останнього сигналу як вагою: в одній
        транзакції учасник позначається живим, застарілі позначки видаляються,
        а повертається кількість живих (п. 4.8)."""
        key = self.HEARTBEAT_KEY.format(group)
        now = time.time()
        async with self._client.pipeline(transaction=True) as pipe:
            pipe.zadd(key, {member: now})
            pipe.zremrangebyscore(key, "-inf", now - window_seconds)
            pipe.zcard(key)
            results = await pipe.execute()
        return int(results[-1])


class RedisMetricsAdapter:
    """Реалізує порт `MetricsSink`.

    Лічильники живуть у тому самому сховищі, що й кеш: панель
    моніторингу читає їх без звернення до основних баз. Кожне збільшення
    лічильника потрапляє ще й у посекундний кошик із часом життя 2 хв,
    а `snapshot()` повертає разом із підсумками середню швидкість за
    останні 5 с (п. 4.8). Методи порту від цього не змінюються.
    """

    #: Підсумки лічильників — хеш без часу життя
    KEY_TOTALS = "metrics:totals"
    #: Посекундний кошик лічильників
    KEY_SECOND = "metrics:sec:{}"
    #: Журнал останніх пошукових запитів (поля таблиці А.12)
    KEY_QUERIES = "metrics:recent_queries"
    #: Останні виміри тривалості операції та перелік операцій
    KEY_LATENCY = "metrics:latency:{}"
    KEY_LATENCY_OPERATIONS = "metrics:latency_operations"

    BUCKET_TTL_SECONDS = 120
    RATE_WINDOW_SECONDS = 5
    LATENCY_SAMPLES = 1000

    def __init__(self, client: Any, recent_limit: int = 200) -> None:
        self._client = client
        self._recent_limit = recent_limit

    async def record_query(self, entry: SearchLogEntry) -> None:
        payload = json.dumps(
            {
                "query_id": entry.query_id,
                "raw_query": entry.raw_query,
                "normalized_query": entry.normalized_query,
                "kind": entry.kind.value,
                "filters": entry.filters,
                "results_count": entry.results_count,
                "status": entry.status.value,
                "latency_ms": entry.latency_ms,
                "cache_hit": entry.cache_hit,
                "created_at": entry.created_at.isoformat(),
            },
            ensure_ascii=False,
        )
        async with self._client.pipeline(transaction=True) as pipe:
            pipe.lpush(self.KEY_QUERIES, payload)
            pipe.ltrim(self.KEY_QUERIES, 0, self._recent_limit - 1)
            await pipe.execute()
        await self.inc("search.requests")

    async def inc(self, counter: str, amount: int = 1) -> None:
        if amount == 0:
            return
        bucket = self.KEY_SECOND.format(int(time.time()))
        async with self._client.pipeline(transaction=False) as pipe:
            pipe.hincrby(self.KEY_TOTALS, counter, amount)
            pipe.hincrby(bucket, counter, amount)
            pipe.expire(bucket, self.BUCKET_TTL_SECONDS)
            await pipe.execute()

    async def observe_latency(self, operation: str, milliseconds: int) -> None:
        key = self.KEY_LATENCY.format(operation)
        async with self._client.pipeline(transaction=False) as pipe:
            pipe.sadd(self.KEY_LATENCY_OPERATIONS, operation)
            pipe.lpush(key, int(milliseconds))
            pipe.ltrim(key, 0, self.LATENCY_SAMPLES - 1)
            await pipe.execute()

    async def snapshot(self) -> dict:
        """Зріз для панелі: підсумки, середня швидкість за 5 повних секунд,
        журнал останніх запитів і процентилі тривалості операцій."""
        now = int(time.time())
        seconds = range(now - self.RATE_WINDOW_SECONDS, now)
        async with self._client.pipeline(transaction=False) as pipe:
            pipe.hgetall(self.KEY_TOTALS)
            for second in seconds:
                pipe.hgetall(self.KEY_SECOND.format(second))
            pipe.lrange(self.KEY_QUERIES, 0, self._recent_limit - 1)
            pipe.smembers(self.KEY_LATENCY_OPERATIONS)
            results = await pipe.execute()
        totals = {_text(k): int(v) for k, v in results[0].items()}
        sums: dict[str, int] = {}
        for bucket in results[1 : 1 + len(seconds)]:
            for k, v in bucket.items():
                sums[_text(k)] = sums.get(_text(k), 0) + int(v)
        queries = [json.loads(item) for item in results[-2]]
        operations = sorted(_text(op) for op in results[-1])
        latency: dict[str, dict[str, float]] = {}
        if operations:
            async with self._client.pipeline(transaction=False) as pipe:
                for operation in operations:
                    pipe.lrange(self.KEY_LATENCY.format(operation), 0, self.LATENCY_SAMPLES - 1)
                samples = await pipe.execute()
            for operation, values in zip(operations, samples):
                ordered = sorted(int(v) for v in values)
                if ordered:
                    latency[operation] = {
                        "count": len(ordered),
                        "p50": float(statistics.median(ordered)),
                        "p95": float(ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]),
                    }
        return {
            "counters": totals,
            "rates": {name: total / self.RATE_WINDOW_SECONDS for name, total in sums.items()},
            "recent_queries": queries,
            "latency": latency,
        }


def _text(value: Any) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)
