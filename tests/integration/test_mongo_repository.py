"""Узагальнений репозиторій на справжньому документному сховищі.

Виконується, якщо задано MONGO_URI, наприклад у контейнері застосунку
поруч із контейнером сховища:  MONGO_URI=mongodb://localhost:27017 pytest tests/integration
"""

from __future__ import annotations

import os
from dataclasses import replace

import pytest

from app.domain.values import DocumentKind
from app.infrastructure.config import Container, Settings
from workers.storage_check import StorageCheck

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif("MONGO_URI" not in os.environ, reason="потребує MONGO_URI"),
]


@pytest.fixture
async def repository():
    container = Container(Settings.from_env({**os.environ, "MONGO_DATABASE": "dnd_test"}))
    await container.connect_documents()
    yield container.repository(DocumentKind.ADVENTURE)
    await container._documents.drop_database("dnd_test")
    await container.shutdown()


async def test_add_then_get_returns_equal_entity(repository) -> None:
    adventure = StorageCheck.sample()
    await repository.add(adventure)
    assert await repository.get(adventure.adventure_id) == adventure


async def test_add_is_idempotent(repository) -> None:
    """Повторна доставка пакета чергою не створює дублікатів."""
    adventure = StorageCheck.sample()
    await repository.add(adventure)
    await repository.add(adventure)
    assert await repository.count() == 1


async def test_batch_write_and_ordered_read(repository) -> None:
    batch = [StorageCheck.sample() for _ in range(3)]
    assert await repository.add_many(batch) == 3
    ids = [a.adventure_id for a in reversed(batch)]
    assert [a.adventure_id for a in await repository.get_many(ids)] == ids


async def test_update_and_delete(repository) -> None:
    adventure = StorageCheck.sample()
    await repository.add(adventure)
    assert await repository.update(replace(adventure, title="Нова назва"))
    assert (await repository.get(adventure.adventure_id)).title == "Нова назва"
    assert await repository.delete(adventure.adventure_id)
    assert await repository.get(adventure.adventure_id) is None
