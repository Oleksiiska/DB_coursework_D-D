"""Процес генератора.

Винесений окремо: генерація навантажує процесор і в межах одного
процесу з асинхронним циклом подій блокувала б обслуговування
запитів користувача.
"""

from __future__ import annotations

from app.domain.ports import CacheStore, DocumentSource, EventPublisher


class GeneratorWorker:
    """Читає цільову швидкість зі спільного сховища перед кожним пакетом.

    Отримує порти, а не контейнер: залежності процесу видно з сигнатури.
    """

    RATE_KEY = "config:generator_rate"
    TOPIC = "documents"

    def __init__(self, source: DocumentSource, publisher: EventPublisher, cache: CacheStore) -> None:
        self._source = source
        self._publisher = publisher
        self._cache = cache
        self._running = False

    async def run(self) -> None:
        """Цикл: прочитати швидкість, згенерувати пакет, опублікувати."""
        raise NotImplementedError

    async def stop(self) -> None:
        raise NotImplementedError
