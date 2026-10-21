"""Інтеграційні тести шляху запису.

Виконуються зі справжніми сховищами: перевіряють розподіл між
вузлами, пакетний запис і синхронність трьох сховищ, а також
негативний сценарій — відхилення невалідного документа.

    MONGO_URI=mongodb://localhost:27017 CACHE_URL=redis://localhost:6379/15 \\
    SEARCH_URL=http://localhost:9200 GRAPH_URI=bolt://localhost:7687 python -m pytest tests/integration
"""

from __future__ import annotations

import dataclasses
import json
import os
import random
import uuid

import pytest

from app.domain.entities import Adventure
from app.domain.services import ShardKeyService
from app.domain.values import DocumentKind
from app.infrastructure.config import Container, Settings
from app.infrastructure.persistence import MongoAdventureRepository
from workers.storage_check import StorageCheck

pytestmark = pytest.mark.integration

ALL_STORES = ("MONGO_URI", "CACHE_URL", "SEARCH_URL", "GRAPH_URI")


def _needs(*names: str):
    missing = [n for n in names if n not in os.environ]
    return pytest.mark.skipif(bool(missing), reason=f"потребує {', '.join(missing)}")


#: Обсяг вибірки й допуск: за 12 000 документів стандартне відхилення частки
#: зони — близько 1,3% від рівної, тож межа 5% лежить далеко за шумом
DISTRIBUTION_SAMPLE = 12_000
DISTRIBUTION_TOLERANCE = 0.05


def _adventures_with_seeded_ids(keys: ShardKeyService, count: int, marker: str) -> list[Adventure]:
    """Пригоди з відтворюваними UUID: тест дає той самий розподіл за кожного запуску."""
    rng = random.Random(20261010)
    template = StorageCheck.sample(keys)
    batch = []
    for _ in range(count):
        adventure_id = str(uuid.UUID(int=rng.getrandbits(128), version=4))
        batch.append(dataclasses.replace(
            template, adventure_id=adventure_id, title=marker, slug=f"{marker}-{adventure_id[:8]}",
            shard_key=keys.key_value(adventure_id)))
    return batch


@_needs("MONGO_URI")
async def test_shard_distribution_is_even() -> None:
    """Розподіл між зонами відхиляється від рівного не більш ніж на 5%.

    Точні кількості — countDocuments за діапазоном shard_key кожної зони
    (count_in_zones, п. 4.9). У шардованому кластері приріст документів на
    кожному шарді за $collStats збігається з приростом його зони: документ
    лягає на шард своєї зони, а не туди, куди його згодом перенесе балансувальник.
    """
    container = Container(Settings.from_env())
    await container.connect_documents()
    repository = container.repository(DocumentKind.ADVENTURE)
    assert isinstance(repository, MongoAdventureRepository)
    keys = container.shard_keys()
    bounds = keys.zone_bounds()
    marker = f"distribution-{uuid.uuid4().hex[:8]}"
    batch = _adventures_with_seeded_ids(keys, DISTRIBUTION_SAMPLE, marker)
    zones_before = await repository.count_in_zones(bounds)
    shards_before = await repository.count_by_shard()
    try:
        for start in range(0, len(batch), 500):
            assert await repository.add_many(batch[start : start + 500]) == len(batch[start : start + 500])
        zones = [after - before for after, before in zip(await repository.count_in_zones(bounds), zones_before)]
        assert zones == list(keys.distribution(a.adventure_id for a in batch).values())
        even = DISTRIBUTION_SAMPLE / keys.shard_count
        assert all(abs(n / even - 1) <= DISTRIBUTION_TOLERANCE for n in zones), zones
        shards_after = await repository.count_by_shard()
        if len(shards_after) > 1:  # шардований кластер: шарди shard1–shard3 за зонами zone1–zone3
            assert [shards_after[name] - shards_before.get(name, 0) for name in sorted(shards_after)] == zones
    finally:
        await container.documents_database().adventures.delete_many({"title": marker})
        await container.shutdown()


