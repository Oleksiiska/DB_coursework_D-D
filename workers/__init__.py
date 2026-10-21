"""Окремі процеси системи (п. 3.6). Запуск — `python -m workers.launcher <назва>`.

Процеси-джерела працюють за одним циклом (п. 4.8): отримати пакет від
DocumentSource, передати його лічильники в MetricsSink, перевірити глибину
черги й опублікувати документи через EventPublisher. Спільний крок —
публікацію зі зворотним тиском — винесено сюди.
"""

from __future__ import annotations

import asyncio
import logging

from app.domain.ports import EventPublisher, MetricsSink

#: Тема черги пакетів документів
TOPIC = "documents"


class BackpressurePublisher:
    """Публікація пакетів зі зворотним тиском (п. 4.3, 4.8).

    Якщо глибина черги перевищує межу (за замовчуванням 50 пакетів),
    публікація відкладається на 100 мс із повторною перевіркою, а кожна
    пауза рахується лічильником queue.backpressure. Попередження пишеться
    один раз на початку тиску, а повідомлення про відновлення — один раз
    після нього, тож тривале насичення не засмічує журнал.
    """

    def __init__(
        self,
        publisher: EventPublisher,
        metrics: MetricsSink,
        limit: int = 50,
        pause_seconds: float = 0.1,
        log: logging.Logger | None = None,
    ) -> None:
        self._publisher = publisher
        self._metrics = metrics
        self._limit = limit
        self._pause_seconds = pause_seconds
        self._log = log
        self._pressed = False

    async def publish(self, documents: list[dict]) -> None:
        waited = False
        while await self._publisher.queue_depth(TOPIC) > self._limit:
            if not self._pressed and self._log is not None:
                self._log.warning("черга досягла межі %d пакетів — публікацію призупинено", self._limit)
            self._pressed = waited = True
            await self._metrics.inc("queue.backpressure")
            await asyncio.sleep(self._pause_seconds)
        if self._pressed and not waited:
            self._pressed = False
            if self._log is not None:
                self._log.info("черга нижче межі — публікацію відновлено")
        await self._publisher.publish(TOPIC, documents)
