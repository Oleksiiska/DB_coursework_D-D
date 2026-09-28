"""Композиційний корінь.

Єдиний модуль, який знає і про порти, і про конкретні реалізації.
Решта коду оперує лише інтерфейсами.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.application.commands import ConfigureRateHandler, IngestDocumentHandler
from app.application.queries import (
    GetAdventureCardHandler,
    GetAuthorCardHandler,
    GetCharacterCardHandler,
    GetLiveStatsHandler,
    GetMonsterCardHandler,
    GetPlayStatisticsHandler,
    GetRecommendationsHandler,
    SearchAdventuresHandler,
    SearchEncountersHandler,
    SearchMonstersHandler,
)
from app.domain.ports import (
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
from app.domain.services import (
    DocumentValidator,
    EnvironmentClassifier,
    QueryNormalizer,
    ShardKeyService,
    VocabularyNormalizer,
)
from app.domain.values import DocumentKind
from app.infrastructure.messaging import QueueAdapter
from app.infrastructure.observability import FileDataQualityLog
from app.infrastructure.persistence import (
    GraphAdapter,
    MongoAdventureRepository,
    MongoCharacterRepository,
    MongoCrudRepository,
    MongoEncounterRepository,
    MongoMonsterRepository,
    RedisCacheAdapter,
    RedisMetricsAdapter,
    SearchIndexAdapter,
)
from app.infrastructure.sources import CrawlerSource, CsvSource, GeneratorSource


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
    quality_log_dir: str = "./logs"
    batch_size: int = 500
    default_rate: int = 100
    log_level: str = "INFO"

    #: Назви індексів за видом документа
    index_names: dict[DocumentKind, str] = field(
        default_factory=lambda: {
            DocumentKind.ADVENTURE: "adventures_idx",
            DocumentKind.MONSTER: "monsters_idx",
            DocumentKind.ENCOUNTER: "encounters_idx",
        }
    )

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
    #: Повний реєстр репозиторіїв за видом документа — для шляху запису
    repositories: dict[DocumentKind, CrudRepository]
    indexes: dict[DocumentKind, SearchIndex]
    graph: GraphRepository
    cache: CacheStore
    metrics: MetricsSink
    publisher: EventPublisher
    consumer: EventConsumer
    quality_log: DataQualityLog
    sources: dict[str, DocumentSource] = field(default_factory=dict)


@dataclass(slots=True)
class UseCases:
    """Набір реалізованих вхідних портів."""

    search_adventures: SearchAdventuresHandler
    search_monsters: SearchMonstersHandler
    search_encounters: SearchEncountersHandler
    adventure_card: GetAdventureCardHandler
    monster_card: GetMonsterCardHandler
    character_card: GetCharacterCardHandler
    author_card: GetAuthorCardHandler
    recommendations: GetRecommendationsHandler
    play_statistics: GetPlayStatisticsHandler
    live_stats: GetLiveStatsHandler
    ingest: IngestDocumentHandler
    configure_rate: ConfigureRateHandler


class Container:
    """Складання об'єктного графа під час запуску.

    Заміна сховища чи джерела зачіпає лише цей клас: сценарії
    використання залежать від інтерфейсів і не змінюються.
    """

    #: Спеціалізовані репозиторії за видом документа; решта видів
    #: (автори, теги, світи, записи участі) — узагальнений `MongoCrudRepository`
    REPOSITORY_CLASSES: dict[DocumentKind, type] = {
        DocumentKind.ADVENTURE: MongoAdventureRepository,
        DocumentKind.MONSTER: MongoMonsterRepository,
        DocumentKind.CHARACTER: MongoCharacterRepository,
        DocumentKind.ENCOUNTER: MongoEncounterRepository,
    }
    GENERIC_REPOSITORY: type = MongoCrudRepository

    #: Реалізації портів, що не залежать від виду документа
    ADAPTER_CLASSES: dict[str, type] = {
        "index": SearchIndexAdapter,
        "graph": GraphAdapter,
        "cache": RedisCacheAdapter,
        "metrics": RedisMetricsAdapter,
        "queue": QueueAdapter,
        "quality_log": FileDataQualityLog,
    }

    #: Джерела документів за назвою процесу
    SOURCE_CLASSES: dict[str, type] = {
        "csv": CsvSource,
        "generator": GeneratorSource,
        "crawler": CrawlerSource,
    }

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
        """Створення клієнтів сховищ і адаптерів.

        Автори, теги, світи й записи участі отримують узагальнений
        `MongoCrudRepository` з універсальним відображенням.
        """
        raise NotImplementedError

    def build_use_cases(self, adapters: Adapters) -> UseCases:
        """Підстановка адаптерів в обробники."""
        raise NotImplementedError

    async def shutdown(self) -> None:
        """Коректне закриття з'єднань."""
        raise NotImplementedError
