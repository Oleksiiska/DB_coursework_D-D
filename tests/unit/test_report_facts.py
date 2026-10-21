"""Значення, наведені у звіті, збігаються з кодом.

Кожен тест посилається на пункт звіту. Зміна значення в коді без правки
звіту (або навпаки) ламає тест, тож розбіжність між ними не накопичується
непомітно.
"""

from __future__ import annotations

import dataclasses
import inspect
import json
import re
import uuid
from pathlib import Path

from app.application.dto import MonsterCardDTO, SearchResultDTO
from app.application.ports import QUERY_GROUP_COVERAGE, GetMonsterCardUseCase, GetRecommendationsUseCase
from app.application.queries import (
    CachedSearchHandler,
    SearchAdventuresHandler,
    SearchEncountersHandler,
    SearchMonstersHandler,
)
from app.domain.ports import GraphRepository
from app.domain.services import DocumentValidator, QueryNormalizer, ShardKeyService
from app.domain.values import DocumentKind, SearchHits, SourceOrigin
from app.infrastructure.config import Container, Settings
from app.infrastructure.sources import CrawlerSource
from app.infrastructure.sources.crawler_source import PolitenessPolicy
from workers.csv_loader import CsvLoader

PROJECT = Path(__file__).resolve().parents[2]
DATA = PROJECT / "app" / "data"


def _fields(cls: type) -> set[str]:
    return {f.name for f in dataclasses.fields(cls)}


def test_search_results_live_in_cache_for_60_seconds() -> None:
    """П. 4.3: результати пошуку кешуються з часом життя 60 с."""
    assert CachedSearchHandler.CACHE_TTL_SECONDS == 60
    for handler in (SearchAdventuresHandler, SearchMonstersHandler, SearchEncountersHandler):
        assert handler.CACHE_TTL_SECONDS == 60


def test_cache_key_covers_page_and_sort() -> None:
    """П. 3.8.5: ключ кешу — вид, текст, фільтри, сторінка й сортування."""
    parameters = list(inspect.signature(QueryNormalizer.cache_key).parameters)
    assert parameters == ["self", "kind", "raw", "spec", "page", "sort"]


def test_search_result_carries_tag_counts() -> None:
    """П. 3.8.5, група запитів 2: кількість пригод за тегами у видачі."""
    assert "tag_counts" in _fields(SearchHits)
    assert "tag_counts" in _fields(SearchResultDTO)


def test_monsters_seen_together_serve_query_group_8() -> None:
    """Таблиця 1.2, група 8: монстри, що трапляються разом у реальних боях."""
    assert set(QUERY_GROUP_COVERAGE[8]) == {GetRecommendationsUseCase, GetMonsterCardUseCase}
    assert "seen_together" in _fields(MonsterCardDTO)
    assert callable(getattr(GraphRepository, "seen_together", None))


def test_settings_come_from_a_manual_list_first() -> None:
    """П. 2.1 і 2.2: ігрові світи — список вручну, origin = manual,
    і завантажуються першими."""
    assert SourceOrigin.MANUAL.value == "manual"
    assert Container.MANUAL_FILES == {DocumentKind.SETTING: "settings.json"}
    assert CsvLoader.ORDER[0] is DocumentKind.SETTING
    assert set(CsvLoader.ORDER) == set(Container.CSV_FILES) | set(Container.MANUAL_FILES)


def test_manual_lists_are_consistent() -> None:
    """Таблиця 4.7: 10–15 ігрових світів; ключі детерміновані (п. 2.8.3)."""
    settings = json.loads((DATA / "settings.json").read_text(encoding="utf-8"))
    assert 10 <= len(settings) <= 15
    namespace = uuid.uuid5(uuid.NAMESPACE_URL, "https://github.com/Oleksiiska/DB_coursework_D-D/settings")
    ids = {s["setting_id"] for s in settings}
    assert len(ids) == len(settings)
    for setting in settings:
        assert setting["setting_id"] == str(uuid.uuid5(namespace, setting["slug"]))
        assert set(setting["related_setting_ids"]) <= ids - {setting["setting_id"]}
    for name in ("systems.json", "publishers.json"):
        weights = [entry["weight"] for entry in json.loads((DATA / name).read_text(encoding="utf-8"))]
        assert abs(sum(weights) - 1) < 1e-9, name
    aliases = json.loads((DATA / "tag_aliases.json").read_text(encoding="utf-8"))
    assert all(entry["name"] and entry["aliases"] for entry in aliases)


