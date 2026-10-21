"""Адаптери Redis на справжньому сервері: реєстр екземплярів генератора,
посекундні кошики швидкостей і черга Redis Streams (п. 4.7–4.8).

    CACHE_URL=redis://localhost:6379/15 python -m pytest tests/integration/test_redis_adapters.py
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone

import pytest

from app.domain.entities import SearchLogEntry
from app.domain.values import DocumentKind
from app.infrastructure.messaging import QueueAdapter
from app.infrastructure.persistence import RedisCacheAdapter, RedisMetricsAdapter

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif("CACHE_URL" not in os.environ, reason="потребує CACHE_URL"),
]


@pytest.fixture
async def redis():
    from redis.asyncio import Redis

    client = Redis.from_url(os.environ["CACHE_URL"])
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


async def test_cache_keys_expire_but_configuration_does_not(redis) -> None:
    cache = RedisCacheAdapter(redis)
    await cache.set("search:v1:adventures:x", b"{}", 60)
    await cache.set("config:generator_rate", b"2000")
    assert await redis.ttl("search:v1:adventures:x") == 60 and await redis.ttl("config:generator_rate") == -1


async def test_heartbeat_counts_live_instances_only(redis) -> None:
    cache = RedisCacheAdapter(redis)
    assert await cache.heartbeat("generator", "a", 5) == 1
    assert await cache.heartbeat("generator", "b", 5) == 2
    await redis.zadd("heartbeat:generator", {"crashed": time.time() - 10})
    assert await cache.heartbeat("generator", "a", 5) == 2


async def test_metrics_rates_come_from_per_second_buckets(redis) -> None:
    metrics = RedisMetricsAdapter(redis)
    now = int(time.time())
    for second in range(now - 5, now):
        await redis.hset(f"metrics:sec:{second}", "generator.generated", 100)
    await metrics.inc("generator.generated", 7)
    assert 0 < await redis.ttl(f"metrics:sec:{now}") <= 120
    await metrics.record_query(SearchLogEntry.from_result("q", "goblin", "goblin", DocumentKind.MONSTER, {}, 3, 9,
                                                          False, datetime.now(timezone.utc)))
    snapshot = await metrics.snapshot()
    assert snapshot["counters"]["generator.generated"] == 7
    assert snapshot["rates"]["generator.generated"] == 100
    assert snapshot["recent_queries"][0]["raw_query"] == "goblin"
    assert await redis.ttl("metrics:totals") == -1 and await redis.ttl("metrics:recent_queries") == -1


async def test_stream_queue_acknowledges_deletes_and_redelivers(redis) -> None:
    queue = QueueAdapter(redis, "worker-1")
    await queue.publish("documents", [{"kind": "monster", "name": "Goblin"}])
    await queue.publish("documents", [{"kind": "monster", "name": "Orc"}])
    assert await queue.queue_depth("documents") == 2
    consumer = queue.consume("documents")
    first = await consumer.__anext__()
    await queue.acknowledge("documents", first.batch_id)
    assert await queue.queue_depth("documents") == 1  # XACK + XDEL: довжина — ще не записані пакети
    lost = await consumer.__anext__()  # процес «упав» до підтвердження
    await consumer.aclose()
    other = QueueAdapter(redis, "worker-2")
    other.CLAIM_IDLE_MS = 0
    reclaimed = await other.consume("documents").__anext__()
    assert reclaimed.batch_id == lost.batch_id and reclaimed.documents == [{"kind": "monster", "name": "Orc"}]
