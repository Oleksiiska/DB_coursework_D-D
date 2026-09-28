"""Процес запису: споживання черги та пакетний запис у сховища."""

from __future__ import annotations

from app.application.ports import IngestDocumentUseCase
from app.domain.ports import EventConsumer


class IngestWorker:
    """Забирає пакети з черги й передає сценарію приймання.

    Пакетний запис зменшує кількість звернень до сховищ на два порядки
    і є основним чинником пропускної здатності шляху команд.
    """

    TOPIC = "documents"

    def __init__(self, consumer: EventConsumer, ingest: IngestDocumentUseCase, batch_size: int = 500) -> None:
        self._consumer = consumer
        self._ingest = ingest
        self._batch_size = batch_size
        self._running = False

    async def run(self) -> None:
        """Читання пакета, обробка, підтвердження за `batch_id`."""
        raise NotImplementedError

    async def stop(self) -> None:
        """Зупинка після завершення поточного пакета."""
        raise NotImplementedError
