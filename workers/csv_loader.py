"""Процес завантаження підготовлених CSV із заданою швидкістю (п. 4.8)."""

from __future__ import annotations

import logging

from app.domain.ports import DocumentSource, EventPublisher, MetricsSink
from app.domain.values import DocumentKind
from workers import BackpressurePublisher

log = logging.getLogger("csv-loader")


class CsvLoader:
    """Публікує в чергу підготовлені файли в фіксованому порядку.

    Першими йдуть ігрові світи зі списку вручну (app/data/settings.json,
    origin = manual), далі — CSV таблиці 2.3 пакетами по 500 рядків зі
    швидкістю CSV_RATE (за замовчуванням 1000 рядків за секунду). Порядок
    зручний, але не обов'язковий: ключі детерміновані, тож посилання на ще
    не записаний документ лише тимчасово не показується у видачі (п. 2.2).
    Після останнього файлу процес завершується.
    """

    TOPIC = "documents"
    BATCH_SIZE = 500

    ORDER: tuple[DocumentKind, ...] = (
        DocumentKind.SETTING,
        DocumentKind.MONSTER,
        DocumentKind.CHARACTER,
        DocumentKind.ENCOUNTER,
        DocumentKind.PARTICIPATION,
        DocumentKind.AUTHOR,
        DocumentKind.TAG,
        DocumentKind.ADVENTURE,
    )

    def __init__(
        self,
        sources: dict[DocumentKind, DocumentSource],
        publisher: EventPublisher,
        metrics: MetricsSink,
        queue_limit: int = 50,
        pause_seconds: float = 0.1,
    ) -> None:
        self._sources = sources
        self._publisher = publisher
        self._metrics = metrics
        self._outbox = BackpressurePublisher(publisher, metrics, queue_limit, pause_seconds, log)

    async def run(self) -> None:
        total = 0
        for kind in self.ORDER:
            total += await self.load_one(kind)
        log.info("завантаження завершено, прочитано рядків: %d", total)

    async def load_one(self, kind: DocumentKind) -> int:
        """Один файл: лічильник csv.rows фіксує прочитаний обсяг."""
        source = self._sources[kind]
        path = getattr(source, "path", None)
        if path is not None and not path.exists():
            log.warning("%s: файл %s не знайдено — пропущено", source.name, path)
            return 0
        rows = 0
        async for batch in source.stream(self.BATCH_SIZE):
            await self._metrics.inc("csv.rows", len(batch.documents))
            await self._outbox.publish(batch.documents)
            rows += len(batch.documents)
        log.info("%s: опубліковано рядків: %d", source.name, rows)
        return rows
