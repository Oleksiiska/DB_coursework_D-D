"""Джерело: заздалегідь підготовлені CSV-файли.

Розбір початкових форматів виконано офлайн скриптами підготовки,
тому тут лишається однорідне читання рядків із керованою швидкістю.
"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator


class CsvSource:
    """Реалізує порт `DocumentSource`.

    Читає файл потоково: набір на ~66 700 рядків не піднімається
    в пам'ять цілком.
    """

    def __init__(self, path: Path, kind: str, rate: int = 1000) -> None:
        self._path = path
        self._kind = kind
        self._rate = rate

    @property
    def name(self) -> str:
        return f"csv:{self._kind}"

    async def stream(self, batch_size: int = 500) -> AsyncIterator[list[dict]]:
        """Потік пакетів із витримуванням цільової швидкості."""
        raise NotImplementedError

    async def set_rate(self, documents_per_second: int) -> None:
        raise NotImplementedError

    def _throttle_delay(self, batch_size: int) -> float:
        """Затримка між пакетами для досягнення цільової швидкості."""
        raise NotImplementedError
