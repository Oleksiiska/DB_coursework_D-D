"""Аналітика ніш (група запитів 11, п. 4.12): розподілений запит у формі
map-reduce — без сховищ.

Перевіряється те, на чому тримається коректність зведення: агрегат ніші є
моноїдом, тож будь-який поділ каталогу на зони й будь-який порядок відповідей
дає той самий результат, а верхні ніші вибираються лише після зведення.
"""

from __future__ import annotations

import asyncio
import json
import random
import uuid
from datetime import datetime, timezone

import pytest

from app.application.dto import NicheQueryDTO, NicheRunDTO, dto_from_dict, dto_to_dict
from app.application.queries import GetNicheAnalyticsHandler
from app.domain.entities import Adventure, Review
from app.domain.errors import ValidationError
from app.domain.services import NicheAggregator, ShardKeyService
from app.domain.services.niche_aggregator import UNSPECIFIED
from app.domain.values import (
    PLAY_TIERS,
    CatalogDimension,
    CatalogScope,
    LevelRange,
    Money,
    NicheOrder,
    NicheStats,
    Provenance,
    SourceOrigin,
    ZoneBounds,
    play_tier,
)
from app.infrastructure.persistence import MongoAdventureRepository
from tests.unit.fakes import FakeCacheStore, InMemoryAdventureRepository

TAGS = ("horror", "one-shot", "dungeon", "investigation", "urban", "wilderness", "undead", "mystery")
SYSTEMS = ("Dungeons & Dragons", "Pathfinder", "Shadowdark RPG", None)


def _catalog(count: int = 600, seed: int = 7) -> list[Adventure]:
    """Каталог з усіма випадками полів: без тегів, без ціни, безкоштовні й
    платні, з оцінкою й без, з тривалістю й без, різні походження."""
    rng = random.Random(seed)
    keys = ShardKeyService()
    adventures = []
    for _ in range(count):
        adventure_id = str(uuid.UUID(int=rng.getrandbits(128), version=4))
        origin = rng.choice((SourceOrigin.GENERATED, SourceOrigin.GENERATED, SourceOrigin.CRAWLER, SourceOrigin.CSV))
        level = rng.choice((None, rng.randint(1, 20)))
        rating = rng.choice((None, round(rng.uniform(1, 5), 2), 5.0, 0.0))
        price = rng.choice((None, 0.0, round(rng.uniform(1, 20), 2)))
        adventures.append(Adventure(
            adventure_id=adventure_id,
            title=f"Adventure {adventure_id[:6]}",
            synopsis="Undead stalk the cursed village.",
            provenance=Provenance(origin),
            tags=rng.sample(TAGS, rng.randint(0, 4)),
            system=rng.choice(SYSTEMS),
            setting_id=rng.choice((None, "forgotten-realms", "eberron")),
            levels=LevelRange(level, None if level is None else min(20, level + 3)),
            published_year=rng.choice((None, 2019, 2023, 2026)),
            price=Money(price),
            duration_hours=rng.choice((None, 2.5, 4.0, 12.0)),
            rating_avg=rating,
            ratings_count=None if rating is None else rng.randint(1, 90),
            reviews=[Review(str(i), "gm", "Ran it twice.") for i in range(rng.randint(0, 3))],
            shard_key=keys.key_value(adventure_id),
        ))
    return adventures


def _stats(**values) -> NicheStats:
    return NicheStats(**values)


# --- агрегат ніші — моноїд ---


def test_niche_stats_is_a_monoid() -> None:
    aggregator = NicheAggregator()
    a, b, c = (aggregator.contribution(x) for x in _catalog(3, seed=1))
    identity = NicheStats()
    assert identity.combine(a) == a and a.combine(identity) == a
    assert aggregator.equal({"k": a.combine(b).combine(c)}, {"k": a.combine(b.combine(c))})
    assert aggregator.equal({"k": a.combine(b)}, {"k": b.combine(a)})


