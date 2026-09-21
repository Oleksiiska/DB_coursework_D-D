"""Процес генератора.

Винесений окремо: генерація навантажує процесор і в межах одного
процесу з асинхронним циклом подій блокувала б обслуговування
запитів користувача.
"""

from __future__ import annotations

from app.infrastructure.config import Container, Settings


class GeneratorWorker:
    """Читає цільову швидкість зі спільного сховища перед кожним пакетом."""

    RATE_KEY = "config:generator_rate"
    POLL_INTERVAL = 1.0

    def __init__(self, container: Container) -> None:
        self._container = container
        self._running = False

    async def run(self) -> None:
        """Цикл: прочитати швидкість, згенерувати пакет, опублікувати."""
        raise NotImplementedError

    async def stop(self) -> None:
        raise NotImplementedError


async def main() -> None:
    raise NotImplementedError
