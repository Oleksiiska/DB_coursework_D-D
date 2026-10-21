"""Джерело: заздалегідь підготовлені файли (Шлях А, п. 2.1.4).

Розбір початкових форматів виконано офлайн скриптами підготовки,
тому тут лишається однорідне читання рядків із керованою швидкістю.
Той самий адаптер читає й списки, складені вручну (app/data/,
наприклад ігрові світи): вони надходять у чергу тим самим шляхом.
"""

from __future__ import annotations

import asyncio
import csv
import json
import sys
import time
from pathlib import Path
from typing import AsyncIterator, Iterator

from app.domain.values import SourceBatch, SourceStats


class CsvSource:
    """Реалізує порт `DocumentSource`.

    CSV читається потоково: набір на ~66 700 рядків не піднімається
    в пам'ять цілком. Список вручну — JSON-масив на кільканадцять
    записів. Кожен документ отримує вид і origin: csv для файлів зі скриптів
    підготовки, manual для списків вручну (п. 2.1.2). Колонки CSV мають
    назви полів моделі, переліки й вкладені об'єкти — у форматі JSON.
    """

    def __init__(self, path: Path, kind: str, rate: int = 1000, origin: str = "csv") -> None:
        self._path = Path(path)
        self._kind = kind
        self._rate = max(1, rate)
        self._origin = origin

    @property
    def name(self) -> str:
        return f"{self._origin}:{self._kind}"

    @property
    def path(self) -> Path:
        return self._path

    async def stream(self, batch_size: int = 500) -> AsyncIterator[SourceBatch]:
        """Потік пакетів із витримуванням цільової швидкості: пакет із n
        рядків відправляється не раніше, ніж через n / rate с після попереднього."""
        started = time.monotonic()
        sent = 0
        batch: list[dict] = []
        for row in self._rows():
            batch.append({**row, "kind": self._kind, "origin": self._origin})
            if len(batch) >= batch_size:
                sent += len(batch)
                yield SourceBatch(batch, SourceStats(requested=len(batch), succeeded=len(batch)))
                batch = []
                await self._pace(started, sent)
        if batch:
            yield SourceBatch(batch, SourceStats(requested=len(batch), succeeded=len(batch)))

    async def set_rate(self, documents_per_second: int) -> None:
        self._rate = max(1, int(documents_per_second))

    def _throttle_delay(self, batch_size: int) -> float:
        """Затримка між пакетами для досягнення цільової швидкості."""
        return batch_size / self._rate

    async def _pace(self, started: float, sent: int) -> None:
        delay = started + self._throttle_delay(sent) - time.monotonic()
        await asyncio.sleep(max(0.0, delay))

    def _rows(self) -> Iterator[dict]:
        if self._path.suffix == ".json":
            yield from json.loads(self._path.read_text(encoding="utf-8"))
            return
        csv.field_size_limit(sys.maxsize)
        with self._path.open(encoding="utf-8", newline="") as stream:
            for row in csv.DictReader(stream):
                yield {key: value for key, value in row.items() if key is not None}
