"""Модульні тести сценарію приймання документів."""

from __future__ import annotations

import pytest

from app.application.commands import IngestDocumentHandler
from app.application.ports import IngestDocumentUseCase
from app.domain.services import (
    DocumentValidator,
    EnvironmentClassifier,
    ShardKeyService,
    VocabularyNormalizer,
)
from app.domain.values import DocumentKind
from tests.unit.fakes import (
    FakeDataQualityLog,
    FakeGraphRepository,
    FakeMetricsSink,
    FakeSearchIndex,
    repository_registry,
)


def build(repositories=None, indexes=None) -> IngestDocumentHandler:
    return IngestDocumentHandler(
        repositories=repository_registry() if repositories is None else repositories,
        indexes={k: FakeSearchIndex() for k in DocumentKind if k.is_indexed} if indexes is None else indexes,
        graph=FakeGraphRepository(),
        metrics=FakeMetricsSink(),
        quality_log=FakeDataQualityLog(),
        validator=DocumentValidator(),
        shard_keys=ShardKeyService(3),
        classifier=EnvironmentClassifier(),
        vocabulary=VocabularyNormalizer(),
    )


def test_ingest_builds_with_full_registry() -> None:
    assert isinstance(build(), IngestDocumentUseCase)


def test_every_document_kind_has_a_repository() -> None:
    """Вид без репозиторію означав би мовчазну втрату документів."""
    registry = repository_registry()
    del registry[DocumentKind.MONSTER]
    with pytest.raises(ValueError, match="monster"):
        build(repositories=registry)


def test_every_indexed_kind_has_an_index() -> None:
    with pytest.raises(ValueError, match="encounter"):
        build(indexes={DocumentKind.ADVENTURE: FakeSearchIndex(), DocumentKind.MONSTER: FakeSearchIndex()})
