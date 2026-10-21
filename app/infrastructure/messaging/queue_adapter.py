"""Адаптер черги повідомлень — потік Redis Streams із групою споживачів (п. 4.8).

Черга розв'язує швидкості виробника й споживача: генератор може
випереджати сховище, не втрачаючи документів.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import time
from datetime import date, datetime
from typing import Any, AsyncIterator, Sequence

from app.domain.values import QueuedBatch

log = logging.getLogger("db.redis")


class QueueAdapter:
    """Реалізує два порти: `EventPublisher` і `EventConsumer`.

    Один адаптер — два вузькі контракти: джерела бачать лише публікацію,
    процес запису — лише читання. Запис потоку — один пакет документів;
    процес запису читає пакети командою XREADGROUP, після запису в три
    сховища підтверджує пакет командою XACK і видаляє його з потоку
    командою XDEL, тож довжина потоку дорівнює кількості ще не записаних
    пакетів. Пакет, не підтверджений протягом 60 с, забирає інший
    екземпляр командою XAUTOCLAIM — це й є повторна доставка.
    """

    TOPIC_DOCUMENTS = "documents"
    GROUP = "ingest"
    STREAM_KEY = "queue:{}"
    CLAIM_IDLE_MS = 60_000
    #: Як часто шукати завислі пакети, якщо нових немає
    CLAIM_INTERVAL_SECONDS = 5.0
    BLOCK_MS = 1000

    def __init__(self, client: Any, consumer_name: str | None = None) -> None:
        self._client = client
        self._consumer = consumer_name or f"{socket.gethostname()}-{os.getpid()}"
        self._groups: set[str] = set()

    async def publish(self, topic: str, batch: Sequence[dict]) -> None:
        payload = json.dumps(list(batch), default=_json_default, ensure_ascii=False)
        await self._client.xadd(self.STREAM_KEY.format(topic), {"documents": payload})

    async def queue_depth(self, topic: str) -> int:
        """Глибина черги робить вузьке місце вимірюваним."""
        return int(await self._client.xlen(self.STREAM_KEY.format(topic)))

    async def consume(self, topic: str, batch_size: int = 500) -> AsyncIterator[QueuedBatch]:
        """Нескінченний потік пакетів: спершу завислі (XAUTOCLAIM), потім нові
        (XREADGROUP); пакет розміром до `batch_size` документів формує джерело."""
        stream = self.STREAM_KEY.format(topic)
        await self._ensure_group(stream)
        claim_cursor = "0-0"
        last_claim = 0.0
        while True:
            entries: list[tuple[Any, dict]] = []
            if time.monotonic() - last_claim >= self.CLAIM_INTERVAL_SECONDS:
                last_claim = time.monotonic()
                claimed = await self._client.xautoclaim(
                    stream, self.GROUP, self._consumer, self.CLAIM_IDLE_MS, start_id=claim_cursor, count=1
                )
                claim_cursor = claimed[0]
                entries = [(entry_id, fields) for entry_id, fields in claimed[1] if fields]
                if entries:
                    log.warning("повторна доставка пакета %s після %d с без підтвердження",
                                _text(entries[0][0]), self.CLAIM_IDLE_MS // 1000)
            if not entries:
                response = await self._client.xreadgroup(
                    self.GROUP, self._consumer, {stream: ">"}, count=1, block=self.BLOCK_MS
                )
                entries = [entry for _, items in (response or []) for entry in items]
            for entry_id, fields in entries:
                raw = fields.get(b"documents", fields.get("documents"))
                yield QueuedBatch(batch_id=_text(entry_id), documents=json.loads(raw))

    async def acknowledge(self, topic: str, batch_id: str) -> None:
        """Підтвердження обробки пакета: XACK і XDEL однією транзакцією."""
        stream = self.STREAM_KEY.format(topic)
        async with self._client.pipeline(transaction=True) as pipe:
            pipe.xack(stream, self.GROUP, batch_id)
            pipe.xdel(stream, batch_id)
            await pipe.execute()

    async def _ensure_group(self, stream: str) -> None:
        """Група споживачів читає потік від початку: пакети, опубліковані до
        старту процесу запису, не губляться."""
        if stream in self._groups:
            return
        try:
            await self._client.xgroup_create(stream, self.GROUP, id="0", mkstream=True)
        except Exception as error:  # noqa: BLE001 — група вже існує (BUSYGROUP)
            if "BUSYGROUP" not in str(error):
                raise
        self._groups.add(stream)


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    raise TypeError(f"тип {type(value).__name__} не серіалізується")


def _text(value: Any) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)
