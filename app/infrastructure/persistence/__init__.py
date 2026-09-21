"""Адаптери сховищ."""

from app.infrastructure.persistence.graph_adapter import GraphAdapter
from app.infrastructure.persistence.mongo_repository import (
    DocumentMapper,
    MongoAdventureRepository,
    MongoCharacterRepository,
    MongoCrudRepository,
    MongoEncounterRepository,
    MongoMonsterRepository,
)
from app.infrastructure.persistence.redis_adapter import RedisCacheAdapter, RedisMetricsAdapter
from app.infrastructure.persistence.search_adapter import SearchIndexAdapter

__all__ = [
    "DocumentMapper",
    "GraphAdapter",
    "MongoAdventureRepository",
    "MongoCharacterRepository",
    "MongoCrudRepository",
    "MongoEncounterRepository",
    "MongoMonsterRepository",
    "RedisCacheAdapter",
    "RedisMetricsAdapter",
    "SearchIndexAdapter",
]
