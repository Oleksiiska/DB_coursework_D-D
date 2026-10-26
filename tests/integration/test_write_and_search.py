"""Тести на запис і пошук у формулюванні методичних вказівок (розділ 5 звіту).

* Запис: об'єкт серіалізується, проходить через ключ розподілу й
  записується на шард своєї зони без втрати даних.
* Пошук, позитивний: документ з унікальним маркером записано шляхом
  команд — пошук за маркером через REST API знаходить саме його.
* Пошук, негативний: пошук за неіснуючим маркером повертає порожню
  видачу з кодом 200, а не помилку 500 чи випадкові документи.

    MONGO_URI=mongodb://localhost:27017 CACHE_URL=redis://localhost:6379/15 \\
    SEARCH_URL=http://localhost:9200 python -m pytest tests/integration/test_write_and_search.py -v
"""

from __future__ import annotations

import os
import uuid

import pytest

from app.domain.values import DocumentKind
from app.infrastructure.config import Container, Settings
from tests.unit.fakes import FakeGraphRepository
from workers.storage_check import StorageCheck

pytestmark = pytest.mark.integration


def _needs(*names: str):
    missing = [n for n in names if n not in os.environ]
    return pytest.mark.skipif(bool(missing), reason=f"потребує {', '.join(missing)}")


@_needs("MONGO_URI")
async def test_document_is_written_to_the_shard_of_its_zone_without_loss() -> None:
    """Пригода з вкладеними об'єктами (діапазони, ціна, відгук) читається
    назад без змін, а в шардованому кластері план читання за ключем
    (explain) показує один шард — той, за яким закріплено зону її shard_key."""
    container = Container(Settings.from_env())
    await container.connect_documents()
    repository = container.repository(DocumentKind.ADVENTURE)
    keys = container.shard_keys()
    adventure = StorageCheck.sample(keys)
    try:
        await repository.add(adventure)
        restored = await repository.get(adventure.adventure_id)
        assert restored == adventure  # серіалізація й відновлення без втрат
        assert restored.shard_key == keys.key_value(adventure.adventure_id)

        plan = await container.documents_database().command({
            "explain": {"find": "adventures", "filter": {"_id": adventure.adventure_id,
                                                         "shard_key": adventure.shard_key}},
            "verbosity": "executionStats",
        })
        shards = plan.get("executionStats", {}).get("executionStages", {}).get("shards")
        if shards:  # шардований кластер: зони zone1–zone3 закріплено за shard1–shard3
            expected = f"shard{keys.shard_for(adventure.adventure_id) + 1}"
            assert [s["shardName"] for s in shards] == [expected]
            assert shards[0]["nReturned"] == 1
    finally:
        await repository.delete(adventure.adventure_id)
        await container.shutdown()


@_needs("MONGO_URI", "CACHE_URL", "SEARCH_URL")
async def test_search_finds_unique_marker_and_returns_empty_result_for_unknown_marker(tmp_path) -> None:
    """Документ проходить шлях команд (перевірка, перетворення, документне
    сховище й пошуковий індекс), а пошук іде через REST API так само, як
    з інтерфейсу: нормалізація, кеш, індекс, пакетне читання з MongoDB."""
    import httpx
    from fastapi import FastAPI

    from app.presentation.rest import RouteRegistry

    suffix = uuid.uuid4().hex[:8]
    settings = Settings.from_env({**os.environ, "MONGO_DATABASE": f"dnd_test_{suffix}", "QUALITY_LOG_DIR": str(tmp_path)})
    settings.index_names = {kind: f"test_{suffix}_{name}" for kind, name in settings.index_names.items()}
    container = Container(settings)
    adapters = await container.build_adapters()
    adapters.graph = FakeGraphRepository()  # граф не бере участі в пошуку
    for repository in adapters.repositories.values():
        await repository.ensure_indexes()
    for index in adapters.indexes.values():
        await index.ensure_index()

    marker, unknown = f"zq{uuid.uuid4().hex[:10]}", f"zq{uuid.uuid4().hex[:10]}"
    adventure_id = str(uuid.uuid4())
    document = {"kind": "adventure", "origin": "generated", "adventure_id": adventure_id,
                "title": "The Sunken Archive", "level_min": 3, "level_max": 5,
                "synopsis": f"The heroes follow the {marker} sigil into a flooded library."}
    try:
        report = await container.ingest_handler(adapters).handle([document], "test")
        assert (report.saved, report.indexed, report.rejected) == (1, 1, 0)
        await container.search_client().indices.refresh(index=settings.index_names[DocumentKind.ADVENTURE])

        registry = RouteRegistry()
        registry.bind(container.build_use_cases(adapters))
        app = FastAPI()
        registry.register(app)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            found = await client.get("/api/search/adventures", params={"q": marker})
            assert found.status_code == 200
            body = found.json()
            assert body["total"] == 1 and [item["adventure_id"] for item in body["items"]] == [adventure_id]
            snippet = next(s for s in body["items"][0]["snippets"] if s["field_name"] == "synopsis")
            start, end = snippet["highlights"][0]
            assert snippet["text"][start:end] == marker

            empty = await client.get("/api/search/adventures", params={"q": unknown})
            assert empty.status_code == 200
            assert empty.json()["total"] == 0 and empty.json()["items"] == []
    finally:
        client = container.search_client()
        for name in settings.index_names.values():
            await client.options(ignore_status=404).indices.delete(index=name)
        await container.documents_database().client.drop_database(settings.mongo_database)
        await container.shutdown()
