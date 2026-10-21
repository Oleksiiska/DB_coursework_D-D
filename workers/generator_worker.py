"""Процес генератора.

Винесений окремо: генерація навантажує процесор і в межах одного
процесу з асинхронним циклом подій блокувала б обслуговування
запитів користувача.
"""

from __future__ import annotations

import logging
import os
import socket

from app.domain.ports import CacheStore, DocumentSource, EventPublisher, MetricsSink
from workers import BackpressurePublisher

log = logging.getLogger("generator")


class GeneratorWorker:
    """Керується цільовою сумарною швидкістю R зі спільного сховища (п. 4.8).

    Перед кожним пакетом екземпляр позначає себе живим у реєстрі
    екземплярів (CacheStore.heartbeat) і отримує кількість N екземплярів,
    що подавали сигнал протягом останніх 5 с; свою частку r = R / N він
    передає джерелу, а N зберігає в кеші для панелі. Оскільки R і N
    перечитуються перед кожним пакетом, зміна положення повзунка діє не
    пізніше ніж за секунду. Отримує порти, а не контейнер: залежності
    процесу видно з сигнатури.
    """

    RATE_KEY = "config:generator_rate"
    INSTANCES_KEY = "config:generator_instances"
    HEARTBEAT_GROUP = "generator"
    HEARTBEAT_WINDOW_SECONDS = 5
    TOPIC = "documents"
    BATCH_SIZE = 500

    def __init__(
        self,
        source: DocumentSource,
        publisher: EventPublisher,
        cache: CacheStore,
        metrics: MetricsSink,
        default_rate: int = 100,
        queue_limit: int = 50,
        pause_seconds: float = 0.1,
        member: str | None = None,
    ) -> None:
        self._source = source
        self._publisher = publisher
        self._cache = cache
        self._metrics = metrics
        self._default_rate = default_rate
        self._outbox = BackpressurePublisher(publisher, metrics, queue_limit, pause_seconds, log)
        self._member = member or f"{socket.gethostname()}-{os.getpid()}"
        self._running = False
        self._state: tuple[int, int] | None = None

    async def run(self) -> None:
        """Цикл: сигнал живості й частка швидкості, пакет, лічильник, публікація."""
        self._running = True
        await self._tune()
        async for batch in self._source.stream(self.BATCH_SIZE):
            await self._metrics.inc("generator.generated", batch.stats.succeeded)
            await self._outbox.publish(batch.documents)
            if not self._running:
                break
            await self._tune()

    async def stop(self) -> None:
        self._running = False

    async def _tune(self) -> None:
        instances = max(1, await self._cache.heartbeat(self.HEARTBEAT_GROUP, self._member,
                                                       self.HEARTBEAT_WINDOW_SECONDS))
        await self._cache.set(self.INSTANCES_KEY, str(instances).encode("ascii"))
        payload = await self._cache.get(self.RATE_KEY)
        total = self._default_rate if payload is None else int(payload)
        await self._source.set_rate(total / instances)
        if self._state != (total, instances):
            self._state = (total, instances)
            log.info("цільова швидкість %d док/с, екземплярів %d, частка %.1f док/с", total, instances, total / instances)
