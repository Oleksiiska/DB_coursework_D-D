"""Сценарії повнотекстового пошуку — шлях запитів CQRS.

Три види пошуку (пригоди, монстри, зустрічі) мають однаковий порядок
кроків і відрізняються лише типом фільтрів і перетворенням сутності
на картку. Тому порядок закріплено в базовому класі (шаблонний метод),
а підкласи визначають лише змінні кроки.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Generic, TypeVar

from app.application.dto import (
    AdventureCardDTO,
    EncounterCardDTO,
    MonsterCardDTO,
    SearchQueryDTO,
    SearchResultDTO,
)
from app.domain.entities import Adventure, Encounter, Monster
from app.domain.ports import CacheStore, CrudRepository, MetricsSink, SearchIndex
from app.domain.services import QueryNormalizer
from app.domain.values import (
    AdventureFilters,
    DocumentKind,
    EncounterFilters,
    MonsterFilters,
    Snippet,
)

TEntity = TypeVar("TEntity")
TCard = TypeVar("TCard")


class CachedSearchHandler(ABC, Generic[TEntity, TCard]):
    """Базовий сценарій пошуку з кешуванням. Реалізує порт `SearchUseCase`.

    Залежить лише від інтерфейсів: у модульних тестах вони підмінюються
    заглушками, і жодна база для запуску тесту не потрібна.
    """

    #: Вид документа — входить у ключ кешу
    KIND: DocumentKind
    #: Очікуваний тип фільтрів
    FILTERS_TYPE: type
    #: Час життя запису в кеші
    CACHE_TTL_SECONDS = 300

    def __init__(
        self,
        index: SearchIndex,
        repository: CrudRepository[TEntity],
        cache: CacheStore,
        metrics: MetricsSink,
        normalizer: QueryNormalizer,
    ) -> None:
        self._index = index
        self._repository = repository
        self._cache = cache
        self._metrics = metrics
        self._normalizer = normalizer

    async def handle(self, query: SearchQueryDTO) -> SearchResultDTO[TCard]:
        """Шаблонний метод: нормалізація → кеш → індекс → сховище → кеш → метрика.

        При влучанні в кеш звернень до сховищ немає взагалі.
        """
        raise NotImplementedError

    # --- змінний крок, що визначається підкласами ---

    @abstractmethod
    def _to_card(self, entity: TEntity, snippets: list[Snippet]) -> TCard:
        ...

    # --- незмінні кроки ---

    def _check_filters(self, query: SearchQueryDTO) -> None:
        """Фільтри мають відповідати виду пошуку."""
        raise NotImplementedError

    async def _from_cache(self, key: str) -> SearchResultDTO[TCard] | None:
        raise NotImplementedError

    async def _from_storage(self, query: SearchQueryDTO) -> SearchResultDTO[TCard]:
        """Пошук в індексі, потім пакетне читання сутностей за ключами."""
        raise NotImplementedError

    async def _record(self, query: SearchQueryDTO, result: SearchResultDTO[TCard]) -> None:
        """Запис у журнал запитів — живить панель моніторингу."""
        raise NotImplementedError


class SearchAdventuresHandler(CachedSearchHandler[Adventure, AdventureCardDTO]):
    """Пошук пригод за текстом і фільтрами партії (групи запитів 1, 2, 9).

    Тексти вкладених відгуків індексуються разом із пригодою, тому
    пошук за текстом відгуку теж виконується тут.
    """

    KIND = DocumentKind.ADVENTURE
    FILTERS_TYPE = AdventureFilters

    def _to_card(self, entity: Adventure, snippets: list[Snippet]) -> AdventureCardDTO:
        raise NotImplementedError


class SearchMonstersHandler(CachedSearchHandler[Monster, MonsterCardDTO]):
    """Пошук монстрів за описом здібності та підбір за рівнем
    небезпеки й середовищем (групи запитів 1, 3)."""

    KIND = DocumentKind.MONSTER
    FILTERS_TYPE = MonsterFilters

    def _to_card(self, entity: Monster, snippets: list[Snippet]) -> MonsterCardDTO:
        raise NotImplementedError


class SearchEncountersHandler(CachedSearchHandler[Encounter, EncounterCardDTO]):
    """Пошук реальних зустрічей за текстом наративу ведучого (група 1)."""

    KIND = DocumentKind.ENCOUNTER
    FILTERS_TYPE = EncounterFilters

    def _to_card(self, entity: Encounter, snippets: list[Snippet]) -> EncounterCardDTO:
        raise NotImplementedError
