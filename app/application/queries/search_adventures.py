"""Сценарій «пошук пригод» — шлях запитів CQRS (п. 4.9.1)."""

from __future__ import annotations

from app.application.dto import SearchQueryDTO, SearchResultDTO
from app.domain.ports import (
    AdventureRepository,
    CacheStore,
    MetricsSink,
    SearchIndex,
)
from app.domain.services import QueryNormalizer


class SearchAdventuresHandler:
    """Реалізує вхідний порт `SearchAdventuresUseCase`.

    Залежить лише від інтерфейсів: у модульних тестах вони підмінюються
    заглушками, і жодна база для запуску тесту не потрібна.
    """

    #: Час життя запису в кеші
    CACHE_TTL_SECONDS = 300

    def __init__(
        self,
        index: SearchIndex,
        cache: CacheStore,
        repository: AdventureRepository,
        metrics: MetricsSink,
        normalizer: QueryNormalizer,
    ) -> None:
        self._index = index
        self._cache = cache
        self._repository = repository
        self._metrics = metrics
        self._normalizer = normalizer

    async def handle(self, query: SearchQueryDTO) -> SearchResultDTO:
        """Порядок: нормалізація → кеш → індекс → сховище → кеш → метрика.

        При влучанні в кеш звернень до сховищ немає взагалі.
        """
        raise NotImplementedError

    async def _from_cache(self, key: str) -> SearchResultDTO | None:
        raise NotImplementedError

    async def _from_storage(self, query: SearchQueryDTO) -> SearchResultDTO:
        """Пошук в індексі, потім пакетне читання карток за ключами."""
        raise NotImplementedError

    async def _record(self, query: SearchQueryDTO, result: SearchResultDTO) -> None:
        """Запис у журнал запитів — живить панель моніторингу."""
        raise NotImplementedError