@_needs("MONGO_URI", "CACHE_URL")
async def test_invalid_document_is_rejected(tmp_path) -> None:
    """Негативний сценарій: документ без обов'язкових полів не зберігається,
    а потрапляє в журнал браку й лічильник відхилених."""
    settings = Settings.from_env({**os.environ, "MONGO_DATABASE": "dnd_test_write", "QUALITY_LOG_DIR": str(tmp_path)})
    container = Container(settings)
    adapters = await container.build_adapters()
    try:
        before = (await adapters.metrics.snapshot())["counters"].get("ingest.rejected.monster", 0)
        report = await container.ingest_handler(adapters).handle(
            [{"kind": "monster", "origin": "csv", "slug": "nameless", "type": "beast"}], "test")
        assert report.saved == 0 and report.rejected == 1
        assert await adapters.monsters.get(DocumentKind.MONSTER.natural_id("nameless")) is None
        line = json.loads((tmp_path / "quality" / "rejected.jsonl").read_text(encoding="utf-8").splitlines()[-1])
        assert line["reasons"] == ["name: обов'язкове поле", "challenge_rating: обов'язкове поле"]
        after = (await adapters.metrics.snapshot())["counters"]["ingest.rejected.monster"]
        assert after == before + 1
    finally:
        await container.documents_database().client.drop_database(settings.mongo_database)
        await container.shutdown()


@_needs(*ALL_STORES)
async def test_batch_write_reaches_all_stores(tmp_path) -> None:
    """Документ після запису доступний у сховищі, індексі та графі."""
    suffix = uuid.uuid4().hex[:8]
    settings = Settings.from_env({**os.environ, "MONGO_DATABASE": f"dnd_test_{suffix}", "QUALITY_LOG_DIR": str(tmp_path)})
    settings.index_names = {kind: f"test_{suffix}_{name}" for kind, name in settings.index_names.items()}
    container = Container(settings)
    adapters = await container.build_adapters()
    await container.ensure_storage(adapters)
    monster = {"kind": "monster", "origin": "csv", "slug": f"zombie-{suffix}", "name": "Zombie", "type": "undead",
               "challenge_rating": "0.25", "play_count": "843"}
    adventure = {"kind": "adventure", "origin": "generated", "adventure_id": str(uuid.uuid4()),
                 "title": "The Cursed Village of Mirefen", "synopsis": "Undead stalk the cursed village by night.",
                 "tags": json.dumps(["horror"]), "level_min": 2, "level_max": 4,
                 "monster_ids": json.dumps([DocumentKind.MONSTER.natural_id(f"zombie-{suffix}")])}
    try:
        report = await container.ingest_handler(adapters).handle([monster, adventure], "test")
        assert report.saved == 2 and report.indexed == 2 and report.graph_edges >= 2
        assert (await adapters.adventures.get(adventure["adventure_id"])).title == adventure["title"]
        index = adapters.indexes[DocumentKind.ADVENTURE]
        await container.search_client().indices.refresh(index=settings.index_names[DocumentKind.ADVENTURE])
        from app.domain.values import AdventureFilters, Page

        hits = await index.search("cursed villages", AdventureFilters(level=3), Page())
        assert hits.ids == (adventure["adventure_id"],) and hits.tag_counts == {"horror": 1}
        assert any("<mark>" not in s.text and s.highlights for s in hits.snippets[adventure["adventure_id"]])
        appears = await adapters.graph.adventures_with_monster(DocumentKind.MONSTER.natural_id(f"zombie-{suffix}"))
        assert appears == [(adventure["adventure_id"], adventure["title"])]
    finally:
        client = container.search_client()
        for name in settings.index_names.values():
            await client.options(ignore_status=404).indices.delete(index=name)
        await container.graph_driver().execute_query(
            "MATCH (n) WHERE n.adventure_id = $a OR n.monster_id = $m DETACH DELETE n",
            a=adventure["adventure_id"], m=DocumentKind.MONSTER.natural_id(f"zombie-{suffix}"))
        await container.documents_database().client.drop_database(settings.mongo_database)
        await container.shutdown()