def test_unknown_year_does_not_take_part_in_min_and_max() -> None:
    merged = _stats(adventures=1, year_min=None, year_max=None).combine(_stats(adventures=1, year_min=2019, year_max=2019))
    assert (merged.year_min, merged.year_max) == (2019, 2019)
    merged = merged.combine(_stats(adventures=1, year_min=2023, year_max=2023))
    assert (merged.year_min, merged.year_max) == (2019, 2023)


def test_algebraic_measures_come_from_sums_and_counts() -> None:
    stats = _stats(adventures=4, rated=2, rating_sum=9.0, free=1, priced=3, price_sum=15.0, timed=2, duration_sum=7.0,
                   collected=1)
    assert (stats.avg_rating, stats.avg_price, stats.avg_duration) == (4.5, 5.0, 3.5)
    assert (stats.free_share, stats.collected_share) == (0.25, 0.25)
    assert NicheStats().avg_rating is None and NicheStats().free_share is None


def test_median_rating_is_estimated_from_the_histogram() -> None:
    """Медіана холістична: її оцінено за гістограмою з похибкою до ширини
    кошика (0,5 бала)."""
    aggregator = NicheAggregator()
    ratings = [1.2, 3.9, 4.1, 4.4, 4.6, 4.8, 5.0]
    stats = NicheStats()
    for rating in ratings:
        stats = stats.combine(aggregator.contribution(Adventure(
            adventure_id=str(uuid.uuid4()), title="x", synopsis="x", provenance=Provenance(SourceOrigin.CSV),
            rating_avg=rating)))
    assert stats.rated == 7 and sum(stats.rating_bins) == 7
    assert stats.rating_bins[9] == 3  # 4,6, 4,8 і 5,0 — останній кошик [4,5; 5]
    assert abs(stats.median_rating() - 4.4) <= 0.5
    assert NicheStats().median_rating() is None


# --- еталон етапу map ---


def test_group_keys_of_each_dimension() -> None:
    aggregator = NicheAggregator()
    adventure = Adventure(adventure_id="a", title="t", synopsis="s", provenance=Provenance(SourceOrigin.GENERATED),
                          tags=["urban", "horror", "urban"], system="Pathfinder", setting_id="eberron",
                          levels=LevelRange(5, 8), published_year=2023)
    assert aggregator.group_keys(adventure, CatalogDimension.TAG) == ["horror", "urban"]
    assert aggregator.group_keys(adventure, CatalogDimension.SYSTEM) == ["Pathfinder"]
    assert aggregator.group_keys(adventure, CatalogDimension.SETTING) == ["eberron"]
    assert aggregator.group_keys(adventure, CatalogDimension.TIER) == ["2"]
    assert aggregator.group_keys(adventure, CatalogDimension.YEAR) == ["2023"]
    bare = Adventure(adventure_id="b", title="t", synopsis="s", provenance=Provenance(SourceOrigin.CSV))
    for dimension in CatalogDimension:
        assert aggregator.group_keys(bare, dimension) == [UNSPECIFIED]


def test_play_tiers_follow_the_rules() -> None:
    assert PLAY_TIERS == ((1, 4), (5, 10), (11, 16), (17, 20))
    assert [play_tier(level) for level in (1, 4, 5, 10, 11, 16, 17, 20, None)] == [1, 1, 2, 2, 3, 3, 4, 4, None]


def test_contribution_of_one_adventure() -> None:
    aggregator = NicheAggregator()
    paid = Adventure(adventure_id="a", title="t", synopsis=None, provenance=Provenance(SourceOrigin.CRAWLER),
                     price=Money(4.5), rating_avg=4.62, ratings_count=25, duration_hours=3.0, published_year=2021,
                     reviews=[Review("1", "gm", "great")])
    stats = aggregator.contribution(paid)
    assert (stats.adventures, stats.collected, stats.free, stats.priced, stats.price_sum) == (1, 1, 0, 1, 4.5)
    assert (stats.rated, stats.rating_sum, stats.rating_bins[9], stats.ratings_total) == (1, 4.62, 1, 25)
    assert (stats.timed, stats.duration_sum, stats.reviews_total, stats.year_min) == (1, 3.0, 1, 2021)
    free = Adventure(adventure_id="b", title="t", synopsis="s", provenance=Provenance(SourceOrigin.GENERATED),
                     price=Money(0.0))
    stats = aggregator.contribution(free)
    assert (stats.collected, stats.free, stats.priced, stats.rated, stats.timed) == (0, 1, 0, 0, 0)


