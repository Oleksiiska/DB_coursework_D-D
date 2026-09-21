"""Адаптер черги повідомлень.

Черга розв'язує швидкості виробника й споживача: генератор може
випереджати сховище, не втрачаючи документів.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Sequence


class QueueAdapter:
    """Реалізує порт `EventPublisher`."""

    TOPIC_DOCUMENTS = "documents"

    def __init__(self, client: Any) -> None:
        self._client = client

    async def publish(self, topic: str, batch: Sequence[dict]) -> None:
        raise NotImplementedError

    async def queue_depth(self, topic: str) -> int:
        """Глибина черги робить вузьке місце вимірюваним."""
        raise NotImplementedError

    async def consume(self, topic: str, batch_size: int = 500) -> AsyncIterator[list[dict]]:
        """Споживання пакетами — використовується процесом запису."""
        raise NotImplementedError
