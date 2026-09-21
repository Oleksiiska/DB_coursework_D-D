"""Композиційний корінь.

Єдиний модуль, який знає і про порти, і про конкретні реалізації.
Решта коду оперує лише інтерфейсами.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.application.commands import ConfigureRateHandler, IngestDocumentHandler
from app.application.queries import (
    GetAdventureCardHandler,
    GetLiveStatsHandler,
    GetMonsterStatsHandler,
    GetRecommendationsHandler,
    SearchAdventuresHandler,
)
from app.domain.ports import (
    AdventureRepository,
    CacheStore,
    CharacterRepository,
    DocumentSource,
    EncounterRepository,
    EventPublisher,
    GraphRepository,
    MetricsSink,
    MonsterRepository,
    SearchIndex,
)
from app.domain.services import (
    DocumentValidator,
    EnvironmentClassifier,
    QueryNormalizer,
    ShardKeyService,
    VocabularyNormalizer,
)


@dataclass(slots=True)
class Settings:
    """Конфігурація з оточення. Значення за замовчуванням — для розробки."""

    mongo_uri: str = "mongodb://localhost:27017"
    search_url: str = "http://localhost:9200"
    graph_uri: str = "bolt://localhost:7687"
    cache_url: str = "redis://localhost:6379"
    queue_url: str = "redis://localhost:6379"

    shard_count: int = 3
    csv_dir: str = "./data/csv"
    batch_size: int = 500
    default_rate: int = 100
    log_level: str = "INFO"
    cache_ttl_search: int = 300
    cache_ttl_card: int = 600

    @classmethod
    def from_env(cls) -> "Settings":
        raise NotImplementedError


@dataclass(slots=True)
class Adapters:
    """Набір реалізованих вихідних портів."""

    adventures: AdventureRepository
    monsters: MonsterRepository
    characters: CharacterRepository
    encounters: EncounterRepository
    index: SearchIndex
    graph: GraphRepository
    cache: CacheStore
    metrics: MetricsSink
    publisher: EventPublisher
    sources: dict[str, DocumentSource] = field(default_factory=dict)


@dataclass(slots=True)
class UseCases:
    """Набір реалізованих вхідних портів."""

    search: SearchAdventuresHandler
    adventure_card: GetAdventureCardHandler
    monster_card: GetMonsterStatsHandler
    recommendations: GetRecommendationsHandler
    live_stats: GetLiveStatsHandler
    ingest: IngestDocumentHandler
    configure_rate: ConfigureRateHandler


class Container:
    """Складання об'єктного графа під час запуску.

    Заміна сховища чи джерела зачіпає лише цей клас: сценарії
    використання залежать від інтерфейсів і не змінюються.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._services = self._build_services()

    def _build_services(self) -> dict:
        """Доменні служби — без зовнішніх залежностей."""
        return {
            "normalizer": QueryNormalizer(),
            "shard_keys": ShardKeyService(self._settings.shard_count),
            "classifier": EnvironmentClassifier(),
            "validator": DocumentValidator(),
            "vocabulary": VocabularyNormalizer(),
        }

    async def build_adapters(self) -> Adapters:
        """Створення клієнтів сховищ і адаптерів."""
        raise NotImplementedError

    def build_use_cases(self, adapters: Adapters) -> UseCases:
        """Підстановка адаптерів в обробники."""
        raise NotImplementedError

    async def shutdown(self) -> None:
        """Коректне закриття з'єднань."""
        raise NotImplementedError
