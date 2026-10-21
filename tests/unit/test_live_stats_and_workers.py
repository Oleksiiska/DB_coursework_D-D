"""Панель моніторингу (п. 4.9) і процеси-джерела (п. 4.8) на заглушках."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.application.queries import GetLiveStatsHandler
from app.domain.entities import SearchLogEntry
from app.domain.values import DocumentKind
from tests.unit.fakes import FakeCacheStore, FakeMetricsSink, FakeQueue, InMemoryAdventureRepository, ListSource
from workers import BackpressurePublisher
from workers.crawler_worker import CrawlerWorker
from workers.csv_loader import CsvLoader
from workers.generator_worker import GeneratorWorker
from workers.ingest_worker import IngestWorker


def _entry(latency: int, hit: bool, found: int) -> SearchLogEntry:
    return SearchLogEntry.from_result("q", "cursed village", "curs villag", DocumentKind.ADVENTURE, {}, found,
                                      latency, hit, datetime.now(timezone.utc))


async def test_snapshot_combines_metrics_queue_and_database() -> None:
    metrics, cache, queue = FakeMetricsSink(), FakeCacheStore(), FakeQueue()
    for latency, hit in ((4, True), (10, False), (40, False), (2, True)):
        await metrics.record_query(_entry(latency, hit, 3))
    for counter, value in {"ingest.filled.adventures.generated": 10, "ingest.filled.level_min.generated": 10,
                           "ingest.filled.adventures.crawler": 4, "ingest.filled.level_min.crawler": 1,
                           "crawler.stage.lists.discovered": 54, "crawler.stage.lists.done": 54,
                           "crawler.stage.cards.discovered": 1909, "crawler.stage.cards.done": 700}.items():
        await metrics.inc(counter, value)
    queue.depth = 7
    await cache.set("config:generator_rate", b"2000")
    await cache.set("config:generator_instances", b"4")
    stats = await GetLiveStatsHandler(metrics, cache, queue, InMemoryAdventureRepository()).handle()
    assert (stats.target_rate, stats.generator_instances, stats.queue_depth, stats.queue_limit) == (2000, 4, 7, 50)
    assert stats.cache_hit_ratio == 0.5 and stats.latency_p50_ms == 7 and stats.latency_p95_ms == 40
    assert stats.fill["level_min"] == {"collected": 0.25, "generated": 1.0}
    assert stats.crawler_stage == {"stage": "cards", "done": 700, "total": 1909}
    assert stats.shards == {"shard1": 0} and cache.ttl["stats:adventures_by_shard"] == 1


async def test_backpressure_pauses_publication_and_counts_pauses(caplog) -> None:
    queue, metrics = FakeQueue(), FakeMetricsSink()
    queue.depth = 51
    calls = 0
    original = queue.queue_depth

    async def draining(topic: str) -> int:
        nonlocal calls
        calls += 1
        if calls == 3:
            queue.depth = 50
        return await original(topic)

    queue.queue_depth = draining
    outbox = BackpressurePublisher(queue, metrics, limit=50, pause_seconds=0, log=logging.getLogger("generator"))
    with caplog.at_level(logging.INFO, logger="generator"):
        await outbox.publish([{"kind": "adventure"}])
        queue.depth, calls = 51, 0
        await outbox.publish([{"kind": "adventure"}])  # тиск триває: попередження не повторюється
        queue.depth = 0
        await outbox.publish([{"kind": "adventure"}])
    assert metrics.counters["queue.backpressure"] == 4 and len(queue.published) == 3
    messages = [record.getMessage() for record in caplog.records]
    assert messages == ["черга досягла межі 50 пакетів — публікацію призупинено", "черга нижче межі — публікацію відновлено"]


async def test_generator_takes_its_share_of_the_total_rate() -> None:
    """r = R / N; N зберігається в кеші без часу життя."""
    cache, metrics, queue = FakeCacheStore(), FakeMetricsSink(), FakeQueue()
    await cache.set("config:generator_rate", b"3000")
    await cache.heartbeat("generator", "other-instance", 5)
    source = ListSource([{"kind": "adventure", "origin": "generated"}] * 3)
    worker = GeneratorWorker(source, queue, cache, metrics, member="me")
    await worker.run()
    assert source.rates[0] == 1500 and cache.store["config:generator_instances"] == b"2"
    assert cache.ttl["config:generator_instances"] is None and metrics.counters["generator.generated"] == 3


async def test_csv_loader_publishes_settings_first_and_counts_rows() -> None:
    queue, metrics = FakeQueue(), FakeMetricsSink()
    sources = {kind: ListSource([{"kind": kind.value}] * 2, kind.value) for kind in CsvLoader.ORDER}
    await CsvLoader(sources, queue, metrics).run()
    assert queue.published[0]["kind"] == "setting" and metrics.counters["csv.rows"] == 16


async def test_crawler_worker_maps_source_report_to_counters() -> None:
    from app.domain.values import SourceBatch, SourceStats

    class Crawl(ListSource):
        async def stream(self, batch_size: int = 20):
            yield SourceBatch([{"kind": "adventure"}], SourceStats(5, 3, 1, 1, 2, "cards", 1909, 4))

    metrics, queue = FakeMetricsSink(), FakeQueue()
    await CrawlerWorker(Crawl([]), queue, metrics).run()
    assert metrics.counters["crawler.requests"] == 5 and metrics.counters["crawler.pages_ok"] == 3
    assert metrics.counters["crawler.throttled"] == 1 and metrics.counters["crawler.robots_skipped"] == 2
    assert metrics.counters["crawler.cards_parsed"] == 1 and metrics.counters["crawler.stage.cards.discovered"] == 1909


async def test_ingest_worker_acknowledges_only_written_batches() -> None:
    queue = FakeQueue()
    await queue.publish("documents", [{"kind": "adventure", "ok": True}])
    await queue.publish("documents", [{"kind": "adventure", "ok": False}])

    class Ingest:
        async def handle(self, batch, source_name):
            if not batch[0]["ok"]:
                raise ConnectionError("mongo-shard-2 недоступний")

    await IngestWorker(queue, Ingest()).run()
    assert queue.acknowledged == ["0"]