def test_synopsis_is_required_only_for_generated_adventures() -> None:
    """П. 2.11: синопсис є лише в 96% карток каталогу, тож обов'язковий
    тільки для згенерованих пригод."""
    assert "synopsis" not in DocumentValidator.REQUIRED["adventure"]
    assert DocumentValidator.REQUIRED_BY_ORIGIN[("adventure", SourceOrigin.GENERATED.value)] == ("synopsis",)


def test_crawler_identifies_itself() -> None:
    """П. 2.6.1: сталий User-Agent із назвою проєкту й контактною адресою,
    затримка 1,5–3 с, 36 карток на сторінці списку (п. 2.6.2)."""
    policy = PolitenessPolicy()
    assert policy.user_agent.startswith("DnD-Adventures-Coursework/")
    assert "github.com/Oleksiiska/DB_coursework_D-D" in policy.user_agent
    assert not hasattr(PolitenessPolicy, "next_user_agent")
    defaults = inspect.signature(PolitenessPolicy.__init__).parameters
    assert (defaults["min_delay"].default, defaults["max_delay"].default) == (1.5, 3.0)
    assert CrawlerSource.LIST_PAGE_SIZE == 36


def test_only_adventures_are_sharded() -> None:
    """П. 4.3: шардується лише колекція пригод, на три шарди, 1024 кошики."""
    assert Container.SHARDED_KINDS == frozenset({DocumentKind.ADVENTURE})
    assert Settings().shard_count == 3
    assert ShardKeyService.BUCKETS == 1024


def test_cluster_compose_describes_every_container_of_table_3_7() -> None:
    """Таблиця 3.7: кожен контейнер є в docker-compose.cluster.yml, а процеси
    застосунку звертаються до кластера через маршрутизатор mongos."""
    text = (PROJECT / "docker-compose.cluster.yml").read_text(encoding="utf-8")
    services = set(re.findall(r"^  ([a-z0-9-]+):\s*$", text, re.M))
    expected = {
        "api", "csv-loader", "generator", "crawler", "ingest-worker", "storage-check",
        "mongos", "mongo-config", "mongo-shard-1", "mongo-shard-2", "mongo-shard-3", "mongo-init",
        "elasticsearch", "neo4j", "redis",
    }
    assert expected <= services, expected - services
    assert "MONGO_URI: mongodb://mongos:27017" in text
    assert "./docker/mongo-init.js:/scripts/mongo-init.js:ro" in text


async def test_document_writes_are_acknowledged_after_the_journal(monkeypatch) -> None:
    """П. 4.4.3: запис у документне сховище підтверджується після запису
    в журнал на диску (w: majority) — і в кластері, і на одному вузлі."""
    import pymongo

    captured: dict = {}

    class FakeAdmin:
        async def command(self, name: str) -> dict:
            return {"ok": 1}

    class FakeClient:
        def __init__(self, uri: str, **kwargs) -> None:
            captured.update(kwargs)
            self.admin = FakeAdmin()

        async def close(self) -> None:
            pass

    monkeypatch.setattr(pymongo, "AsyncMongoClient", FakeClient)
    container = Container(Settings())
    await container.connect_documents()
    await container.shutdown()
    assert captured["w"] == "majority"


