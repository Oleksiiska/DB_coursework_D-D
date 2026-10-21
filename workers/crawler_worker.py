"""Процес краулера (бонусна частина).

Швидкість обмежена мережею й ввічливою затримкою, тому виконується
незалежно від основного завантаження.
"""

from __future__ import annotations

import logging

from app.domain.ports import DocumentSource, EventPublisher, MetricsSink
from workers import BackpressurePublisher

log = logging.getLogger("crawler")


class CrawlerWorker:
    """Переносить пакети CrawlerSource у чергу, а звіт джерела — у лічильники
    таблиці 4.14: запитані й отримані сторінки, помилки, обмеження частоти,
    заборони robots.txt, розібрані картки та поступ етапів обходу."""

    TOPIC = "documents"
    BATCH_SIZE = 20

    def __init__(
        self,
        source: DocumentSource,
        publisher: EventPublisher,
        metrics: MetricsSink,
        queue_limit: int = 50,
        pause_seconds: float = 0.1,
    ) -> None:
        self._source = source
        self._publisher = publisher
        self._metrics = metrics
        self._outbox = BackpressurePublisher(publisher, metrics, queue_limit, pause_seconds, log)

    async def run(self) -> None:
        async for batch in self._source.stream(self.BATCH_SIZE):
            stats = batch.stats
            for counter, value in (
                ("crawler.requests", stats.requested),
                ("crawler.pages_ok", stats.succeeded),
                ("crawler.http_errors", stats.failed),
                ("crawler.throttled", stats.throttled),
                ("crawler.robots_skipped", stats.robots_skipped),
                ("crawler.cards_parsed", sum(1 for d in batch.documents if d.get("kind") == "adventure")),
            ):
                await self._metrics.inc(counter, value)
            if stats.stage:
                await self._metrics.inc(f"crawler.stage.{stats.stage}.discovered", stats.discovered)
                await self._metrics.inc(f"crawler.stage.{stats.stage}.done", stats.completed)
            if batch.documents:
                await self._outbox.publish(batch.documents)
