"""Навантаження заміру: документи пригод і паралельний пакетний запис."""

from __future__ import annotations

import asyncio
import logging
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.domain.entities import Adventure
from app.domain.services import ShardKeyService, VocabularyNormalizer
from app.domain.values import DocumentKind
from app.infrastructure.persistence import DataclassDocumentMapper, MongoAdventureRepository

log = logging.getLogger("shard-bench")


@dataclass(slots=True)
class RunResult:
    """Один запуск: N шардів, W паралельних записів, повтор r."""

    shards: int
    writers: int
    repeat: int
    documents: int
    seconds: float
    batch_ms: list[float] = field(default_factory=list)
    #: Скільки документів запуску лежить на кожному шарді (точний підрахунок за зонами)
    per_shard: dict[str, int] = field(default_factory=dict)
    chunks: dict[str, int] = field(default_factory=dict)
    #: Процесорний час процесу заміру за запуск: чи не клієнт обмежував швидкість
    client_cpu_seconds: float = 0.0

    @property
    def throughput(self) -> float:
        return self.documents / self.seconds if self.seconds > 0 else 0.0


def mapper() -> DataclassDocumentMapper:
    return DataclassDocumentMapper(Adventure, ("adventure_id",))


def repository(client: Any, database: str, collection: str) -> MongoAdventureRepository:
    """Той самий адаптер, що пише пригоди в системі: заміна зі вставкою за
    ключем, невпорядкований bulkWrite, w: majority клієнта, shard_key у фільтрі."""
    return MongoAdventureRepository(client, collection, mapper(), database=database,
                                    shard_key_of=ShardKeyService().key_value)


async def pool_from_collection(client: Any, database: str, size: int) -> list[dict]:
    """Випадкова вибірка згенерованих пригод із колекції системи — документи
    саме того розміру й складу, які пише генератор."""
    cursor = await client[database]["adventures"].aggregate([
        {"$match": {"provenance.origin": "generated"}},
        {"$sample": {"size": size}},
    ])
    return await cursor.to_list()


def pool_from_generator(container: Any, size: int, aliases: dict[str, str]) -> list[dict]:
    """Документи від генератора системи після тієї самої підготовки, що й на
    шляху запису (IngestDocumentHandler без сховищ). Генератор калібрується
    за CSV з CSV_DIR, якщо вони є."""
    from tools.prepare.check import write_path_handler

    source = container.generator_source()
    handler = write_path_handler(VocabularyNormalizer(aliases))
    to_document = mapper().to_document
    documents: list[dict] = []
    attempts = 0
    while len(documents) < size and attempts < size * 3:
        attempts += 1
        entity, problems = handler._prepare(DocumentKind.ADVENTURE, source.build_document())  # noqa: SLF001
        if entity is not None and not problems:
            documents.append(to_document(entity))
    return documents


def plan_ids(count: int, seed: int) -> list[tuple[str, str]]:
    """Відтворювані ключі документів і їхні shard_key: кожен сценарій пише той
    самий набір ключів, тож розподіл за кошиками однаковий."""
    rng = random.Random(seed)
    keys = ShardKeyService()
    ids = [str(uuid.UUID(int=rng.getrandbits(128), version=4)) for _ in range(count)]
    return [(i, keys.key_value(i)) for i in ids]


def copy_of(template: dict, document_id: str, shard_key: str) -> dict:
    """Документ із пулу з новим ключем: решта полів — без змін."""
    return {**template, "_id": document_id, "adventure_id": document_id, "shard_key": shard_key,
            "slug": f"bench-{document_id[:8]}"}


async def write(
    target: MongoAdventureRepository,
    pool: list[dict],
    ids: list[tuple[str, str]],
    batch_size: int,
    writers: int,
) -> tuple[float, list[float], float]:
    """W паралельних записувачів беруть пакети з одного переліку, як екземпляри
    процесу запису беруть пакети з черги. Повертає тривалість, час кожного
    пакета й процесорний час процесу заміру."""
    batches = [ids[start:start + batch_size] for start in range(0, len(ids), batch_size)]
    pending = iter(enumerate(batches))
    latencies: list[float] = []

    async def writer() -> None:
        for number, batch in pending:
            documents = [copy_of(pool[(number * batch_size + offset) % len(pool)], document_id, shard_key)
                         for offset, (document_id, shard_key) in enumerate(batch)]
            started = time.perf_counter()
            written = await target._write(documents)  # noqa: SLF001 — той самий bulkWrite, що й add_many
            latencies.append((time.perf_counter() - started) * 1000)
            if written != len(documents):
                raise RuntimeError(f"пакет {number}: записано {written} з {len(documents)}")

    cpu = time.process_time()
    started = time.perf_counter()
    await asyncio.gather(*(writer() for _ in range(max(1, writers))))
    return time.perf_counter() - started, latencies, time.process_time() - cpu
