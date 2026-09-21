"""Процес запису: споживання черги та пакетний запис у сховища."""

from __future__ import annotations

from app.infrastructure.config import Container


class IngestWorker:
    """Забирає пакети з черги й передає сценарію приймання.

    Пакетний запис зменшує кількість звернень до сховищ на два порядки
    і є основним чинником пропускної здатності шляху команд.
    """

    def __init__(self, container: Container, batch_size: int = 500) -> None:
        self._container = container
        self._batch_size = batch_size
        self._running = False

    async def run(self) -> None:
        raise NotImplementedError

    async def stop(self) -> None:
        """Зупинка після завершення поточного пакета."""
        raise NotImplementedError


async def main() -> None:
    raise NotImplementedError
