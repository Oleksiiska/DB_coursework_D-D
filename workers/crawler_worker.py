"""Процес краулера (бонусна частина).

Швидкість обмежена мережею, тому виконується незалежно від
основного завантаження.
"""

from __future__ import annotations

from app.domain.ports import DocumentSource, EventPublisher


class CrawlerWorker:
    TOPIC = "documents"

    def __init__(self, source: DocumentSource, publisher: EventPublisher) -> None:
        self._source = source
        self._publisher = publisher

    async def run(self) -> None:
        raise NotImplementedError
