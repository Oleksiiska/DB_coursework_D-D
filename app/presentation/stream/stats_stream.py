"""Ведучий адаптер: потік статистики в реальному часі (Server-Sent Events).

Панель моніторингу отримує зріз раз на секунду без опитування; браузерний
EventSource сам перепідключається після обриву.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import AsyncIterator

from app.application.dto import LiveStatsDTO, dto_to_dict
from app.application.ports import GetLiveStatsUseCase

log = logging.getLogger("api")


class StatsStream:
    """`GET /api/stats/stream` — потік подій text/event-stream."""

    INTERVAL_SECONDS = 1.0

    def __init__(self, use_case: GetLiveStatsUseCase) -> None:
        self._use_case = use_case

    async def stream(self) -> AsyncIterator[LiveStatsDTO]:
        """Нескінченний потік зрізів до розриву з'єднання."""
        while True:
            started = time.monotonic()
            yield await self._use_case.handle()
            await asyncio.sleep(max(0.0, self.INTERVAL_SECONDS - (time.monotonic() - started)))

    async def events(self) -> AsyncIterator[str]:
        """Події SSE; тимчасова недоступність сховищ — подія error, а не обрив потоку."""
        while True:
            started = time.monotonic()
            try:
                yield self.serialize(await self._use_case.handle())
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 — панель лишається підключеною
                log.warning("зріз статистики недоступний: %s", error)
                yield f"event: error\ndata: {json.dumps({'detail': str(error)}, ensure_ascii=False)}\n\n"
            await asyncio.sleep(max(0.0, self.INTERVAL_SECONDS - (time.monotonic() - started)))

    def serialize(self, stats: LiveStatsDTO) -> str:
        return f"data: {json.dumps(dto_to_dict(stats), ensure_ascii=False)}\n\n"
