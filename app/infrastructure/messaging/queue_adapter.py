"""Адаптер черги повідомлень.

Черга розв'язує швидкості виробника й споживача: генератор може
випереджати сховище, не втрачаючи документів.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Sequence

from app.domain.values import QueuedBatch


class QueueAdapter:
    """Реалізує два порти: `EventPublisher` і `EventConsumer`.

    Один адаптер — два вузькі контракти: джерела бачать лише публікацію,
    процес запису — лише читання.
    """

    TOPIC_DOCUMENTS = "documents"

    def __init__(self, client: Any) -> None:
        self._client = client

    async def publish(self, topic: str, batch: Sequence[dict]) -> None:
        raise NotImplementedError

    async def queue_depth(self, topic: str) -> int:
        """Глибина черги робить вузьке місце вимірюваним."""
        raise NotImplementedError

    async def consume(self, topic: str, batch_size: int = 500) -> AsyncIterator[QueuedBatch]:
        raise NotImplementedError

    async def acknowledge(self, topic: str, batch_id: str) -> None:
        raise NotImplementedError