def test_scope_selects_part_of_the_catalog() -> None:
    aggregator = NicheAggregator()
    catalog = _catalog()
    _, everything = aggregator.map(catalog, CatalogDimension.SYSTEM, CatalogScope.ALL)
    _, collected = aggregator.map(catalog, CatalogDimension.SYSTEM, CatalogScope.COLLECTED)
    _, generated = aggregator.map(catalog, CatalogDimension.SYSTEM, CatalogScope.GENERATED)
    assert everything == len(catalog) == collected + generated
    assert CatalogScope.COLLECTED.origins == ("csv", "crawler") and CatalogScope.ALL.origins is None


# --- reduce ---


@pytest.mark.parametrize("dimension", list(CatalogDimension))
def test_reduce_of_zone_partials_equals_the_whole_catalog(dimension: CatalogDimension) -> None:
    """Головна властивість map-reduce: зведення часткових агрегатів зон у
    будь-якому порядку дорівнює агрегату всього каталогу."""
    aggregator = NicheAggregator()
    catalog = _catalog()
    keys = ShardKeyService(3)
    partials, documents = [], 0
    for zone in keys.zones():
        inside = [a for a in catalog if (zone.lower is None or a.shard_key >= zone.lower)
                  and (zone.upper is None or a.shard_key < zone.upper)]
        groups, count = aggregator.map(inside, dimension, CatalogScope.ALL)
        partials.append(groups)
        documents += count
    whole, total = aggregator.map(catalog, dimension, CatalogScope.ALL)
    assert documents == total == len(catalog)
    for order in ([0, 1, 2], [2, 0, 1], [1, 2, 0]):
        assert aggregator.equal(aggregator.reduce(partials[i] for i in order), whole)


def test_top_niches_are_chosen_only_after_reduce() -> None:
    """Ніша, яка в жодній зоні не перша, у підсумку може бути першою: тому
    зони повертають усі групи, а не свої верхні N."""
    aggregator = NicheAggregator()
    zones = [{"horror": _stats(adventures=10), "urban": _stats(adventures=6)},
             {"heist": _stats(adventures=10), "urban": _stats(adventures=6)},
             {"undead": _stats(adventures=10), "urban": _stats(adventures=6)}]
    assert [aggregator.rank(z, NicheOrder.COUNT, 1)[0][0] for z in zones] == ["horror", "heist", "undead"]
    assert aggregator.rank(aggregator.reduce(zones), NicheOrder.COUNT, 1)[0] == ("urban", _stats(adventures=18))


def test_rating_order_puts_reliable_niches_first() -> None:
    aggregator = NicheAggregator()
    groups = {
        "lucky": _stats(adventures=2, rated=2, rating_sum=10.0),
        "solid": _stats(adventures=40, rated=20, rating_sum=90.0),
        "fine": _stats(adventures=30, rated=10, rating_sum=40.0),
    }
    assert [name for name, _ in aggregator.rank(groups, NicheOrder.RATING, 3)] == ["solid", "fine", "lucky"]
    assert [name for name, _ in aggregator.rank(groups, NicheOrder.COUNT, 2)] == ["solid", "fine"]


# --- сценарій ---


def _handler(repository: InMemoryAdventureRepository, cache: FakeCacheStore | None = None) -> GetNicheAnalyticsHandler:
    return GetNicheAnalyticsHandler(repository, ShardKeyService(3), NicheAggregator(), cache or FakeCacheStore())


