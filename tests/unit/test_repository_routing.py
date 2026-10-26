"""Точкові запити до шардованої колекції: shard_key у кожному фільтрі
за ідентифікатором (таблиця 4.7). Без сховища — на заглушці колекції."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest
from pymongo.errors import BulkWriteError

from app.domain.entities import Adventure
from app.domain.services import ShardKeyService
from app.infrastructure.persistence import DataclassDocumentMapper, MongoCrudRepository
from workers.storage_check import StorageCheck

KEYS = ShardKeyService()


class _Cursor:
    def __init__(self, documents: list[dict]) -> None:
        self._documents = documents

    async def to_list(self) -> list[dict]:
        return self._documents


class RecordingCollection:
    """Запам'ятовує фільтри й зберігає документи за _id."""

    def __init__(self) -> None:
        self.filters: list[dict] = []
        self.documents: dict[str, dict] = {}
        #: Результат пакетного запису з помилками — як від сховища
        self.bulk_errors: dict | None = None

    async def find_one(self, query: dict):
        self.filters.append(query)
        return self.documents.get(query["_id"])

    def find(self, query: dict):
        self.filters.append(query)
        ids = query["_id"]["$in"]
        return _Cursor([self.documents[i] for i in ids if i in self.documents])

    async def replace_one(self, query: dict, document: dict, upsert: bool = False):
        self.filters.append(query)
        matched = query["_id"] in self.documents
        if matched or upsert:
            self.documents[query["_id"]] = document
        return SimpleNamespace(matched_count=int(matched))

    async def bulk_write(self, operations, ordered: bool = True):
        if self.bulk_errors is not None:
            raise BulkWriteError(self.bulk_errors)
        for op in operations:
            self.filters.append(op._filter)
            self.documents[op._filter["_id"]] = op._doc
        return SimpleNamespace(matched_count=0, upserted_count=len(operations))

    async def delete_one(self, query: dict):
        self.filters.append(query)
        return SimpleNamespace(deleted_count=int(self.documents.pop(query["_id"], None) is not None))


def repository(sharded: bool) -> tuple[MongoCrudRepository[Adventure], RecordingCollection]:
    collection = RecordingCollection()
    client = {"dnd": {"adventures": collection}}
    mapper = DataclassDocumentMapper(Adventure, ("adventure_id",))
    shard_key_of = KEYS.key_value if sharded else None
    return MongoCrudRepository(client, "adventures", mapper, shard_key_of=shard_key_of), collection


async def test_every_query_by_id_carries_the_shard_key() -> None:
    repo, collection = repository(sharded=True)
    adventure = StorageCheck.sample(KEYS)
    await repo.add(adventure)
    assert await repo.get(adventure.adventure_id) == adventure
    assert await repo.update(replace(adventure, title="Нова назва"))
    assert await repo.delete(adventure.adventure_id)
    expected = {"_id": adventure.adventure_id, "shard_key": adventure.shard_key}
    assert collection.filters == [expected] * 4


async def test_batch_read_targets_only_owning_shards() -> None:
    repo, collection = repository(sharded=True)
    batch = [StorageCheck.sample(KEYS) for _ in range(3)]
    assert await repo.add_many(batch) == 3
    ids = [a.adventure_id for a in batch]
    assert [a.adventure_id for a in await repo.get_many(ids)] == ids
    query = collection.filters[-1]
    assert query["shard_key"] == {"$in": sorted({KEYS.key_value(i) for i in ids})}


async def test_batch_read_skips_ids_missing_from_the_store() -> None:
    """П. 4.4.2: ідентифікатор з індексу, якого ще немає в документному
    сховищі, у видачу не потрапляє, а порядок решти зберігається."""
    repo, _ = repository(sharded=True)
    first, last = StorageCheck.sample(KEYS), StorageCheck.sample(KEYS)
    await repo.add_many([first, last])
    pending = StorageCheck.sample(KEYS).adventure_id  # уже в індексі, ще не записаний
    found = await repo.get_many([last.adventure_id, pending, first.adventure_id])
    assert [a.adventure_id for a in found] == [last.adventure_id, first.adventure_id]


async def test_missing_shard_key_is_filled_on_write() -> None:
    repo, collection = repository(sharded=True)
    adventure = replace(StorageCheck.sample(KEYS), shard_key=None)
    await repo.add(adventure)
    assert collection.documents[adventure.adventure_id]["shard_key"] == KEYS.key_value(adventure.adventure_id)


async def test_inconsistent_shard_key_is_rejected() -> None:
    repo, _ = repository(sharded=True)
    adventure = StorageCheck.sample(KEYS)
    wrong = "0000" if adventure.shard_key != "0000" else "0001"
    with pytest.raises(ValueError):
        await repo.add(replace(adventure, shard_key=wrong))


async def test_unsharded_collection_queries_by_id_only() -> None:
    repo, collection = repository(sharded=False)
    adventure = StorageCheck.sample(KEYS)
    await repo.add(adventure)
    await repo.get(adventure.adventure_id)
    assert collection.filters == [{"_id": adventure.adventure_id}] * 2


def _bulk_errors(code: int) -> dict:
    return {"writeErrors": [{"index": 0, "code": code, "errmsg": "test"}], "writeConcernErrors": [],
            "nInserted": 0, "nUpserted": 2, "nMatched": 0, "nModified": 0, "nRemoved": 0, "upserted": []}


async def test_storage_failure_keeps_the_batch_unacknowledged() -> None:
    """П. 4.4: недоступний шард не ковтається — пакет надійде повторно."""
    repo, collection = repository(sharded=True)
    collection.bulk_errors = _bulk_errors(6)  # HostUnreachable
    with pytest.raises(ConnectionError) as raised:
        await repo.add_many([StorageCheck.sample(KEYS) for _ in range(3)])
    # стислий опис замість повного переліку операцій із документами
    assert str(raised.value) == "adventures: test (код 6, не записано 1 з 3)"
    assert isinstance(raised.value.__cause__, BulkWriteError)


async def test_document_error_does_not_stop_the_batch() -> None:
    """Повтор ключа під час паралельної вставки стосується одного документа."""
    repo, collection = repository(sharded=True)
    collection.bulk_errors = _bulk_errors(11000)  # DuplicateKey
    assert await repo.add_many([StorageCheck.sample(KEYS) for _ in range(3)]) == 2
