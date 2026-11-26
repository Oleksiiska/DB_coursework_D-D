"""Адаптери документного сховища.

Реалізують порти репозиторіїв. Жоден із цих класів не імпортується
доменним шаром: зв'язок встановлює композиційний корінь.
"""

from __future__ import annotations

import asyncio
import collections.abc
import dataclasses
import enum
import itertools
import logging
import time
import types
import typing
from abc import ABC, abstractmethod
from datetime import date, datetime, timezone
from typing import Any, Awaitable, Callable, Generic, Sequence, TypeVar

from app.domain.entities import Adventure, Encounter, Monster, PlayerCharacter
from app.domain.values import (
    PLAY_TIERS,
    RATING_BINS,
    AdventureFilters,
    CatalogDimension,
    CatalogScope,
    EncounterFilters,
    MergedAggregate,
    MonsterFilters,
    NicheStats,
    ReadSnapshot,
    SearchSpecification,
    ZoneBounds,
    ZonePartial,
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
            for shard in sorted(shards, key=lambda s: str(s.get("shardName", "")))
        )

    async def add(self, entity: T) -> None:
        document = self._to_document(entity)
        await self._documents.replace_one(self._write_filter(document), document, upsert=True)

    async def add_many(self, entities: Sequence[T]) -> int:
        """Невпорядкований пакетний запис.

        Помилка окремого документа (повтор ключа, невалідний документ, зокрема
        такий, що драйвер не може закодувати в BSON) не зупиняє решту пакета:
        документ пропускається з попередженням. Збій самого сховища —
        недоступний шард, невиконана вимога підтвердження запису — піднімається
        вище як ConnectionError зі стислим описом: пакет лишається
        непідтвердженим і надходить повторно, а не губиться (п. 4.4).
        """
        if not entities:
            return 0
        return await self._write([self._to_document(entity) for entity in entities])

    async def _write(self, documents: list[dict]) -> int:
        from bson import DEFAULT_CODEC_OPTIONS
        from bson import encode as bson_encode
        from bson.errors import InvalidDocument
        from pymongo import ReplaceOne
        from pymongo.errors import BulkWriteError

        operations = [ReplaceOne(self._write_filter(document), document, upsert=True) for document in documents]
        started = time.perf_counter()
        try:
            result = await self._documents.bulk_write(operations, ordered=False)
            log.debug("%s.add_many %d док. — %.0f мс", self._collection, len(operations),
                      (time.perf_counter() - started) * 1000)
            return result.matched_count + result.upserted_count
        except (OverflowError, InvalidDocument):
            # Драйвер кодує пакет до надсилання, тож один документ, якого не можна
            # подати в BSON (ціле понад 64 біти, непідтримуваний тип), зупинив би
            # весь пакет — і той надходив би повторно без кінця
            codec_options = getattr(self._documents, "codec_options", DEFAULT_CODEC_OPTIONS)
            valid = []
            for document in documents:
                try:
                    bson_encode(document, codec_options=codec_options)
                    valid.append(document)
                except (OverflowError, InvalidDocument) as reason:
                    log.warning("%s: документ %s не записано: %s", self._collection, document.get("_id"), reason)
            if len(valid) == len(documents):
                raise
            return await self._write(valid) if valid else 0
        except BulkWriteError as error:
            details = error.details
            errors = details.get("writeErrors", [])
            failures = details.get("writeConcernErrors") or [
                e for e in errors if e.get("code") not in self.DOCUMENT_ERROR_CODES
            ]
            if failures:
                # Збій сховища, а не документа: стисле повідомлення замість повного
                # переліку операцій із документами, який займав би сотні кілобайтів журналу
                first = failures[0]
                raise ConnectionError(
                    f"{self._collection}: {first.get('errmsg', 'помилка запису')} "
                    f"(код {first.get('code')}, не записано {len(errors) or len(operations)} з {len(operations)})"
                ) from error
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
    #: Роботи автора від новіших і статистика авторів; кількості пригод
    #: тегу й світу для похідних лічильників (п. 4.11)
    INDEXES = (
        ([("author_ids", 1), ("published_year", -1)], {}),
        ([("tags", 1)], {}),
        ([("setting_id", 1)], {}),
    )
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

    async def find_by_author(self, author_id: str, limit: int = 50) -> list[Adventure]:
        cursor = self._documents.find({"author_ids": author_id}).sort([("published_year", -1), ("_id", 1)]).limit(limit)
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

    # --- аналітика ніш: етап map на шарді зони і звірка (п. 4.12) ---

    #: Межа тривалості одного запиту аналітики; розмір першої порції відповіді —
    #: усі групи зони вміщуються в неї, тож запит не потребує getMore
    NICHE_MAX_TIME_MS = 120_000
    NICHE_BATCH_SIZE = 10_000
    #: Походження пригод каталогу itch.io (п. 2.1.2)
    COLLECTED_ORIGINS = CatalogScope.COLLECTED.origins
    #: Коди помилок, за яких проба знімка повторюється з іншою зоною, а не
    #: вимикається: недоступний шард, мережа, вибори первинного вузла, ще не
    #: створена колекція
    TRANSIENT_CODES = frozenset({6, 7, 26, 89, 91, 133, 189, 202, 262, 9001, 10107, 11600, 11602, 13435, 13436})
    #: Стан, що з'ясовується на першому запиті й далі не перевіряється: чи
    #: підтримує сховище читання snapshot і підказку $natural в агрегації
    _snapshot_unsupported = False
    _natural_hint = True

    async def read_snapshot(self, zone: ZoneBounds | None = None) -> ReadSnapshot | None:
        """Проба читання з рівнем узгодженості snapshot у зоні `zone`: сховище
        саме обирає момент atClusterTime і повертає його у відповіді курсора.
        На одному вузлі без набору реплік таке читання не підтримується — це
        запам'ятовується, і далі проба не виконується."""
        from pymongo.errors import OperationFailure, PyMongoError

        if self._snapshot_unsupported:
            return None
        probe = {self.SHARD_KEY_FIELD: (zone.lower if zone is not None and zone.lower else "0000"),
                 "_id": "__snapshot_probe__"}
        command = {"aggregate": self._collection, "pipeline": [{"$match": probe}, {"$limit": 1}],
                   "cursor": {}, "readConcern": {"level": "snapshot"}}
        try:
            reply = await self._documents.database.command(command)
        except OperationFailure as error:
            if error.code not in self.TRANSIENT_CODES:
                self._snapshot_unsupported = True
                log.warning("%s: читання snapshot не підтримується (%s) — зони читаються без спільного знімка",
                            self._collection, self._brief(error))
            else:
                log.warning("%s: проба знімка в зоні %s не вдалася: %s", self._collection,
                            zone.label if zone else "—", self._brief(error))
            return None
        except PyMongoError as error:
            log.warning("%s: проба знімка в зоні %s не вдалася: %s", self._collection,
                        zone.label if zone else "—", self._brief(error))
            return None
        at = (reply.get("cursor") or {}).get("atClusterTime")
        if at is None:
            return None
        return ReadSnapshot(token=f"{at.time}.{at.inc}", at=datetime.fromtimestamp(at.time, tz=timezone.utc))

    async def niche_partial(
        self,
        zone: ZoneBounds,
        dimension: CatalogDimension,
        scope: CatalogScope,
        snapshot: ReadSnapshot | None = None,
    ) -> ZonePartial:
        """Етап map: групування документів зони на її шарді.

        Фільтр за діапазоном shard_key потрібен маршрутизатору: за ним запит
        надсилається лише на шард зони. На самому шарді зона — це всі його
        документи, тож запит переглядає колекцію в природному порядку, як і
        вбудована агрегація, з якою його звіряють. Шард, що виконав запит, береться
        з плану explain, — це й перевірка, що запит не розіслано на всі шарди.
        """
        from pymongo.errors import PyMongoError

        pipeline = self.niche_pipeline(dimension, scope, zone)
        try:
            (documents, elapsed), plan = await asyncio.gather(
                self._timed(self._niche_aggregate(pipeline, snapshot)), self._niche_plan(pipeline))
        except PyMongoError as error:
            raise ConnectionError(
                f"{self._collection}: зона {zone.index + 1} ({zone.label}): {self._brief(error)}") from error
        groups, total = self._niche_groups(documents)
        shards = self._plan_shards(plan)
        shard = ", ".join(shards) if shards else None
        log.debug("%s.niche_partial зона %d (%s) → %s: %d док., %d груп — %d мс", self._collection,
                  zone.index + 1, zone.label, shard or "?", total, len(groups), elapsed)
        return ZonePartial(zone=zone, groups=groups, documents=total, shard=shard, elapsed_ms=elapsed)

    async def niche_merged(
        self,
        dimension: CatalogDimension,
        scope: CatalogScope,
        snapshot: ReadSnapshot | None = None,
    ) -> MergedAggregate:
        """Той самий конвеєр без фільтра зони: маршрутизатор розсилає його на всі
        шарди, а $group ділить на часткове групування на шардах і злиття."""
        from pymongo.errors import PyMongoError

        pipeline = self.niche_pipeline(dimension, scope)
        try:
            (documents, elapsed), plan = await asyncio.gather(
                self._timed(self._niche_aggregate(pipeline, snapshot)), self._niche_plan(pipeline))
        except PyMongoError as error:
            raise ConnectionError(f"{self._collection}: вбудована агрегація: {self._brief(error)}") from error
        groups, total = self._niche_groups(documents)
        split = plan.get("splitPipeline") or {}
        log.debug("%s.niche_merged: %d док., %d груп — %d мс; злиття: %s", self._collection, total, len(groups),
                  elapsed, plan.get("mergeType") or "—")
        return MergedAggregate(
            groups=groups, documents=total, elapsed_ms=elapsed, shards=self._plan_shards(plan),
            shards_part=self._stage_names(split.get("shardsPart")), merger_part=self._stage_names(split.get("mergerPart")),
            merge_type=plan.get("mergeType"),
        )

    def niche_pipeline(
        self, dimension: CatalogDimension, scope: CatalogScope, zone: ZoneBounds | None = None
    ) -> list[dict[str, Any]]:
        """Конвеєр етапу map — та сама обробка, що й еталон
        NicheAggregator.group_keys / contribution, мовою агрегації.

        1. $match — діапазон shard_key зони (маршрутизація) і частина каталогу.
        2. $project — лише показники пригоди й ключ групи: повні тексти далі
           конвеєром не йдуть.
        3. Для тегів — $unwind без повторів тегу; рядок першого тегу (або
           пригоди без тегів) позначається, тож кількість пригод зони рахується
           в тому самому проході.
        4. $group — часткові суми й кількості за ключем групи.
        """
        match: dict[str, Any] = {}
        if zone is not None:
            bounds = {op: value for op, value in (("$gte", zone.lower), ("$lt", zone.upper)) if value is not None}
            if bounds:
                match[self.SHARD_KEY_FIELD] = bounds
        if scope.origins is not None:
            match["provenance.origin"] = {"$in": list(scope.origins)}
        pipeline: list[dict[str, Any]] = [{"$match": match}] if match else []
        project = self._niche_metrics()
        if dimension is CatalogDimension.TAG:
            project["key"] = {"$setUnion": [{"$ifNull": ["$tags", []]}, []]}
            pipeline += [
                {"$project": project},
                {"$unwind": {"path": "$key", "preserveNullAndEmptyArrays": True, "includeArrayIndex": "position"}},
            ]
            first: Any = {"$cond": [{"$gt": ["$position", 0]}, 0, 1]}
        else:
            project["key"] = self._niche_key(dimension)
            pipeline.append({"$project": project})
            first = 1
        group: dict[str, Any] = {
            "_id": "$key",
            "adventures": {"$sum": 1},
            "documents": {"$sum": first},
            "collected": {"$sum": "$collected"},
            "rated": {"$sum": "$rated"},
            "rating_sum": {"$sum": "$rating"},
            **{f"bin{i}": {"$sum": {"$cond": [{"$eq": ["$bin", i]}, 1, 0]}} for i in range(RATING_BINS)},
            "ratings_total": {"$sum": "$ratings"},
            "reviews_total": {"$sum": "$reviews"},
            "free": {"$sum": "$free"},
            "priced": {"$sum": "$priced"},
            "price_sum": {"$sum": "$price"},
            "timed": {"$sum": "$timed"},
            "duration_sum": {"$sum": "$duration"},
            "year_min": {"$min": "$year"},
            "year_max": {"$max": "$year"},
        }
        pipeline.append({"$group": group})
        return pipeline

    @classmethod
    def _niche_metrics(cls) -> dict[str, Any]:
        """Показники однієї пригоди — відповідник NicheAggregator.contribution."""
        rated = {"$isNumber": "$rating_avg"}
        priced = {"$and": [{"$isNumber": "$price.amount"}, {"$gt": ["$price.amount", 0]}]}
        timed = {"$isNumber": "$duration_hours"}
        factor = RATING_BINS / 5
        return {
            "_id": 0,
            "collected": {"$cond": [{"$in": ["$provenance.origin", list(cls.COLLECTED_ORIGINS)]}, 1, 0]},
            "rated": {"$cond": [rated, 1, 0]},
            "rating": {"$cond": [rated, "$rating_avg", 0]},
            "bin": {"$cond": [rated, {"$min": [RATING_BINS - 1, {"$max": [0, {"$floor": {
                "$multiply": ["$rating_avg", factor]}}]}]}, -1]},
            "ratings": {"$cond": [{"$isNumber": "$ratings_count"}, "$ratings_count", 0]},
            "reviews": {"$cond": [{"$isArray": "$reviews"}, {"$size": "$reviews"}, 0]},
            "free": {"$cond": [{"$isNumber": "$price.amount"}, {"$cond": [{"$eq": ["$price.amount", 0]}, 1, 0]}, 1]},
            "priced": {"$cond": [priced, 1, 0]},
            "price": {"$cond": [priced, "$price.amount", 0]},
            "timed": {"$cond": [timed, 1, 0]},
            "duration": {"$cond": [timed, "$duration_hours", 0]},
            "year": "$published_year",
        }

    @staticmethod
    def _niche_key(dimension: CatalogDimension) -> Any:
        """Ключ групи для вимірів з одним значенням на пригоду; рівень гри —
        за мінімальним рівнем персонажів (play_tier)."""
        if dimension is CatalogDimension.SYSTEM:
            return "$system"
        if dimension is CatalogDimension.SETTING:
            return "$setting_id"
        if dimension is CatalogDimension.YEAR:
            return "$published_year"
        level = "$levels.minimum"
        branches = [{"case": {"$lte": [level, highest]}, "then": tier}
                    for tier, (_, highest) in enumerate(PLAY_TIERS, start=1)]
        return {"$cond": [{"$isNumber": level}, {"$switch": {"branches": branches, "default": len(PLAY_TIERS)}}, None]}

    async def _niche_aggregate(self, pipeline: list[dict[str, Any]], snapshot: ReadSnapshot | None) -> list[dict]:
        """Виконання конвеєра; зі знімком — на моменті atClusterTime, спільному
        для всіх зон запиту. Підказка $natural — перегляд колекції в природному
        порядку: зона займає шард цілком, тож обхід індексу shard_key з читанням
        кожного документа за ним лише повільніший. Якщо сховище підказку
        відхилить, запит повторюється без неї."""
        from bson.timestamp import Timestamp
        from pymongo.errors import OperationFailure

        command: dict[str, Any] = {
            "aggregate": self._collection, "pipeline": pipeline,
            "cursor": {"batchSize": self.NICHE_BATCH_SIZE}, "maxTimeMS": self.NICHE_MAX_TIME_MS,
        }
        if snapshot is not None:
            seconds, increment = (int(part) for part in snapshot.token.split("."))
            command["readConcern"] = {"level": "snapshot", "atClusterTime": Timestamp(seconds, increment)}
        if self._natural_hint:
            try:
                cursor = await self._documents.database.cursor_command({**command, "hint": {"$natural": 1}})
                return await cursor.to_list()
            except OperationFailure as error:
                if "hint" not in str(error).lower():
                    raise
                self._natural_hint = False
                log.warning("%s: підказку $natural відхилено (%s) — запити аналітики без неї",
                            self._collection, self._brief(error))
        cursor = await self._documents.database.cursor_command(command)
        return await cursor.to_list()

    async def _niche_plan(self, pipeline: list[dict[str, Any]]) -> dict:
        """План запиту без виконання (queryPlanner): на які шарди його надіслано
        і як маршрутизатор поділив конвеєр. Діагностика не зупиняє запит."""
        from pymongo.errors import PyMongoError

        request: dict[str, Any] = {"aggregate": self._collection, "pipeline": pipeline, "cursor": {}}
        if self._natural_hint:
            request["hint"] = {"$natural": 1}
        try:
            return await self._documents.database.command({"explain": request, "verbosity": "queryPlanner"})
        except PyMongoError as error:
            log.debug("%s: explain недоступний: %s", self._collection, self._brief(error))
            return {}

    @staticmethod
    async def _timed(operation: Awaitable[Any]) -> tuple[Any, int]:
        started = time.perf_counter()
        result = await operation
        return result, round((time.perf_counter() - started) * 1000)

    @staticmethod
    def _niche_groups(documents: Sequence[dict]) -> tuple[dict[str, NicheStats], int]:
        """Відповідь $group → агрегати за текстовим ключем групи і кількість
        пригод, що потрапили в запит."""
        groups: dict[str, NicheStats] = {}
        total = 0
        for document in documents:
            key = document.get("_id")
            if isinstance(key, float) and key.is_integer():
                key = int(key)
            text = "" if key is None else str(key)
            stats = NicheStats(
                adventures=int(document["adventures"]),
                collected=int(document["collected"]),
                rated=int(document["rated"]),
                rating_sum=float(document["rating_sum"]),
                rating_bins=tuple(int(document[f"bin{i}"]) for i in range(RATING_BINS)),
                ratings_total=int(document["ratings_total"]),
                reviews_total=int(document["reviews_total"]),
                free=int(document["free"]),
                priced=int(document["priced"]),
                price_sum=float(document["price_sum"]),
                timed=int(document["timed"]),
                duration_sum=float(document["duration_sum"]),
                year_min=None if document.get("year_min") is None else int(document["year_min"]),
                year_max=None if document.get("year_max") is None else int(document["year_max"]),
            )
            groups[text] = stats if text not in groups else groups[text].combine(stats)
            total += int(document["documents"])
        return groups, total

    @staticmethod
    def _plan_shards(plan: dict) -> tuple[str, ...]:
        """Шарди з плану explain агрегації; на одному вузлі — порожньо."""
        shards = plan.get("shards")
        if isinstance(shards, dict):
            return tuple(sorted(shards))
        winning = (plan.get("queryPlanner") or {}).get("winningPlan") or {}
        return tuple(sorted(s["shardName"] for s in winning.get("shards") or [] if s.get("shardName")))

    @staticmethod
    def _stage_names(stages: Sequence[dict] | None) -> tuple[str, ...]:
        return tuple(next(iter(stage)) for stage in stages or () if isinstance(stage, dict) and stage)

    @staticmethod
    def _brief(error: Exception) -> str:
        """Стисле повідомлення про збій: код і перший рядок причини."""
        code = getattr(error, "code", None)
        details = getattr(error, "details", None) or {}
        message = str(details.get("errmsg") or error).splitlines()[0][:200]
        return f"{message} (код {code})" if code is not None else message


class MongoMonsterRepository(MongoCrudRepository[Monster]):
    COLLECTION = "monsters"
    #: Найпопулярніші монстри (група 7)
    INDEXES = (([("play_count", -1)], {}),)

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

    async def most_played(self, limit: int = 20) -> list[Monster]:
        cursor = self._documents.find({}).sort([("play_count", -1), ("_id", 1)]).limit(limit)
        return [self._mapper.to_entity(document) for document in await cursor.to_list()]


class MongoCharacterRepository(MongoCrudRepository[PlayerCharacter]):
    COLLECTION = "characters"
    #: Типові закляття класу й рівня (група 7)
    INDEXES = (([("char_class", 1), ("level", 1)], {}),)

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
