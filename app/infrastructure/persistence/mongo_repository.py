"""Адаптери документного сховища.

Реалізують порти репозиторіїв. Жоден із цих класів не імпортується
доменним шаром: зв'язок встановлює композиційний корінь.
"""

from __future__ import annotations

import collections.abc
import dataclasses
import enum
import itertools
import logging
import time
import types
import typing
from abc import ABC, abstractmethod
from datetime import date, datetime
from typing import Any, Callable, Generic, Sequence, TypeVar

from app.domain.entities import Adventure, Encounter, Monster, PlayerCharacter
from app.domain.values import (
    AdventureFilters,
    EncounterFilters,
    MonsterFilters,
    Page,
    SearchSpecification,
    SortOrder,
)

T = TypeVar("T")

#: Журнал підсистеми документного сховища (п. 4.10): на рівні DEBUG —
#: тривалість кожного звернення й вибірковий explain кожного сотого запиту
log = logging.getLogger("db.mongo")

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

    Для шардованої колекції композиційний корінь передає функцію ключа
    розподілу (``ShardKeyService.key_value``). Тоді кожен запит за
    ідентифікатором містить і shard_key, тож маршрутизатор надсилає його
    на один шард, а не на всі (таблиця 4.7); запис без shard_key отримує
    його тут, і ключі запису й читання не можуть розійтися.
    """

    #: Поле ключа розподілу в документі
    SHARD_KEY_FIELD = "shard_key"
    #: Вторинні індекси колекції: (поля, параметри); створюються на старті
    INDEXES: tuple[tuple[list[tuple[str, int]], dict[str, Any]], ...] = ()
    #: На рівні DEBUG кожен сотий запит до шардованої колекції виконується
    #: ще й з explain (executionStats) — час відповіді кожного шарда (таблиця 4.17)
    EXPLAIN_EVERY = 100
    #: Коди помилок, що стосуються самого документа, а не сховища: повтор
    #: пакета їх не виправить (BadValue, ShardKeyNotFound, ImmutableField,
    #: DocumentValidationFailure, BSONObjectTooLarge, DuplicateKey)
    DOCUMENT_ERROR_CODES = frozenset({2, 61, 66, 121, 10334, 11000})

    def __init__(
        self,
        client: Any,
        collection: str,
        mapper: DocumentMapper[T],
        database: str = "dnd",
        shard_key_of: Callable[[str], str] | None = None,
    ) -> None:
        self._client = client
        self._collection = collection
        self._mapper = mapper
        self._documents = client[database][collection]
        self._shard_key_of = shard_key_of
        self._reads = itertools.count(1)

    async def ensure_indexes(self) -> None:
        """Вторинні індекси для запитів колекції; повторний виклик нічого не змінює."""
        for keys, options in self.INDEXES:
            await self._documents.create_index(keys, **options)

    def _by_id(self, entity_id: str) -> dict[str, Any]:
        """Фільтр за ключем; для шардованої колекції — точковий."""
        query: dict[str, Any] = {"_id": entity_id}
        if self._shard_key_of is not None:
            query[self.SHARD_KEY_FIELD] = self._shard_key_of(entity_id)
        return query

    def _to_document(self, entity: T) -> dict[str, Any]:
        document = self._mapper.to_document(entity)
        if self._shard_key_of is not None:
            expected = self._shard_key_of(document["_id"])
            actual = document.get(self.SHARD_KEY_FIELD)
            if actual is None:
                document[self.SHARD_KEY_FIELD] = expected
            elif actual != expected:
                raise ValueError(
                    f"{self._collection}: shard_key {actual!r} не відповідає ключу {document['_id']!r}"
                )
        return document

    def _write_filter(self, document: dict[str, Any]) -> dict[str, Any]:
        query: dict[str, Any] = {"_id": document["_id"]}
        if self._shard_key_of is not None:
            query[self.SHARD_KEY_FIELD] = document[self.SHARD_KEY_FIELD]
        return query

    async def get(self, entity_id: str) -> T | None:
        document = await self._documents.find_one(self._by_id(entity_id))
        return None if document is None else self._mapper.to_entity(document)

    async def get_many(self, entity_ids: Sequence[str]) -> list[T]:
        """Пакетне читання зі збереженням порядку ключів після пошуку.

        Для шардованої колекції запит іде лише на шарди, що володіють
        ключами розподілу цих документів. Ключі, яких ще немає в сховищі
        (документ уже в індексі, але ще не записаний), пропускаються (п. 4.4.2).
        """
        ids = list(entity_ids)
        if not ids:
            return []
        query: dict[str, Any] = {"_id": {"$in": ids}}
        if self._shard_key_of is not None:
            query[self.SHARD_KEY_FIELD] = {"$in": sorted({self._shard_key_of(i) for i in ids})}
        started = time.perf_counter()
        found = await self._documents.find(query).to_list()
        if log.isEnabledFor(logging.DEBUG):
            await self._debug_read("get_many", query, len(found), started)
        by_id = {d["_id"]: d for d in found}
        return [self._mapper.to_entity(by_id[i]) for i in ids if i in by_id]

    async def _debug_read(self, operation: str, query: dict[str, Any], count: int, started: float) -> None:
        """Тривалість читання; для шардованої колекції кожне соте — з explain."""
        message = f"{self._collection}.{operation} {count} док. — {(time.perf_counter() - started) * 1000:.0f} мс"
        if self._shard_key_of is not None and next(self._reads) % self.EXPLAIN_EVERY == 0:
            try:
                plan = await self._documents.database.command(
                    {"explain": {"find": self._collection, "filter": query}, "verbosity": "executionStats"}
                )
                message += f"; explain: {self._shard_times(plan)}"
            except Exception as error:  # noqa: BLE001 — діагностика не має ламати читання
                message += f"; explain недоступний: {error}"
        log.debug(message)

    @staticmethod
    def _shard_times(plan: dict[str, Any]) -> str:
        """Час виконання на кожному шарді з результату explain."""
        stats = plan.get("executionStats", {})
        shards = stats.get("executionStages", {}).get("shards") or []
        if not shards:
            return f"{stats.get('executionTimeMillis', '?')} мс"
        return ", ".join(
            f"{shard.get('shardName', '?')} {shard.get('executionTimeMillis', shard.get('executionTimeMillisEstimate', '?'))} мс"
            for shard in shards
        )

    async def add(self, entity: T) -> None:
        document = self._to_document(entity)
        await self._documents.replace_one(self._write_filter(document), document, upsert=True)

    async def add_many(self, entities: Sequence[T]) -> int:
        """Невпорядкований пакетний запис.

        Помилка окремого документа (повтор ключа, невалідний документ) не
        зупиняє решту пакета. Збій самого сховища — недоступний шард,
        невиконана вимога підтвердження запису — піднімається вище: пакет
        лишається непідтвердженим і надходить повторно, а не губиться (п. 4.4).
        """
        from pymongo import ReplaceOne
        from pymongo.errors import BulkWriteError

        if not entities:
            return 0
        operations = []
        for entity in entities:
            document = self._to_document(entity)
            operations.append(ReplaceOne(self._write_filter(document), document, upsert=True))
        started = time.perf_counter()
        try:
            result = await self._documents.bulk_write(operations, ordered=False)
            log.debug("%s.add_many %d док. — %.0f мс", self._collection, len(operations),
                      (time.perf_counter() - started) * 1000)
            return result.matched_count + result.upserted_count
        except BulkWriteError as error:
            details = error.details
            errors = details.get("writeErrors", [])
            if details.get("writeConcernErrors") or any(
                e.get("code") not in self.DOCUMENT_ERROR_CODES for e in errors
            ):
                raise
            log.warning("%s: документів не записано: %d", self._collection, len(errors))
            return details.get("nMatched", 0) + details.get("nUpserted", 0)

    async def update(self, entity: T) -> bool:
        document = self._to_document(entity)
        result = await self._documents.replace_one(self._write_filter(document), document, upsert=False)
        return result.matched_count == 1

    async def delete(self, entity_id: str) -> bool:
        result = await self._documents.delete_one(self._by_id(entity_id))
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
    """Шардована колекція пригод: єдина, що необмежено росте під
    навантаженням генератора (п. 4.3). Композиційний корінь передає їй
    функцію ключа розподілу."""

    COLLECTION = "adventures"
    INDEXES = (
        ([("author_ids", 1)], {}),
        ([("tags", 1)], {}),
        ([("setting_id", 1), ("levels.minimum", 1)], {}),
        ([("published_year", -1)], {}),
    )
    #: Порядок видачі в документному сховищі; релевантності тут немає,
    #: тож за замовчуванням — від новіших за роком публікації (п. 4.6)
    SORT_FIELDS: dict[SortOrder, tuple[str, int]] = {
        SortOrder.RELEVANCE: ("published_year", -1),
        SortOrder.RATING: ("rating_avg", -1),
        SortOrder.REVIEWS: ("reviews_count", -1),
        SortOrder.YEAR: ("published_year", -1),
        SortOrder.DURATION: ("duration_hours", 1),
    }

    def _build_query(self, spec: SearchSpecification) -> dict[str, Any]:
        if not isinstance(spec, AdventureFilters):
            raise TypeError(f"adventures: очікувалися AdventureFilters, отримано {type(spec).__name__}")
        query: dict[str, Any] = {}
        if spec.level is not None:
            query["levels.minimum"] = {"$lte": spec.level}
            query["levels.maximum"] = {"$gte": spec.level}
        if spec.players is not None:
            query["party.minimum"] = {"$lte": spec.players}
            query["party.maximum"] = {"$gte": spec.players}
        for name in ("system", "difficulty", "setting_id", "publication_language"):
            if getattr(spec, name) is not None:
                query[name] = getattr(spec, name)
        if spec.tags:
            query["tags"] = {"$all": list(spec.tags)}
        if spec.is_free is True:
            query["$or"] = [{"price.amount": None}, {"price.amount": 0}]
        elif spec.is_free is False:
            query["price.amount"] = {"$gt": 0}
        if spec.max_duration_hours is not None:
            query["duration_hours"] = {"$lte": spec.max_duration_hours}
        years = {k: v for k, v in (("$gte", spec.year_from), ("$lte", spec.year_to)) if v is not None}
        if years:
            query["published_year"] = years
        return query

    async def find_by_filters(
        self, filters: AdventureFilters, page: Page, sort: SortOrder = SortOrder.RELEVANCE
    ) -> list[Adventure]:
        field_name, direction = self.SORT_FIELDS[sort]
        pipeline: list[dict[str, Any]] = [{"$match": self._build_query(filters) if not filters.is_empty() else {}}]
        if field_name == "reviews_count":
            pipeline.append({"$addFields": {"reviews_count": {"$size": {"$ifNull": ["$reviews", []]}}}})
        pipeline += [{"$sort": {field_name: direction, "_id": 1}}, {"$skip": page.offset}, {"$limit": page.size}]
        if field_name == "reviews_count":
            pipeline.append({"$unset": "reviews_count"})
        cursor = await self._documents.aggregate(pipeline)
        return [self._mapper.to_entity(document) for document in await cursor.to_list()]

    async def find_by_author(self, author_id: str, limit: int = 50) -> list[Adventure]:
        cursor = self._documents.find({"author_ids": author_id}).sort([("published_year", -1), ("_id", 1)]).limit(limit)
        return [self._mapper.to_entity(document) for document in await cursor.to_list()]

    async def find_chain(self, setting_id: str, level_from: int, level_to: int) -> list[Adventure]:
        query = {
            "setting_id": setting_id,
            "levels.minimum": {"$lte": level_to},
            "levels.maximum": {"$gte": level_from},
        }
        cursor = self._documents.find(query).sort([("levels.minimum", 1), ("_id", 1)]).limit(500)
        return [self._mapper.to_entity(document) for document in await cursor.to_list()]

    async def count_by_shard(self) -> dict[str, int]:
        """Етап $collStats з параметром count: кількість документів на кожному
        шарді з метаданих колекції, без сканування документів (п. 4.9).
        На одному вузлі без шардування — кількість на цьому вузлі."""
        from pymongo.errors import OperationFailure

        try:
            cursor = await self._documents.aggregate([{"$collStats": {"count": {}}}])
            documents = await cursor.to_list()
        except OperationFailure as error:
            if error.code == 26:  # NamespaceNotFound — колекцію ще не створено
                return {}
            raise
        counts = {str(d.get("shard") or d.get("host") or "mongo"): int(d.get("count", 0)) for d in documents}
        return dict(sorted(counts.items()))

    async def count_in_zones(self, bounds: Sequence[tuple[str | None, str | None]]) -> list[int]:
        """Точні кількості за зонами для перевірки рівномірності (розділ 6):
        countDocuments за діапазоном shard_key зони — маршрутизатор надсилає
        такий запит на один шард."""
        counts = []
        for lower, upper in bounds:
            condition = {k: v for k, v in (("$gte", lower), ("$lt", upper)) if v is not None}
            counts.append(await self._documents.count_documents({self.SHARD_KEY_FIELD: condition} if condition else {}))
        return counts

    async def author_stats(self, author_ids: Sequence[str]) -> dict[str, dict]:
        ids = list(author_ids)
        if not ids:
            return {}
        pipeline = [
            {"$match": {"author_ids": {"$in": ids}}},
            {"$project": {"_id": 0, "authors": "$author_ids", "author": "$author_ids",
                          "rating_avg": 1, "published_year": 1}},
            {"$unwind": "$author"},
            {"$match": {"author": {"$in": ids}}},
            {"$group": {
                "_id": "$author",
                "adventures_count": {"$sum": 1},
                "avg_rating": {"$avg": "$rating_avg"},
                "first_published_year": {"$min": "$published_year"},
                "co": {"$addToSet": "$authors"},
            }},
            {"$project": {
                "adventures_count": 1,
                "avg_rating": 1,
                "first_published_year": 1,
                "co_author_ids": {"$setDifference": [
                    {"$reduce": {"input": "$co", "initialValue": [], "in": {"$setUnion": ["$$value", "$$this"]}}},
                    ["$_id"],
                ]},
            }},
        ]
        cursor = await self._documents.aggregate(pipeline)
        result = {}
        for document in await cursor.to_list():
            author = document.pop("_id")
            if document.get("avg_rating") is not None:
                document["avg_rating"] = round(document["avg_rating"], 2)
            document["co_author_ids"] = sorted(document.get("co_author_ids") or [])
            result[author] = document
        return result


class MongoMonsterRepository(MongoCrudRepository[Monster]):
    COLLECTION = "monsters"
    INDEXES = (
        ([("play_count", -1)], {}),
        ([("challenge_rating", 1)], {}),
    )

    def _build_query(self, spec: SearchSpecification) -> dict[str, Any]:
        if not isinstance(spec, MonsterFilters):
            raise TypeError(f"monsters: очікувалися MonsterFilters, отримано {type(spec).__name__}")
        query: dict[str, Any] = {}
        low, high = spec.challenge_bounds()
        bounds = {k: v for k, v in (("$gte", low), ("$lte", high)) if v is not None}
        if bounds:
            query["challenge_rating"] = bounds
        if spec.creature_type is not None:
            query["type"] = spec.creature_type
        if spec.environment is not None:
            query["environment"] = spec.environment
        return query

    async def find_by_filters(self, filters: MonsterFilters, page: Page) -> list[Monster]:
        query = {} if filters.is_empty() else self._build_query(filters)
        cursor = self._documents.find(query).sort([("challenge_rating", 1), ("_id", 1)]).skip(page.offset).limit(page.size)
        return [self._mapper.to_entity(document) for document in await cursor.to_list()]

    async def most_played(self, limit: int = 20) -> list[Monster]:
        cursor = self._documents.find({}).sort([("play_count", -1), ("_id", 1)]).limit(limit)
        return [self._mapper.to_entity(document) for document in await cursor.to_list()]

    async def set_play_counts(self, counts: dict[str, int]) -> None:
        from pymongo import UpdateOne

        if counts:
            await self._documents.bulk_write(
                [UpdateOne({"_id": monster_id}, {"$set": {"play_count": count}}) for monster_id, count in counts.items()],
                ordered=False,
            )


class MongoCharacterRepository(MongoCrudRepository[PlayerCharacter]):
    COLLECTION = "characters"
    INDEXES = (
        ([("char_class", 1), ("level", 1)], {}),
        ([("race", 1)], {}),
    )

    async def typical_spells(self, char_class: str, level: int, limit: int = 10) -> list[tuple[str, int]]:
        """Агрегація по вкладеному масиву заклять."""
        pipeline = [
            {"$match": {"char_class": char_class, "level": level}},
            {"$unwind": "$spells"},
            {"$group": {"_id": "$spells", "n": {"$sum": 1}}},
            {"$sort": {"n": -1, "_id": 1}},
            {"$limit": limit},
        ]
        cursor = await self._documents.aggregate(pipeline)
        return [(d["_id"], d["n"]) for d in await cursor.to_list()]

    async def class_race_distribution(self) -> list[tuple[str, str, int]]:
        pipeline = [
            {"$group": {"_id": {"c": "$char_class", "r": "$race"}, "n": {"$sum": 1}}},
            {"$sort": {"n": -1, "_id.c": 1, "_id.r": 1}},
        ]
        cursor = await self._documents.aggregate(pipeline)
        return [(d["_id"].get("c"), d["_id"].get("r"), d["n"]) for d in await cursor.to_list()]


class MongoEncounterRepository(MongoCrudRepository[Encounter]):
    COLLECTION = "encounters"
    INDEXES = (([("is_abandoned", 1)], {}),)

    def _build_query(self, spec: SearchSpecification) -> dict[str, Any]:
        if not isinstance(spec, EncounterFilters):
            raise TypeError(f"encounters: очікувалися EncounterFilters, отримано {type(spec).__name__}")
        query: dict[str, Any] = {}
        if spec.command is not None:
            query["commands"] = spec.command
        rounds = {k: v for k, v in (("$gte", spec.min_rounds), ("$lte", spec.max_rounds)) if v is not None}
        if rounds:
            query["rounds_count"] = rounds
        if spec.exclude_abandoned:
            query["is_abandoned"] = False
        return query

    async def _median(self, field_name: str, exclude_abandoned: bool) -> float | None:
        """Медіана в групуванні обчислюється наближено (method: approximate) [13]."""
        match: dict[str, Any] = {field_name: {"$ne": None}}
        if exclude_abandoned:
            match["is_abandoned"] = False
        pipeline = [
            {"$match": match},
            {"$group": {"_id": None, "m": {"$median": {"input": f"${field_name}", "method": "approximate"}}}},
        ]
        cursor = await self._documents.aggregate(pipeline)
        documents = await cursor.to_list()
        return None if not documents or documents[0].get("m") is None else float(documents[0]["m"])

    async def median_duration(self, exclude_abandoned: bool = True) -> float | None:
        """Медіана, не середнє: розподіл містить викиди на три порядки."""
        return await self._median("duration_seconds", exclude_abandoned)

    async def median_rounds(self, exclude_abandoned: bool = True) -> float | None:
        return await self._median("rounds_count", exclude_abandoned)

    async def party_composition(self, level: int | None = None) -> list[tuple[str, int]]:
        """Склад партій реальних боїв за класами персонажів гравців."""
        pipeline: list[dict[str, Any]] = [
            {"$match": {"is_abandoned": False}},
            {"$project": {"player_character_ids": 1}},
            {"$unwind": "$player_character_ids"},
            {"$lookup": {"from": "characters", "localField": "player_character_ids", "foreignField": "_id", "as": "pc"}},
            {"$unwind": "$pc"},
        ]
        if level is not None:
            pipeline.append({"$match": {"pc.level": level}})
        pipeline += [
            {"$group": {"_id": "$pc.char_class", "n": {"$sum": 1}}},
            {"$sort": {"n": -1, "_id": 1}},
        ]
        cursor = await self._documents.aggregate(pipeline)
        return [(d["_id"], d["n"]) for d in await cursor.to_list()]
