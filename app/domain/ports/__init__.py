"""Вихідні порти: оголошення інтерфейсів у доменному шарі.

Вхідні порти оголошено в шарі сценаріїв (`app.application.ports`).
"""

from app.domain.ports.outbound import (
    AdventureRepository,
    CacheStore,
    CharacterRepository,
    CrudRepository,
    DataQualityLog,
    DocumentSource,
    EncounterRepository,
    EventConsumer,
    EventPublisher,
    GraphRepository,
    MetricsSink,
    MonsterRepository,
    SearchIndex,
)

__all__ = [
    "AdventureRepository",
    "CacheStore",
    "CharacterRepository",
    "CrudRepository",
    "DataQualityLog",
    "DocumentSource",
    "EncounterRepository",
    "EventConsumer",
    "EventPublisher",
    "GraphRepository",
    "MetricsSink",
    "MonsterRepository",
    "SearchIndex",
]
