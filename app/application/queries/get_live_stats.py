"""Сценарій «статистика в реальному часі» для панелі моніторингу."""

from __future__ import annotations

from app.application.dto import LiveStatsDTO
from app.domain.ports import CacheStore, EventPublisher, MetricsSink


class GetLiveStatsHandler:
    """Зріз стану системи. Читає лише лічильники, сховищ не чіпає."""

    RECENT_QUERIES_LIMIT = 50

    def __init__(self, metrics: MetricsSink, cache: CacheStore, publisher: EventPublisher) -> None:
        self._metrics = metrics
        self._cache = cache
        self._publisher = publisher

    async def handle(self) -> LiveStatsDTO:
        """Лічильники, глибина черги, частка влучань, стрічка запитів."""
        raise NotImplementedError
