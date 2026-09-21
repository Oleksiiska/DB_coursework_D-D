"""Адаптери кешу та лічильників поверх сховища в пам'яті."""

from __future__ import annotations

from typing import Any

from app.domain.entities import SearchLogEntry


class RedisCacheAdapter:
    """Реалізує порт `CacheStore`."""

    def __init__(self, client: Any) -> None:
        self._client = client

    async def get(self, key: str) -> bytes | None:
        raise NotImplementedError

    async def set(self, key: str, value: bytes, ttl_seconds: int | None = None) -> None:
        raise NotImplementedError

    async def delete(self, key: str) -> bool:
        raise NotImplementedError

    async def incr(self, key: str, amount: int = 1) -> int:
        raise NotImplementedError

    async def push_capped(self, key: str, value: bytes, max_length: int) -> None:
        raise NotImplementedError


class RedisMetricsAdapter:
    """Реалізує порт `MetricsSink`.

    Лічильники живуть у тому самому сховищі, що й кеш: панель
    моніторингу читає їх без звернення до основних баз.
    """

    KEY_SAVED = "stats:saved"
    KEY_REJECTED = "stats:rejected"
    KEY_RECENT = "stats:recent_queries"
    KEY_LATENCY = "stats:latency"

    def __init__(self, client: Any, recent_limit: int = 50) -> None:
        self._client = client
        self._recent_limit = recent_limit

    async def record_query(self, entry: SearchLogEntry) -> None:
        raise NotImplementedError

    async def inc(self, counter: str, amount: int = 1) -> None:
        raise NotImplementedError

    async def observe_latency(self, operation: str, milliseconds: int) -> None:
        raise NotImplementedError

    async def snapshot(self) -> dict:
        raise NotImplementedError
