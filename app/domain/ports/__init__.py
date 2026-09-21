"""Порти: оголошення інтерфейсів у доменному шарі."""

from app.domain.ports.outbound import (
    AdventureRepository,
    CacheStore,
    CharacterRepository,
    CrudRepository,
    DocumentSource,
    EncounterRepository,
    EventPublisher,
    GraphRepository,
    MetricsSink,
    MonsterRepository,
    SearchIndex,
    ShardKeyProvider,
)

__all__ = [
    "AdventureRepository",
    "CacheStore",
    "CharacterRepository",
    "CrudRepository",
    "DocumentSource",
    "EncounterRepository",
    "EventPublisher",
    "GraphRepository",
    "MetricsSink",
    "MonsterRepository",
    "SearchIndex",
    "ShardKeyProvider",
]
