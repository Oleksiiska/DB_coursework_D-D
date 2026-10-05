"""Композиційний корінь.

Єдиний модуль, який знає і про порти, і про конкретні реалізації.
Решта коду оперує лише інтерфейсами.
"""

from __future__ import annotations

import dataclasses
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

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
from app.domain.entities import (
    Adventure,
    Author,
    Encounter,
    Monster,
    MonsterParticipation,
    PlayerCharacter,
    Setting,
    Tag,
)
from app.domain.values import DocumentKind
from app.infrastructure.messaging import QueueAdapter
from app.infrastructure.observability import FileDataQualityLog, LoggingConfigurator
from app.infrastructure.persistence import (
    DataclassDocumentMapper,
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
    mongo_database: str = "dnd"
    #: Скільки чекати на сховище при старті: у контейнерах воно може
    #: підніматися довше за застосунок
    mongo_timeout_ms: int = 30000
    search_url: str = "http://localhost:9200"
    graph_uri: str = "bolt://localhost:7687"
    cache_url: str = "redis://localhost:6379"
    queue_url: str = "redis://localhost:6379"

    shard_count: int = 3
    csv_dir: str = "./data/csv"
    quality_log_dir: str = "./logs"
    #: Каталог журналів процесів; у контейнері монтується з хоста
    log_dir: str = "./logs"
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
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        """Значення зі змінних середовища з тими самими назвами великими літерами.

        Конфігурація контейнера задається лише змінними середовища,
        тому той самий образ працює локально, у Compose і в оркестраторі.
        """
        env = os.environ if env is None else env
        values: dict[str, Any] = {}
        for f in dataclasses.fields(cls):
            raw = env.get(f.name.upper())
            if raw is None or f.type not in ("str", "int"):
                continue
            values[f.name] = int(raw) if f.type == "int" else raw
        return cls(**values)

    def redacted_mongo_uri(self) -> str:
        """Адреса сховища без пароля: журнал зберігається на диску хоста."""
        return re.sub(r"(://[^:@/]+:)[^@/]*@", r"\1***@", self.mongo_uri)


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

    #: Сутність, колекція й поля ключа документа для кожного виду.
    #: Участь монстра власного ключа не має — ключ складений.
    ENTITY_BINDINGS: dict[DocumentKind, tuple[type, str, tuple[str, ...]]] = {
        DocumentKind.ADVENTURE: (Adventure, "adventures", ("adventure_id",)),
        DocumentKind.AUTHOR: (Author, "authors", ("author_id",)),
        DocumentKind.TAG: (Tag, "tags", ("tag_id",)),
        DocumentKind.SETTING: (Setting, "settings", ("setting_id",)),
        DocumentKind.MONSTER: (Monster, "monsters", ("monster_id",)),
        DocumentKind.CHARACTER: (PlayerCharacter, "characters", ("character_id",)),
        DocumentKind.ENCOUNTER: (Encounter, "encounters", ("encounter_id",)),
        DocumentKind.PARTICIPATION: (MonsterParticipation, "participations", ("encounter_id", "instance_label")),
    }

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._services = self._build_services()
        self._documents: Any = None

    def configure_logging(self, process: str) -> Path | None:
        """Журнали процесу: стандартний вивід і файл у каталозі журналів."""
        configurator = LoggingConfigurator(self._settings.log_dir, process=process)
        return configurator.configure(self._settings.log_level)

    async def connect_documents(self) -> None:
        """Клієнт документного сховища. Зв'язок перевіряється одразу:
        процес, що не дістався до сховища, має впасти на старті, а не
        на першому записі."""
        from pymongo import AsyncMongoClient

        self._documents = AsyncMongoClient(
            self._settings.mongo_uri,
            tz_aware=True,
            serverSelectionTimeoutMS=self._settings.mongo_timeout_ms,
        )
        await self._documents.admin.command("ping")

    def repository(self, kind: DocumentKind) -> CrudRepository:
        """Репозиторій виду документа: спеціалізований або узагальнений."""
        if self._documents is None:
            raise RuntimeError("документне сховище не під'єднано: спершу connect_documents()")
        entity_type, collection, key_fields = self.ENTITY_BINDINGS[kind]
        repository_class = self.REPOSITORY_CLASSES.get(kind, self.GENERIC_REPOSITORY)
        mapper = DataclassDocumentMapper(entity_type, key_fields)
        return repository_class(self._documents, collection, mapper, database=self._settings.mongo_database)

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
        if self._documents is not None:
            await self._documents.close()
            self._documents = None