async def test_every_zone_is_mapped_once_at_one_snapshot_and_reduced() -> None:
    catalog = _catalog()
    repository = InMemoryAdventureRepository(catalog)
    cache = FakeCacheStore()
    report = await _handler(repository, cache).handle(NicheQueryDTO(dimension="tag", limit=50))
    partial_calls = [c for c in repository.niche_calls if c[0] == "partial"]
    assert sorted(c[1] for c in partial_calls) == [0, 1, 2]
    assert len({c[4] for c in partial_calls}) == 1 and partial_calls[0][4].token == "1732464000.1"
    whole, documents = NicheAggregator().map(catalog, CatalogDimension.TAG, CatalogScope.ALL)
    assert report.documents == documents == len(catalog)
    assert report.groups_total == len(whole) and report.complete and report.coverage == 1.0
    assert {row.key: row.adventures for row in report.rows} == {k: s.adventures for k, s in whole.items()}
    assert [z.shard for z in report.zones] == ["shard1", "shard2", "shard3"]
    assert all(sum(row.by_zone) == row.adventures for row in report.rows)
    assert report.merged.matches is True and report.merged.merger_part == ["$mergeCursors", "$group"]
    assert report.snapshot == "1732464000.1" and not report.cache_hit
    assert cache.ttl["analytics:niches:v1:tag:all"] == GetNicheAnalyticsHandler.CACHE_TTL_SECONDS == 300


async def test_cached_run_serves_any_order_and_limit() -> None:
    repository = InMemoryAdventureRepository(_catalog())
    handler = _handler(repository)
    first = await handler.handle(NicheQueryDTO(dimension="system", order="count", limit=10))
    calls = len(repository.niche_calls)
    second = await handler.handle(NicheQueryDTO(dimension="system", order="rating", limit=2))
    assert len(repository.niche_calls) == calls and second.cache_hit and not first.cache_hit
    assert len(second.rows) == 2 and second.order == "rating"
    await handler.handle(NicheQueryDTO(dimension="system", refresh=True))
    assert len(repository.niche_calls) > calls


async def test_unavailable_zone_gives_a_marked_partial_result_that_is_not_cached() -> None:
    catalog = _catalog()
    repository = InMemoryAdventureRepository(catalog, failing_zones={1})
    cache = FakeCacheStore()
    report = await _handler(repository, cache).handle(NicheQueryDTO(dimension="year"))
    assert not report.complete and report.coverage == pytest.approx(2 / 3)
    assert "код 133" in report.zones[1].error and report.zones[0].error is None
    assert report.merged.matches is None and report.merged.error
    keys = ShardKeyService(3)
    expected = sum(1 for a in catalog if keys.shard_for(a.adventure_id) != 1)
    assert report.documents == expected
    assert not cache.store


async def test_identical_concurrent_requests_share_one_computation() -> None:
    repository = InMemoryAdventureRepository(_catalog())
    handler = _handler(repository)
    await asyncio.gather(*(handler.handle(NicheQueryDTO(dimension="tier")) for _ in range(4)))
    assert sum(1 for c in repository.niche_calls if c[0] == "partial") == 3


async def test_without_snapshot_support_zones_are_read_independently() -> None:
    repository = InMemoryAdventureRepository(_catalog(), snapshot=None)
    report = await _handler(repository).handle(NicheQueryDTO())
    assert report.snapshot is None and report.complete
    assert all(c[4] is None for c in repository.niche_calls if c[0] == "partial")


async def test_invalid_query_is_rejected() -> None:
    handler = _handler(InMemoryAdventureRepository())
    for query in (NicheQueryDTO(dimension="planet"), NicheQueryDTO(scope="mine"), NicheQueryDTO(order="price"),
                  NicheQueryDTO(limit=0), NicheQueryDTO(limit=101)):
        with pytest.raises(ValidationError):
            await handler.handle(query)


