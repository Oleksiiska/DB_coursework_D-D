"""Композиційний корінь.

Єдиний модуль, який знає і про порти, і про конкретні реалізації.
Решта коду оперує лише інтерфейсами.
"""

from __future__ import annotations

import csv
import dataclasses
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from app.application.commands import ConfigureRateHandler, IngestDocumentHandler
from app.application.commands.documents import assign_natural_id, build_entity
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
from app.domain.values import DocumentKind, SourceOrigin
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
from app.infrastructure.sources import CrawlerSource, CsvSource, GeneratorSource, MarkovTextBuilder, PolitenessPolicy
from app.infrastructure.sources.generator_source import DEFAULT_PROFILES, calibrate_author_pool

#: Каталог пакета app/: списки вручну лежать поруч із кодом і потрапляють
#: в образ разом із ним, тож шлях до них не залежить від робочого каталогу
APP_DIR = Path(__file__).resolve().parents[2]

log = logging.getLogger("launcher")


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
    #: Списки, складені вручну (п. 2.1): світи, системи, видавництва, псевдоніми тегів
    data_dir: str = str(APP_DIR / "data")
    quality_log_dir: str = "./logs"
    #: Каталог журналів процесів; у контейнері монтується з хоста
    log_dir: str = "./logs"
    #: Конфігурація журналів (п. 4.10); у контейнері — /app/config/logging.yaml лише для читання
    log_config: str = "config/logging.yaml"
    #: Якщо задано, перекриває рівень кореневого журналу з конфігурації
    log_level: str = ""
    #: Ім'я хоста контейнера — для файлів журналів масштабованих процесів
    hostname: str = ""
    batch_size: int = 500
    #: Цільова сумарна швидкість генератора до першої зміни з інтерфейсу
    default_rate: int = 100
    #: Швидкість читання CSV, рядків за секунду (п. 4.8)
    csv_rate: int = 1000
    #: Межа глибини черги в пакетах і пауза перед повторною перевіркою (п. 4.8)
    queue_limit: int = 50
    queue_pause_ms: int = 100
    crawler_base_url: str = "https://itch.io"
    crawler_tag: str = "dnd"
    api_host: str = "0.0.0.0"
    api_port: int = 8000

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

    #: Шардовані колекції: лише пригоди необмежено ростуть під
    #: навантаженням генератора, решта лежить на первинному шарді (п. 4.3)
    SHARDED_KINDS: frozenset[DocumentKind] = frozenset({DocumentKind.ADVENTURE})

    #: Підготовлені файли за видом документа (таблиця 2.3); ігрові світи —
    #: список вручну, тому їхнє походження manual
    CSV_FILES: dict[DocumentKind, str] = {
        DocumentKind.MONSTER: "monsters.csv",
        DocumentKind.CHARACTER: "characters.csv",
        DocumentKind.ENCOUNTER: "encounters.csv",
        DocumentKind.PARTICIPATION: "participations.csv",
        DocumentKind.ADVENTURE: "adventures.csv",
        DocumentKind.AUTHOR: "authors.csv",
        DocumentKind.TAG: "tags.csv",
    }
    MANUAL_FILES: dict[DocumentKind, str] = {DocumentKind.SETTING: "settings.json"}

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

    #: Файл журналу за назвою процесу; масштабовані процеси пишуть кожен
    #: у власний файл з ім'ям контейнера (п. 4.10)
    LOG_FILES: dict[str, str] = {"csv": "csv-loader", "ingest": "ingest-worker"}
    SCALED_PROCESSES: frozenset[str] = frozenset({"generator", "ingest"})

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._services = self._build_services()
        self._documents: Any = None
        self._redis: dict[str, Any] = {}
        self._search: Any = None
        self._graph: Any = None
        self._repositories: dict[DocumentKind, CrudRepository] = {}

    @property
    def settings(self) -> Settings:
        return self._settings

    def configure_logging(self, process: str) -> Path | None:
        """Журнали процесу: config/logging.yaml через dictConfig, за його
        відсутності — вбудована конфігурація INFO; LOG_LEVEL перекриває рівень."""
        name = self.LOG_FILES.get(process, process)
        if process in self.SCALED_PROCESSES and self._settings.hostname:
            name = f"{name}-{self._settings.hostname}"
        configurator = LoggingConfigurator(self._settings.log_dir, process=name, config_path=self._settings.log_config)
        return configurator.configure(self._settings.log_level or None)

    # --- клієнти сховищ ---

    async def connect_documents(self) -> None:
        """Клієнт документного сховища. Зв'язок перевіряється одразу:
        процес, що не дістався до сховища, має впасти на старті, а не
        на першому записі."""
        from pymongo import AsyncMongoClient

        self._documents = AsyncMongoClient(
            self._settings.mongo_uri,
            tz_aware=True,
            serverSelectionTimeoutMS=self._settings.mongo_timeout_ms,
            # Запис підтверджується лише після запису в журнал на диску
            # (п. 4.4): у кластері це значення за замовчуванням, а на одному
            # вузлі без явного w запис підтверджувався б ще з пам'яті
            w="majority",
        )
        await self._documents.admin.command("ping")

    def documents_database(self) -> Any:
        """База документного сховища з налаштувань — для службових операцій
        (очищення тестових даних), яких порти не передбачають."""
        if self._documents is None:
            raise RuntimeError("документне сховище не під'єднано: спершу connect_documents()")
        return self._documents[self._settings.mongo_database]

    def redis(self, url: str | None = None) -> Any:
        """Клієнт Redis (redis.asyncio) — черга, кеш і лічильники."""
        from redis.asyncio import Redis

        url = url or self._settings.cache_url
        if url not in self._redis:
            self._redis[url] = Redis.from_url(url, health_check_interval=30)
        return self._redis[url]

    def search_client(self) -> Any:
        """Клієнт пошукового рушія (AsyncElasticsearch)."""
        if self._search is None:
            from elasticsearch import AsyncElasticsearch

            self._search = AsyncElasticsearch(self._settings.search_url, request_timeout=30, retry_on_timeout=True)
        return self._search

    def graph_driver(self) -> Any:
        """Асинхронний драйвер графового сховища; автентифікацію в локальному
        стенді вимкнено (таблиця 4.8)."""
        if self._graph is None:
            from neo4j import AsyncGraphDatabase

            self._graph = AsyncGraphDatabase.driver(self._settings.graph_uri, auth=None)
        return self._graph

    # --- адаптери ---

    def repository(self, kind: DocumentKind) -> CrudRepository:
        """Репозиторій виду документа: спеціалізований або узагальнений."""
        if self._documents is None:
            raise RuntimeError("документне сховище не під'єднано: спершу connect_documents()")
        if kind not in self._repositories:
            entity_type, collection, key_fields = self.ENTITY_BINDINGS[kind]
            repository_class = self.REPOSITORY_CLASSES.get(kind, self.GENERIC_REPOSITORY)
            mapper = DataclassDocumentMapper(entity_type, key_fields)
            shard_key_of = self.shard_keys().key_value if kind in self.SHARDED_KINDS else None
            self._repositories[kind] = repository_class(
                self._documents, collection, mapper, database=self._settings.mongo_database, shard_key_of=shard_key_of
            )
        return self._repositories[kind]

    def shard_keys(self) -> ShardKeyService:
        """Доменна служба ключа розподілу: та сама для запису й читання."""
        return self._services["shard_keys"]

    def cache(self) -> RedisCacheAdapter:
        return RedisCacheAdapter(self.redis(self._settings.cache_url))

    def metrics(self) -> RedisMetricsAdapter:
        return RedisMetricsAdapter(self.redis(self._settings.cache_url))

    def queue(self) -> QueueAdapter:
        return QueueAdapter(self.redis(self._settings.queue_url))

    def indexes(self) -> dict[DocumentKind, SearchIndexAdapter]:
        client = self.search_client()
        return {
            kind: SearchIndexAdapter(client, name, kind=kind.plural)
            for kind, name in self._settings.index_names.items()
        }

    def quality_log(self) -> FileDataQualityLog:
        """Журнали браку й нерозпізнаних значень — окремі файли для кожного
        екземпляра процесу запису."""
        suffix = f"-{self._settings.hostname}" if self._settings.hostname else ""
        directory = Path(self._settings.quality_log_dir) / "quality"
        return FileDataQualityLog(directory / f"rejected{suffix}.jsonl", directory / f"unknown-terms{suffix}.jsonl")

    async def build_adapters(self) -> Adapters:
        """Створення клієнтів сховищ і адаптерів.

        Автори, теги, світи й записи участі отримують узагальнений
        `MongoCrudRepository` з універсальним відображенням.
        """
        if self._documents is None:
            await self.connect_documents()
        repositories = {kind: self.repository(kind) for kind in DocumentKind}
        queue = self.queue()
        return Adapters(
            adventures=repositories[DocumentKind.ADVENTURE],
            monsters=repositories[DocumentKind.MONSTER],
            characters=repositories[DocumentKind.CHARACTER],
            encounters=repositories[DocumentKind.ENCOUNTER],
            repositories=repositories,
            indexes=self.indexes(),
            graph=GraphAdapter(self.graph_driver()),
            cache=self.cache(),
            metrics=self.metrics(),
            publisher=queue,
            consumer=queue,
            quality_log=self.quality_log(),
        )

    async def ensure_storage(self, adapters: Adapters) -> None:
        """Вторинні індекси документного сховища, пошукові індекси таблиці 4.12
        й обмеження графа; повторний виклик нічого не змінює."""
        for repository in adapters.repositories.values():
            await repository.ensure_indexes()
        for index in adapters.indexes.values():
            await index.ensure_index()
        await adapters.graph.ensure_schema()

    def build_use_cases(self, adapters: Adapters) -> UseCases:
        """Підстановка адаптерів в обробники."""
        s = self._services
        search_args = dict(cache=adapters.cache, metrics=adapters.metrics, normalizer=s["normalizer"])
        return UseCases(
            search_adventures=SearchAdventuresHandler(
                index=adapters.indexes[DocumentKind.ADVENTURE], repository=adapters.adventures, **search_args),
            search_monsters=SearchMonstersHandler(
                index=adapters.indexes[DocumentKind.MONSTER], repository=adapters.monsters, **search_args),
            search_encounters=SearchEncountersHandler(
                index=adapters.indexes[DocumentKind.ENCOUNTER], repository=adapters.encounters, **search_args),
            adventure_card=GetAdventureCardHandler(adapters.adventures, adapters.cache),
            monster_card=GetMonsterCardHandler(adapters.monsters, adapters.graph, adapters.cache),
            character_card=GetCharacterCardHandler(adapters.characters, adapters.cache),
            author_card=GetAuthorCardHandler(
                adapters.repositories[DocumentKind.AUTHOR], adapters.adventures, adapters.graph, adapters.cache),
            recommendations=GetRecommendationsHandler(adapters.graph, adapters.adventures, adapters.cache),
            play_statistics=GetPlayStatisticsHandler(
                adapters.monsters, adapters.characters, adapters.encounters, adapters.cache),
            live_stats=GetLiveStatsHandler(
                adapters.metrics, adapters.cache, adapters.publisher, adapters.adventures,
                queue_limit=self._settings.queue_limit, default_rate=self._settings.default_rate),
            ingest=self.ingest_handler(adapters),
            configure_rate=ConfigureRateHandler(adapters.cache, default_rate=self._settings.default_rate),
        )

    def ingest_handler(self, adapters: Adapters) -> IngestDocumentHandler:
        s = self._services
        return IngestDocumentHandler(
            repositories=adapters.repositories,
            indexes=adapters.indexes,
            graph=adapters.graph,
            metrics=adapters.metrics,
            quality_log=adapters.quality_log,
            validator=s["validator"],
            shard_keys=s["shard_keys"],
            classifier=s["classifier"],
            vocabulary=s["vocabulary"],
        )

    # --- джерела ---

    def manual_list(self, file_name: str) -> list[dict]:
        """Список, складений вручну, з каталогу app/data/."""
        path = Path(self._settings.data_dir) / file_name
        return json.loads(path.read_text(encoding="utf-8"))

    def file_sources(self) -> dict[DocumentKind, DocumentSource]:
        """Джерела процесу CsvLoader: CSV зі скриптів підготовки (origin = csv)
        і ігрові світи зі списку вручну (origin = manual)."""
        rate = self._settings.csv_rate
        sources: dict[DocumentKind, DocumentSource] = {
            kind: CsvSource(Path(self._settings.csv_dir) / name, kind.value, rate=rate)
            for kind, name in self.CSV_FILES.items()
        }
        for kind, name in self.MANUAL_FILES.items():
            sources[kind] = CsvSource(
                Path(self._settings.data_dir) / name, kind.value, rate=rate, origin=SourceOrigin.MANUAL.value,
            )
        return sources

    def generator_source(self) -> GeneratorSource:
        """Генератор (шлях В): пул монстрів SRD із popularity за FIREBALL і
        середовищем за класифікатором, словник тегів, корпус анотацій каталогу
        й описів монстрів, пул синтетичних авторів, відкалібрований за
        розподілом робіт реальних авторів (п. 2.7)."""
        csv_dir = Path(self._settings.csv_dir)
        classifier: EnvironmentClassifier = self._services["classifier"]
        monster_pool: list[dict] = []
        corpus: list[str] = []
        for row in self._read_csv(csv_dir / self.CSV_FILES[DocumentKind.MONSTER]):
            document = {**row, "origin": row.get("origin") or "csv"}
            # той самий ключ, що й на шляху запису: посилання пригод ведуть на картки монстрів
            assign_natural_id(DocumentKind.MONSTER, document)
            try:
                monster = build_entity(DocumentKind.MONSTER, document)
            except (KeyError, ValueError):
                continue
            if not monster.monster_id:
                continue
            environment = monster.environment or classifier.classify(monster)
            monster_pool.append({
                "monster_id": monster.monster_id, "name": monster.name, "type": monster.type,
                "challenge_rating": monster.challenge_rating, "play_count": monster.play_count,
                "environment": environment,
            })
            corpus.extend(text for text in (monster.lore, monster.build_abilities_text()) if text)
        adventure_rows = self._read_csv(csv_dir / self.CSV_FILES[DocumentKind.ADVENTURE])
        corpus.extend(row["synopsis"] for row in adventure_rows if row.get("synopsis"))
        tags = [row["name"] for row in self._read_csv(csv_dir / self.CSV_FILES[DocumentKind.TAG]) if row.get("name")]
        if not monster_pool:
            log.warning("генератор: %s не знайдено або порожній — пригоди без монстрів", csv_dir / "monsters.csv")
        text_builder = MarkovTextBuilder()
        text_builder.train(corpus)
        pool_size, exponent = calibrate_author_pool(adventure_rows)
        return GeneratorSource(
            profiles=DEFAULT_PROFILES,
            text_builder=text_builder,
            monster_pool=monster_pool,
            author_pool=GeneratorSource.synthetic_authors(pool_size, exponent),
            rate=self._settings.default_rate,
            settings=self.manual_list("settings.json"),
            systems=self.manual_list("systems.json"),
            publishers=self.manual_list("publishers.json"),
            tags=tags,
            party_median=self._party_median(csv_dir / self.CSV_FILES[DocumentKind.ENCOUNTER]),
        )

    def crawler_source(self) -> CrawlerSource:
        """Краулер каталогу (шлях Б): сталий User-Agent, затримка 1,5–3 с;
        системи розпізнаються за списком вручну."""
        return CrawlerSource(
            self._settings.crawler_base_url, self._settings.crawler_tag, PolitenessPolicy(),
            systems=self.manual_list("systems.json"),
        )

    def _party_median(self, path: Path) -> int:
        """Типовий розмір партії — медіана кількості персонажів гравців у бою
        за encounters.csv (калібрування players_min/max, п. 2.7.3)."""
        sizes = []
        for row in self._read_csv(path):
            raw = row.get("player_character_ids") or ""
            try:
                ids = json.loads(raw) if raw.startswith("[") else [i for i in raw.split("|") if i]
            except json.JSONDecodeError:
                continue
            if ids:
                sizes.append(len(ids))
        if not sizes:
            return 4
        sizes.sort()
        return max(1, sizes[len(sizes) // 2])

    @staticmethod
    def _read_csv(path: Path) -> list[dict]:
        if not path.is_file():
            return []
        csv.field_size_limit(sys.maxsize)
        with path.open(encoding="utf-8", newline="") as stream:
            return list(csv.DictReader(stream))

    def _tag_aliases(self) -> dict[str, str]:
        """Варіант написання → канонічний тег (app/data/tag_aliases.json)."""
        aliases: dict[str, str] = {}
        for entry in self.manual_list("tag_aliases.json"):
            for alias in [entry["name"], *entry["aliases"]]:
                aliases[alias.lower()] = entry["name"]
        return aliases

    def _build_services(self) -> dict:
        """Доменні служби — без зовнішніх залежностей; псевдоніми тегів
        беруться зі списку вручну."""
        return {
            "normalizer": QueryNormalizer(),
            "shard_keys": ShardKeyService(self._settings.shard_count),
            "classifier": EnvironmentClassifier(),
            "validator": DocumentValidator(),
            "vocabulary": VocabularyNormalizer(self._tag_aliases()),
        }

    async def shutdown(self) -> None:
        """Коректне закриття з'єднань."""
        if self._documents is not None:
            await self._documents.close()
            self._documents = None
            self._repositories.clear()
        for client in self._redis.values():
            await client.aclose()
        self._redis.clear()
        if self._search is not None:
            await self._search.close()
            self._search = None
        if self._graph is not None:
            await self._graph.close()
            self._graph = None
