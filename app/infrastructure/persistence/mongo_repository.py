"""Адаптери документного сховища.

Реалізують порти репозиторіїв. Жоден із цих класів не імпортується
доменним шаром: зв'язок встановлює композиційний корінь.
"""

from __future__ import annotations

import collections.abc
import dataclasses
import enum
import logging
import types
import typing
from abc import ABC, abstractmethod
from datetime import date, datetime
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

log = logging.getLogger(__name__)

_SCALARS = (str, int, float, bool, datetime, date, bytes, type(None))


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

    @abstractmethod
    def key_of(self, entity: T) -> str:
        """Ключ документа (`_id`) — ідентифікатор сутності."""
        ...


class DataclassDocumentMapper(DocumentMapper[T]):
    """Універсальне відображення для сутностей-датакласів.

    Один клас обслуговує всі вісім колекцій: вкладені датакласи
    (відгуки, статблок, діапазони рівнів) розгортаються рекурсивно,
    переліки зберігаються значеннями, а при читанні типи відновлюються
    за анотаціями полів.

    Ключ документа складається з одного чи кількох полів: асоціативний
    клас «участь монстра» власного ключа не має й ідентифікується парою
    «зустріч — мітка екземпляра».
    """

    def __init__(self, entity_type: type[T], key_fields: tuple[str, ...]) -> None:
        if not dataclasses.is_dataclass(entity_type):
            raise TypeError(f"{entity_type!r} не є датакласом")
        self._entity_type = entity_type
        self._key_fields = key_fields

    # -- запис ---------------------------------------------------------------

    def key_of(self, entity: T) -> str:
        return ":".join(str(getattr(entity, name)) for name in self._key_fields)

    def to_document(self, entity: T) -> dict[str, Any]:
        document = self._encode(entity)
        document["_id"] = self.key_of(entity)
        return document

    def _encode(self, value: Any) -> Any:
        if isinstance(value, enum.Enum):
            return value.value
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            return {f.name: self._encode(getattr(value, f.name)) for f in dataclasses.fields(value)}
        if isinstance(value, (list, tuple, set, frozenset)):
            return [self._encode(item) for item in value]
        if isinstance(value, dict):
            return {str(k): self._encode(v) for k, v in value.items()}
        if isinstance(value, date) and not isinstance(value, datetime):
            return datetime(value.year, value.month, value.day)
        if isinstance(value, _SCALARS):
            return value
        raise TypeError(f"тип {type(value).__name__} не підтримується сховищем")

    # -- читання -------------------------------------------------------------

    def to_entity(self, document: dict[str, Any]) -> T:
        payload = {k: v for k, v in document.items() if k != "_id"}
        return self._decode(payload, self._entity_type)

    def _decode(self, value: Any, hint: Any) -> Any:
        if value is None or hint is Any:
            return value
        origin = typing.get_origin(hint)
        if origin in (typing.Union, types.UnionType):
            options = [a for a in typing.get_args(hint) if a is not type(None)]
            return self._decode(value, options[0]) if len(options) == 1 else value
        if isinstance(hint, type) and dataclasses.is_dataclass(hint):
            hints = typing.get_type_hints(hint)
            fields = {f.name for f in dataclasses.fields(hint) if f.init}
            return hint(**{k: self._decode(v, hints[k]) for k, v in value.items() if k in fields})
        if isinstance(hint, type) and issubclass(hint, enum.Enum):
            return hint(value)
        if origin in (list, set, frozenset, collections.abc.Sequence, collections.abc.Iterable):
            (item,) = typing.get_args(hint) or (Any,)
            container = origin if origin in (list, set, frozenset) else list
            return container(self._decode(v, item) for v in value)
        if origin is tuple:
            args = typing.get_args(hint)
            if len(args) == 2 and args[1] is Ellipsis:
                return tuple(self._decode(v, args[0]) for v in value)
            return tuple(self._decode(v, a) for v, a in zip(value, args))
        if origin is dict:
            _, item = typing.get_args(hint) or (Any, Any)
            return {k: self._decode(v, item) for k, v in value.items()}
        if hint is date and isinstance(value, datetime):
            return value.date()
        return value


class MongoCrudRepository(Generic[T]):
    """Узагальнена реалізація CRUD поверх документного сховища.

    Параметризується назвою колекції та відображенням. Автори, теги,
    ігрові світи й записи участі монстрів обслуговуються цим класом
    без підкласів; решта колекцій додає лише специфічні запити.

    Запис ідемпотентний (заміна з вставкою за ключем): черга може
    доставити пакет повторно, і повторна доставка не створює дублікатів.
    """

    def __init__(self, client: Any, collection: str, mapper: DocumentMapper[T], database: str = "dnd") -> None:
        self._client = client
        self._collection = collection
        self._mapper = mapper
        self._documents = client[database][collection]

    async def get(self, entity_id: str) -> T | None:
        document = await self._documents.find_one({"_id": entity_id})
        return None if document is None else self._mapper.to_entity(document)

    async def get_many(self, entity_ids: Sequence[str]) -> list[T]:
        """Пакетне читання зі збереженням порядку ключів після пошуку."""
        ids = list(entity_ids)
        found = await self._documents.find({"_id": {"$in": ids}}).to_list()
        by_id = {d["_id"]: d for d in found}
        return [self._mapper.to_entity(by_id[i]) for i in ids if i in by_id]

    async def add(self, entity: T) -> None:
        document = self._mapper.to_document(entity)
        await self._documents.replace_one({"_id": document["_id"]}, document, upsert=True)

    async def add_many(self, entities: Sequence[T]) -> int:
        """Невпорядкований пакетний запис: помилка одного документа
        не зупиняє решту пакета."""
        from pymongo import ReplaceOne
        from pymongo.errors import BulkWriteError

        if not entities:
            return 0
        operations = []
        for entity in entities:
            document = self._mapper.to_document(entity)
            operations.append(ReplaceOne({"_id": document["_id"]}, document, upsert=True))
        try:
            result = await self._documents.bulk_write(operations, ordered=False)
            return result.matched_count + result.upserted_count
        except BulkWriteError as error:
            details = error.details
            log.warning("%s: не записано %d документів", self._collection, len(details.get("writeErrors", [])))
            return details.get("nMatched", 0) + details.get("nUpserted", 0)

    async def update(self, entity: T) -> bool:
        document = self._mapper.to_document(entity)
        result = await self._documents.replace_one({"_id": document["_id"]}, document, upsert=False)
        return result.matched_count == 1

    async def delete(self, entity_id: str) -> bool:
        result = await self._documents.delete_one({"_id": entity_id})
        return result.deleted_count == 1

    async def count(self, spec: SearchSpecification | None = None) -> int:
        query = {} if spec is None or spec.is_empty() else self._build_query(spec)
        return await self._documents.count_documents(query)

    def _build_query(self, spec: SearchSpecification) -> dict[str, Any]:
        """Переклад доменної специфікації у мову запитів сховища.

        Узагальнений репозиторій фільтрів не знає; їх перекладають
        спеціалізовані репозиторії своїх колекцій.
        """
        raise NotImplementedError(f"{self._collection}: фільтри не підтримуються узагальненим репозиторієм")


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

    async def set_play_counts(self, counts: dict[str, int]) -> None:
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