def test_cached_run_restores_domain_aggregates() -> None:
    aggregator = NicheAggregator()
    groups, documents = aggregator.map(_catalog(50), CatalogDimension.TAG, CatalogScope.ALL)
    run = NicheRunDTO(dimension="tag", scope="all", generated_at=datetime.now(timezone.utc), documents=documents,
                      groups=groups, by_zone={k: [1, 2, 3] for k in groups})
    restored = dto_from_dict(NicheRunDTO, json.loads(json.dumps(dto_to_dict(run))))
    assert aggregator.equal(restored.groups, groups) and restored.by_zone == run.by_zone
    assert isinstance(next(iter(restored.groups.values())).rating_bins, tuple)


# --- адаптер документного сховища: чисті частини ---


def _repository() -> MongoAdventureRepository:
    class _Client(dict):
        def __missing__(self, name):
            return {"adventures": None}

    return MongoAdventureRepository(_Client(), "adventures", mapper=None, shard_key_of=ShardKeyService().key_value)


def test_map_pipeline_targets_one_zone_and_projects_only_measures() -> None:
    repository = _repository()
    zone = ZoneBounds(1, "0342", "0683")
    pipeline = repository.niche_pipeline(CatalogDimension.TAG, CatalogScope.COLLECTED, zone)
    assert pipeline[0] == {"$match": {"shard_key": {"$gte": "0342", "$lt": "0683"},
                                      "provenance.origin": {"$in": ["csv", "crawler"]}}}
    project = pipeline[1]["$project"]
    assert project["_id"] == 0 and "full_text" not in project and "synopsis" not in project
    assert pipeline[2]["$unwind"]["preserveNullAndEmptyArrays"] is True
    assert set(pipeline[3]["$group"]) >= {"_id", "adventures", "documents", "rating_sum", "bin0", "bin9", "year_max"}
    first = repository.niche_pipeline(CatalogDimension.SYSTEM, CatalogScope.ALL, ZoneBounds(0, None, "0342"))
    assert first[0] == {"$match": {"shard_key": {"$lt": "0342"}}} and first[1]["$project"]["key"] == "$system"
    whole = repository.niche_pipeline(CatalogDimension.YEAR, CatalogScope.ALL)
    assert list(whole[0]) == ["$project"]


def test_group_documents_become_niche_aggregates() -> None:
    row = {"adventures": 3, "documents": 2, "collected": 1, "rated": 2, "rating_sum": 9.0,
           **{f"bin{i}": 0 for i in range(10)}, "ratings_total": 30, "reviews_total": 4, "free": 1, "priced": 2,
           "price_sum": 10, "timed": 1, "duration_sum": 2.5, "year_min": 2019, "year_max": 2023}
    groups, total = MongoAdventureRepository._niche_groups([
        {**row, "_id": "horror"}, {**row, "_id": None}, {**row, "_id": ""}, {**row, "_id": 2019.0}])
    assert set(groups) == {"horror", "", "2019"} and total == 8
    assert groups[""].adventures == 6 and groups["horror"].price_sum == 10.0
    assert isinstance(groups["horror"].price_sum, float)


def test_explain_shows_target_shards_and_split_pipeline() -> None:
    plan = {"mergeType": "mongos", "shards": {"shard2": {}, "shard1": {}},
            "splitPipeline": {"shardsPart": [{"$project": {}}, {"$group": {}}], "mergerPart": [{"$mergeCursors": {}},
                                                                                             {"$group": {}}]}}
    assert MongoAdventureRepository._plan_shards(plan) == ("shard1", "shard2")
    assert MongoAdventureRepository._stage_names(plan["splitPipeline"]["mergerPart"]) == ("$mergeCursors", "$group")
    find_plan = {"queryPlanner": {"winningPlan": {"shards": [{"shardName": "shard3"}]}}}
    assert MongoAdventureRepository._plan_shards(find_plan) == ("shard3",)
    assert MongoAdventureRepository._plan_shards({}) == ()
