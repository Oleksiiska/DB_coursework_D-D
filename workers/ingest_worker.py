"""Процес запису: споживання черги та пакетний запис у сховища."""

from __future__ import annotations

import asyncio
import logging

from app.application.ports import IngestDocumentUseCase
from app.domain.ports import EventConsumer

log = logging.getLogger("ingest")


class IngestWorker:
    """Забирає пакети з черги й передає сценарію приймання.

    Пакет підтверджується лише після запису в усі три сховища; збій
    лишає його непідтвердженим, і через 60 с його забере інший екземпляр
    (повторна доставка). Пакетний запис зменшує кількість звернень до
    сховищ на два порядки й є основним чинником пропускної здатності.
    """

    TOPIC = "documents"
    #: Пауза після збою сховища, щоб не перебирати чергу вхолосту
    FAILURE_PAUSE_SECONDS = 1.0

    def __init__(self, consumer: EventConsumer, ingest: IngestDocumentUseCase, batch_size: int = 500) -> None:
        self._consumer = consumer
        self._ingest = ingest
        self._batch_size = batch_size
        self._running = False

    async def run(self) -> None:
        """Читання пакета, обробка, підтвердження за `batch_id`."""
        self._running = True
        async for batch in self._consumer.consume(self.TOPIC, self._batch_size):
            origins = sorted({f"{d.get('origin')}:{d.get('kind')}" for d in batch.documents})
            try:
                await self._ingest.handle(batch.documents, ", ".join(origins) or "—")
            except Exception as error:  # noqa: BLE001 — пакет лишається непідтвердженим
                log.error("пакет %s не записано: %s", batch.batch_id, str(error) or type(error).__name__)
                await asyncio.sleep(self.FAILURE_PAUSE_SECONDS)
            else:
                await self._consumer.acknowledge(self.TOPIC, batch.batch_id)
            if not self._running:
                break

    async def stop(self) -> None:
        """Зупинка після завершення поточного пакета."""
        self._running = False
