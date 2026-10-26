"""Узагальнений репозиторій на справжньому документному сховищі.

Виконується, якщо задано MONGO_URI, наприклад у контейнері застосунку
поруч із контейнером сховища:  MONGO_URI=mongodb://localhost:27017 pytest tests/integration
"""

from __future__ import annotations

import os
from dataclasses import replace

import pytest

from app.domain.services import ShardKeyService
from app.domain.values import DocumentKind
from app.infrastructure.config import Container, Settings
from workers.storage_check import StorageCheck

KEYS = ShardKeyService()

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif("MONGO_URI" not in os.environ, reason="потребує MONGO_URI"),
]


@pytest.fixture
async def repository():
    container = Container(Settings.from_env({**os.environ, "MONGO_DATABASE": "dnd_test"}))
    await container.connect_documents()
    yield container.repository(DocumentKind.ADVENTURE)
    await container.documents_database().client.drop_database("dnd_test")
    await container.shutdown()


async def test_add_then_get_returns_equal_entity(repository) -> None:
    adventure = StorageCheck.sample(KEYS)
    await repository.add(adventure)
    assert await repository.get(adventure.adventure_id) == adventure


async def test_add_is_idempotent(repository) -> None:
    """Повторна доставка пакета чергою не створює дублікатів."""
    adventure = StorageCheck.sample(KEYS)
    await repository.add(adventure)
    await repository.add(adventure)
    assert await repository.count() == 1


async def test_batch_write_and_ordered_read(repository) -> None:
    batch = [StorageCheck.sample(KEYS) for _ in range(3)]
    assert await repository.add_many(batch) == 3
    ids = [a.adventure_id for a in reversed(batch)]
    assert [a.adventure_id for a in await repository.get_many(ids)] == ids


async def test_update_and_delete(repository) -> None:
    adventure = StorageCheck.sample(KEYS)
    await repository.add(adventure)
    assert await repository.update(replace(adventure, title="Нова назва"))
    assert (await repository.get(adventure.adventure_id)).title == "Нова назва"
    assert await repository.delete(adventure.adventure_id)
    assert await repository.get(adventure.adventure_id) is None


# --- спеціалізовані запити на справжньому сховищі ---

from datetime import datetime, timezone  # noqa: E402

from app.domain.entities import Encounter, Monster, PlayerCharacter  # noqa: E402
from app.domain.values import AdventureFilters, Provenance, SourceOrigin  # noqa: E402


@pytest.fixture
async def container():
    container = Container(Settings.from_env({**os.environ, "MONGO_DATABASE": "dnd_test"}))
    await container.connect_documents()
    yield container
    await container.documents_database().client.drop_database("dnd_test")
    await container.shutdown()


async def test_adventure_queries_and_author_stats(container) -> None:
    repository = container.repository(DocumentKind.ADVENTURE)
    await repository.ensure_indexes()
    first, second = StorageCheck.sample(KEYS), StorageCheck.sample(KEYS)
    first.author_ids, first.rating_avg, first.published_year = ["a1", "a2"], 4.0, 2020
    second.author_ids, second.rating_avg, second.published_year = ["a1"], 5.0, 2023
    await repository.add_many([first, second])
    assert [a.adventure_id for a in await repository.find_by_author("a1")] == [second.adventure_id, first.adventure_id]
    stats = await repository.author_stats(["a1", "a2"])
    assert stats["a1"] == {"adventures_count": 2, "avg_rating": 4.5, "first_published_year": 2020, "co_author_ids": ["a2"]}
    assert await repository.count(AdventureFilters(level=2, tags=("horror",))) == 2
    assert await repository.count(AdventureFilters(level=7)) == 0
    indexes = await container.documents_database().adventures.index_information()
    assert {"author_ids_1_published_year_-1", "tags_1", "setting_id_1"} <= set(indexes)
    shards = await repository.count_by_shard()
    assert sum(shards.values()) == 2


async def test_play_statistics_aggregations(container) -> None:
    monsters = container.repository(DocumentKind.MONSTER)
    characters = container.repository(DocumentKind.CHARACTER)
    encounters = container.repository(DocumentKind.ENCOUNTER)
    csv = Provenance(origin=SourceOrigin.CSV)
    await monsters.add_many([
        Monster("m1", "Skeleton", "undead", 0.25, csv, play_count=997),
        Monster("m2", "Owlbear", "monstrosity", 3, csv, play_count=120),
    ])
    assert [m.name for m in await monsters.most_played(1)] == ["Skeleton"]
    await characters.add_many([
        PlayerCharacter("c1", "Aria", csv, race="Elf", char_class="Wizard", level=5, spells=["Fireball", "Shield"]),
        PlayerCharacter("c2", "Bo", csv, race="Human", char_class="Wizard", level=5, spells=["Fireball"]),
    ])
    assert await characters.typical_spells("Wizard", 5) == [("Fireball", 2), ("Shield", 1)]
    assert await characters.class_race_distribution() == [("Wizard", "Elf", 1), ("Wizard", "Human", 1)]
    start = datetime(2021, 5, 1, tzinfo=timezone.utc)
    await encounters.add_many([
        Encounter("e1", start, csv, duration_seconds=600, rounds_count=3, player_character_ids=["c1", "c2"]),
        Encounter("e2", start, csv, duration_seconds=900, rounds_count=5, player_character_ids=["c1"]),
        Encounter("e3", start, csv, duration_seconds=50_000, rounds_count=2554, is_abandoned=True),
    ])
    assert await encounters.median_duration() in (600.0, 900.0)
    assert await encounters.median_rounds() in (3.0, 5.0)
    assert await encounters.party_composition() == [("Wizard", 3)]
