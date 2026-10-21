"""Шлях запису на заглушках: валідація, трансформації, три сховища,
лічильники таблиці 4.14 і журнали якості даних."""

from __future__ import annotations

import asyncio
import json

from app.application.commands import IngestDocumentHandler
from app.domain.services import DocumentValidator, EnvironmentClassifier, ShardKeyService, VocabularyNormalizer
from app.domain.values import DocumentKind
from tests.unit.fakes import (
    FakeDataQualityLog,
    FakeGraphRepository,
    FakeMetricsSink,
    FakeSearchIndex,
    repository_registry,
)

KEYS = ShardKeyService(3)


def handler():
    parts = {
        "repositories": repository_registry(),
        "indexes": {k: FakeSearchIndex() for k in DocumentKind if k.is_indexed},
        "graph": FakeGraphRepository(),
        "metrics": FakeMetricsSink(),
        "quality_log": FakeDataQualityLog(),
    }
    return IngestDocumentHandler(
        **parts, validator=DocumentValidator(), shard_keys=KEYS, classifier=EnvironmentClassifier(),
        vocabulary=VocabularyNormalizer({"dungeons-and-dragons": "dnd", "dnd": "dnd"}),
    ), parts


CRAWLED = {
    "kind": "adventure", "origin": "csv", "itch_id": "games/945604", "title": "The Drowned Chapel",
    "synopsis": "A gothic one-shot.", "tags": json.dumps(["Dungeons and Dragons", "horror"]),
    "level_min": "3", "level_max": "5", "price": "", "rating_avg": "4.5", "ratings_count": "12",
    "author_ids": json.dumps(["author-1"]), "derived_fields": json.dumps(["level_min", "level_max"]),
    "reviews": json.dumps([{"review_id": "1", "author_name": "Mira", "text": "Ran it twice!"}]),
}


async def test_adventure_reaches_three_stores_with_shard_key() -> None:
    ingest, parts = handler()
    report = await ingest.handle([CRAWLED], "csv:adventure")
    assert (report.saved, report.indexed, report.rejected) == (1, 1, 0)
    adventure_id = DocumentKind.ADVENTURE.natural_id("games/945604")
    saved = await parts["repositories"][DocumentKind.ADVENTURE].get(adventure_id)
    assert saved.shard_key == KEYS.key_value(adventure_id) and saved.levels.minimum == 3
    assert saved.tags == ["dnd", "horror"] and saved.is_free and saved.slug.startswith("the-drowned-chapel-")
    indexed = parts["indexes"][DocumentKind.ADVENTURE].indexed[0]
    assert indexed["reviews"] == [{"text": "Ran it twice!"}] and indexed["is_free"] and indexed["reviews_count"] == 1
    assert parts["graph"].merged[0]["tags"] == ["dnd", "horror"]
    counters = parts["metrics"].counters
    assert counters["ingest.saved.adventure"] == 1
    assert counters["ingest.filled.adventures.csv"] == 1 and counters["ingest.filled.level_min.csv"] == 1
    assert "ingest.filled.price.csv" not in counters


async def test_invalid_documents_go_to_the_rejection_log() -> None:
    """Негативний сценарій: документ без обов'язкових полів не зберігається."""
    ingest, parts = handler()
    broken = [
        {"kind": "adventure", "origin": "generated", "title": "No synopsis"},
        {"kind": "monster", "origin": "csv", "name": "Goblin"},
        {"kind": "spaceship", "origin": "csv"},
        {**CRAWLED, "level_min": "three"},
    ]
    report = await ingest.handle(broken, "test")
    assert report.saved == 0 and report.rejected == 4
    assert len(parts["quality_log"].rejected_documents) == 4
    assert parts["metrics"].counters["ingest.rejected.adventure"] == 2
    assert parts["metrics"].counters["ingest.rejected.unknown"] == 1
    assert await parts["repositories"][DocumentKind.ADVENTURE].count() == 0


async def test_monster_environment_and_encounter_outlier_are_computed() -> None:
    ingest, parts = handler()
    await ingest.handle([
        {"kind": "monster", "origin": "csv", "slug": "zombie", "name": "Zombie", "type": "undead",
         "challenge_rating": "0.25", "statblock": json.dumps({"speed": {"walk": 20}})},
        {"kind": "encounter", "origin": "csv", "encounter_id": "1650000000-abc", "started_at": "1650000000",
         "ended_at": "1650040000", "commands": "init attack|cast"},
    ], "csv")
    monster = await parts["repositories"][DocumentKind.MONSTER].get(DocumentKind.MONSTER.natural_id("zombie"))
    assert monster.environment == ["graveyard", "underdark"] and "environment" in monster.provenance.derived_fields
    encounter = await parts["repositories"][DocumentKind.ENCOUNTER].get("1650000000-abc")
    assert encounter.duration_seconds == 40000 and encounter.is_abandoned


async def test_vocabularies_and_unknown_terms() -> None:
    ingest, parts = handler()
    report = await ingest.handle([
        {"kind": "character", "origin": "csv", "name": "Aria", "char_class": "Bloodhunter", "controller_hash": "c1"},
        {"kind": "character", "origin": "csv", "name": "Bo", "char_class": "Gunslinger", "controller_hash": "c2"},
        {**CRAWLED, "tags": json.dumps(["mystery"])},
    ], "csv")
    aria = await parts["repositories"][DocumentKind.CHARACTER].get(DocumentKind.CHARACTER.natural_id("Aria:c1"))
    assert aria.char_class == "Blood Hunter"
    assert ("class", "Gunslinger") in parts["quality_log"].unknown_terms
    assert ("tag", "mystery") in parts["quality_log"].unknown_terms and "class:Gunslinger" in report.unknown_terms


async def test_derived_author_counters_are_recomputed() -> None:
    ingest, parts = handler()
    await ingest.handle([{"kind": "author", "origin": "csv", "author_id": "author-1", "name": "Mira"}], "csv")
    ingest._last_refresh = 0.0
    await ingest.handle([CRAWLED, {**CRAWLED, "itch_id": "games/2", "rating_avg": "3.5"}], "csv")
    author = await parts["repositories"][DocumentKind.AUTHOR].get("author-1")
    assert author.adventures_count == 2 and author.avg_rating == 4.0


async def test_derived_counters_catch_up_without_new_batches() -> None:
    """Похідні лічильники перераховуються за таймером і тоді, коли черга
    спорожніла: відставання тимчасове (таблиця 4.2)."""
    ingest, parts = handler()
    ingest.DERIVED_REFRESH_SECONDS = 0.05
    await ingest.handle([{"kind": "author", "origin": "csv", "author_id": "author-1", "name": "Mira"}], "csv")
    await ingest.handle([CRAWLED], "csv")
    author = await parts["repositories"][DocumentKind.AUTHOR].get("author-1")
    assert author.adventures_count == 0  # перерахунок ще не на часі
    await asyncio.sleep(0.15)
    author = await parts["repositories"][DocumentKind.AUTHOR].get("author-1")
    assert author.adventures_count == 1


async def test_storage_failure_is_raised_so_the_batch_stays_unacknowledged() -> None:
    ingest, parts = handler()

    async def broken(_):
        raise ConnectionError("shard unavailable")

    parts["indexes"][DocumentKind.ADVENTURE].index_many = broken
    try:
        await ingest.handle([CRAWLED], "csv")
    except ConnectionError:
        pass
    else:
        raise AssertionError("збій сховища проковтнуто")
