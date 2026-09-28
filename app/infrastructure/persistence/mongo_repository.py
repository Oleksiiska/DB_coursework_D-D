"""Адаптери документного сховища.

Реалізують порти репозиторіїв. Жоден із цих класів не імпортується
доменним шаром: зв'язок встановлює композиційний корінь.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Generic, Sequence, TypeVar

from app.domain.entities import Adventure, Encounter, Monster, PlayerCharacter
from app.domain.values import (
    AdventureFilters,
    MonsterFilters,
    Page,
    SearchSpecification,
    SortOrder,
)

T = TypeVar("T")


class DocumentMapper(ABC, Generic[T]):
    """Відображення документа сховища в сутність і навпаки.

    Винесено окремо від репозиторію, щоб адаптер лишався однаковим
    для всіх колекцій.
    """

    @abstractmethod
    def to_entity(self, document: dict[str, Any]) -> T:
        ...

    @abstractmethod
    def to_document(self, entity: T) -> dict[str, Any]:
        ...


class DataclassDocumentMapper(DocumentMapper[T]):
    """Універсальне відображення для сутностей-датакласів.

    Один клас обслуговує всі вісім колекцій: вкладені датакласи
    (відгуки, статблок, участь монстрів) розгортаються рекурсивно.
    """

    def __init__(self, entity_type: type[T]) -> None:
        self._entity_type = entity_type

    def to_entity(self, document: dict[str, Any]) -> T:
        raise NotImplementedError

    def to_document(self, entity: T) -> dict[str, Any]:
        raise NotImplementedError


class MongoCrudRepository(Generic[T]):
    """Узагальнена реалізація CRUD поверх документного сховища.

    Параметризується назвою колекції та відображенням. Автори, теги,
    ігрові світи й записи участі монстрів обслуговуються цим класом
    без підкласів; решта колекцій додає лише специфічні запити.
    """

    def __init__(self, client: Any, collection: str, mapper: DocumentMapper[T]) -> None:
        self._client = client
        self._collection = collection
        self._mapper = mapper

    async def get(self, entity_id: str) -> T | None:
        raise NotImplementedError

    async def get_many(self, entity_ids: Sequence[str]) -> list[T]:
        """Пакетне читання зі збереженням порядку ключів після пошуку."""
        raise NotImplementedError

    async def add(self, entity: T) -> None:
        raise NotImplementedError

    async def add_many(self, entities: Sequence[T]) -> int:
        """Невпорядкований пакетний запис: помилка одного документа
        не зупиняє решту пакета."""
        raise NotImplementedError

    async def update(self, entity: T) -> bool:
        raise NotImplementedError

    async def delete(self, entity_id: str) -> bool:
        raise NotImplementedError

    async def count(self, spec: SearchSpecification | None = None) -> int:
        raise NotImplementedError

    def _build_query(self, spec: SearchSpecification) -> dict[str, Any]:
        """Переклад доменної специфікації у мову запитів сховища."""
        raise NotImplementedError


class MongoAdventureRepository(MongoCrudRepository[Adventure]):
    """Шардована колекція пригод."""

    COLLECTION = "adventures"

    async def find_by_filters(
        self, filters: AdventureFilters, page: Page, sort: SortOrder = SortOrder.RELEVANCE
    ) -> list[Adventure]:
        raise NotImplementedError

    async def find_by_author(self, author_id: str) -> list[Adventure]:
        raise NotImplementedError

    async def find_chain(self, setting_id: str, level_from: int, level_to: int) -> list[Adventure]:
        raise NotImplementedError


class MongoMonsterRepository(MongoCrudRepository[Monster]):
    COLLECTION = "monsters"

    async def find_by_filters(self, filters: MonsterFilters, page: Page) -> list[Monster]:
        raise NotImplementedError

    async def most_played(self, limit: int = 20) -> list[Monster]:
        raise NotImplementedError

    async def bump_play_counts(self, counts: dict[str, int]) -> None:
        raise NotImplementedError


class MongoCharacterRepository(MongoCrudRepository[PlayerCharacter]):
    COLLECTION = "characters"

    async def typical_spells(self, char_class: str, level: int, limit: int = 10) -> list[tuple[str, int]]:
        """Агрегація по вкладеному масиву заклять."""
        raise NotImplementedError

    async def class_race_distribution(self) -> list[tuple[str, str, int]]:
        raise NotImplementedError


class MongoEncounterRepository(MongoCrudRepository[Encounter]):
    COLLECTION = "encounters"

    async def median_duration(self, exclude_abandoned: bool = True) -> float:
        """Медіана, не середнє: розподіл містить викиди на три порядки."""
        raise NotImplementedError

    async def median_rounds(self, exclude_abandoned: bool = True) -> float:
        raise NotImplementedError

    async def party_composition(self, level: int | None = None) -> list[tuple[str, int]]:
        raise NotImplementedError
