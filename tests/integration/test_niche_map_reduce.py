"""Аналітика ніш на справжньому сховищі (п. 4.12).

* Етап map кожної зони — конвеєр агрегації на шарді — дає ті самі агрегати,
  що й доменний еталон NicheAggregator.map над документами зони.
* У шардованому кластері запит зони надсилається рівно на один шард — той,
  за яким закріплено її діапазон shard_key.
* На спільному знімку (snapshot) запис, що з'явився після нього, не видно в
  жодній зоні, а явний map-reduce збігається з вбудованою агрегацією.

Тест працює в окремій базі dnd_test_niches_<суфікс>; у кластері її колекцію
пригод розподілено між трьома шардами так само, як у колекції системи.

    MONGO_URI=mongodb://localhost:27017 python -m pytest tests/integration/test_niche_map_reduce.py -v
"""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest

from app.application.dto import NicheQueryDTO
from app.application.queries import GetNicheAnalyticsHandler
from app.domain.entities import Adventure
from app.domain.services import NicheAggregator, ShardKeyService
from app.domain.values import CatalogDimension, CatalogScope
from app.infrastructure.config import Container, Settings
from app.infrastructure.persistence import DataclassDocumentMapper, MongoAdventureRepository
from tests.unit.fakes import FakeCacheStore
from tests.unit.test_niche_analytics import _catalog
from tools.shard_bench.cluster import ZoneLayout

pytestmark = [pytest.mark.integration,
              pytest.mark.skipif("MONGO_URI" not in os.environ, reason="потребує MONGO_URI")]

CATALOG_SIZE = 1500


@pytest.fixture
async def stand():
    container = Container(Settings.from_env())
    await container.connect_documents()
    client = container.documents_database().client
    database = f"dnd_test_niches_{uuid.uuid4().hex[:8]}"
    layout = ZoneLayout(client, database)
    shards = await layout.shards()
    ranges = await layout.prepare("adventures", shards[:3]) if len(shards) >= 3 else None
    repository = MongoAdventureRepository(client, "adventures", DataclassDocumentMapper(Adventure, ("adventure_id",)),
                                          database=database, shard_key_of=ShardKeyService().key_value)
    catalog = _catalog(CATALOG_SIZE, seed=24)
    assert await repository.add_many(catalog) == CATALOG_SIZE
    try:
        yield SimpleNamespace(client=client, database=database, ranges=ranges, repository=repository, catalog=catalog)
    finally:
        if ranges:
            await layout.drop("adventures")
            await layout.release(shards[:3])
        await client.drop_database(database)
        await container.shutdown()


def _inside(adventure: Adventure, zone) -> bool:
    return (zone.lower is None or adventure.shard_key >= zone.lower) and (zone.upper is None or adventure.shard_key < zone.upper)


@pytest.mark.parametrize("dimension", list(CatalogDimension))
async def test_map_of_each_zone_matches_the_domain_reference(stand, dimension: CatalogDimension) -> None:
    aggregator = NicheAggregator()
    for scope in (CatalogScope.ALL, CatalogScope.COLLECTED):
        for zone in ShardKeyService(3).zones():
            partial = await stand.repository.niche_partial(zone, dimension, scope)
            expected, documents = aggregator.map((a for a in stand.catalog if _inside(a, zone)), dimension, scope)
            assert partial.documents == documents, (dimension, scope, zone.label)
            assert aggregator.equal(partial.groups, expected), (dimension, scope, zone.label)
            if stand.ranges:
                assert partial.shard == stand.ranges[zone.index]["shard"], (zone.label, partial.shard)


async def test_snapshot_hides_later_writes_in_every_zone(stand) -> None:
    if not stand.ranges:
        pytest.skip("читання snapshot перевіряється в шардованому кластері")
    zones = ShardKeyService(3).zones()
    snapshot = await stand.repository.read_snapshot(zones[0])
    assert snapshot is not None and snapshot.at is not None
    later = _catalog(300, seed=25)
    assert await stand.repository.add_many(later) == 300
    aggregator = NicheAggregator()
    partials = [await stand.repository.niche_partial(zone, CatalogDimension.TAG, CatalogScope.ALL, snapshot)
                for zone in zones]
    assert sum(p.documents for p in partials) == CATALOG_SIZE
    merged = await stand.repository.niche_merged(CatalogDimension.TAG, CatalogScope.ALL, snapshot)
    assert merged.documents == CATALOG_SIZE
    assert aggregator.equal(aggregator.reduce(p.groups for p in partials), merged.groups)
    assert set(merged.shards) == {r["shard"] for r in stand.ranges}
    now = await stand.repository.niche_merged(CatalogDimension.TAG, CatalogScope.ALL)
    assert now.documents == CATALOG_SIZE + 300


async def test_report_reduces_zones_and_matches_the_builtin_aggregation(stand) -> None:
    handler = GetNicheAnalyticsHandler(stand.repository, ShardKeyService(3), NicheAggregator(), FakeCacheStore())
    report = await handler.handle(NicheQueryDTO(dimension="system", limit=100))
    expected, documents = NicheAggregator().map(stand.catalog, CatalogDimension.SYSTEM, CatalogScope.ALL)
    assert report.complete and report.documents == documents == CATALOG_SIZE
    assert {row.key: row.adventures for row in report.rows} == {k: s.adventures for k, s in expected.items()}
    assert report.merged.matches is True
    if stand.ranges:
        assert report.snapshot is not None
        assert [z.shard for z in report.zones] == [r["shard"] for r in stand.ranges]