def test_cache_lifetimes_of_table_4_9() -> None:
    """Таблиця 4.9, п. 4.7: пошук — 60 с, картки — 10 хв, рекомендації — 15 хв, статистика гри — 1 год."""
    from app.application.queries import (
        GetAdventureCardHandler, GetAuthorCardHandler, GetCharacterCardHandler, GetMonsterCardHandler,
        GetPlayStatisticsHandler, GetRecommendationsHandler,
    )

    assert {h.CACHE_TTL_SECONDS for h in (GetAdventureCardHandler, GetMonsterCardHandler,
                                         GetCharacterCardHandler, GetAuthorCardHandler)} == {600}
    assert GetRecommendationsHandler.CACHE_TTL_SECONDS == 900
    assert GetPlayStatisticsHandler.CACHE_TTL_SECONDS == 3600
    assert QueryNormalizer.CACHE_PREFIX == "search" and QueryNormalizer.RULES_VERSION == "v1"


def test_redis_is_bounded_and_persistent() -> None:
    """Таблиця 4.8, п. 4.7: AOF, maxmemory 256 МБ, volatile-lru."""
    text = (PROJECT / "docker-compose.cluster.yml").read_text(encoding="utf-8")
    assert '"--appendonly", "yes", "--maxmemory", "256mb", "--maxmemory-policy", "volatile-lru"' in text


def test_config_is_mounted_read_only_into_every_app_container() -> None:
    """Таблиця 3.7: config/ (лише читання) у всіх контейнерах застосунку."""
    import yaml

    compose = yaml.safe_load((PROJECT / "docker-compose.cluster.yml").read_text(encoding="utf-8"))
    for name in ("api", "csv-loader", "generator", "crawler", "ingest-worker", "storage-check"):
        assert "./config:/app/config:ro" in compose["services"][name]["volumes"], name
    assert compose["services"]["api"]["ports"] == ["127.0.0.1:${API_PORT:-8000}:8000"]
    assert all("profiles" not in service for service in compose["services"].values())
    single = yaml.safe_load((PROJECT / "docker-compose.yml").read_text(encoding="utf-8"))
    assert "./config:/app/config:ro" in single["services"]["storage-check"]["volumes"]


def test_cluster_uses_compound_shard_key() -> None:
    """П. 4.3, таблиця 4.7: { shard_key: 1, _id: 1 }, межі зон — значення
    shard_key і MinKey для _id."""
    script = (PROJECT / "docker" / "mongo-init.js").read_text(encoding="utf-8")
    assert "const SHARD_KEY = { shard_key: 1, _id: 1 };" in script
    assert "_id: bsonMin()" in script and "refineCollectionShardKey" in script


def test_collection_flow_defaults_of_section_4_8() -> None:
    """Межа черги 50 пакетів, пауза 100 мс, CSV_RATE 1000 рядків/с, пакети
    по 500 рядків, вікно сигналу живості генератора 5 с, група споживачів ingest."""
    from app.infrastructure.messaging import QueueAdapter
    from workers.generator_worker import GeneratorWorker

    settings = Settings()
    assert (settings.queue_limit, settings.queue_pause_ms, settings.csv_rate) == (50, 100, 1000)
    assert CsvLoader.BATCH_SIZE == 500 and GeneratorWorker.HEARTBEAT_WINDOW_SECONDS == 5
    assert QueueAdapter.GROUP == "ingest" and QueueAdapter.CLAIM_IDLE_MS == 60_000


def test_index_names_of_table_4_12() -> None:
    assert set(Settings().index_names.values()) == {"adventures_idx", "monsters_idx", "encounters_idx"}


def test_requirements_pin_the_stack_of_table_4_11() -> None:
    text = (PROJECT / "requirements.txt").read_text(encoding="utf-8")
    for package in ("pymongo==4.18", "elasticsearch[async]==9", "neo4j==", "redis==", "fastapi==", "uvicorn==",
                    "httpx==", "beautifulsoup4==", "lxml==", "PyYAML=="):
        assert package in text, package
    assert "locust==" in (PROJECT / "requirements-dev.txt").read_text(encoding="utf-8")
