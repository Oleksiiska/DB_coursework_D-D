"""Ведучий адаптер: потік статистики в реальному часі.

Панель моніторингу отримує зріз раз на секунду без опитування.
"""

from __future__ import annotations

from typing import AsyncIterator

from app.application.dto import LiveStatsDTO
from app.application.ports import GetLiveStatsUseCase


class StatsStream:
    """`GET /api/stats/stream` — потік подій."""

    INTERVAL_SECONDS = 1.0

    def __init__(self, use_case: GetLiveStatsUseCase) -> None:
        self._use_case = use_case

    async def stream(self) -> AsyncIterator[LiveStatsDTO]:
        """Нескінченний потік зрізів до розриву з'єднання."""
        raise NotImplementedError

    def serialize(self, stats: LiveStatsDTO) -> str:
        raise NotImplementedError
