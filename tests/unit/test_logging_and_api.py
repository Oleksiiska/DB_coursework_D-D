"""Журнали (п. 4.10) і маршрути API (таблиця 4.15) — без сховищ."""

from __future__ import annotations

import logging
import logging.handlers
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from app.application.dto import LiveStatsDTO, RateSettingDTO, SearchResultDTO
from app.domain.errors import EntityNotFound, ValidationError
from app.infrastructure.config import Container, Settings
from app.infrastructure.observability import LoggingConfigurator

PROJECT = Path(__file__).resolve().parents[2]
CONFIG = PROJECT / "config" / "logging.yaml"


@pytest.fixture
def restore_logging():
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    for handler in handlers:
        root.addHandler(handler)
    root.setLevel(level)


def test_logging_yaml_matches_section_4_10() -> None:
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    assert config["formatters"]["plain"]["format"] == "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    file_handler = config["handlers"]["file"]
    assert file_handler["class"] == "logging.handlers.RotatingFileHandler"
    assert (file_handler["filename"], file_handler["maxBytes"], file_handler["backupCount"]) == (
        "/app/logs/{process}.log", 10485760, 5)
    assert config["root"] == {"level": "INFO", "handlers": ["console", "file"]}
    assert config["loggers"]["db"] == {"level": "INFO"} and config["loggers"]["crawler"] == {"level": "INFO"}


def test_yaml_is_applied_with_process_name(tmp_path, restore_logging) -> None:
    path = LoggingConfigurator(tmp_path, process="generator-abc", config_path=CONFIG).configure()
    assert path.name == "generator-abc.log"
    logging.getLogger("generator").info("перевірка")
    assert "INFO    generator: перевірка" in path.read_text(encoding="utf-8")
    assert any(isinstance(h, logging.handlers.RotatingFileHandler) for h in logging.getLogger().handlers)


def test_builtin_config_and_level_override(tmp_path, restore_logging) -> None:
    path = LoggingConfigurator(tmp_path, process="api", config_path=tmp_path / "missing.yaml").configure("DEBUG")
    assert path == tmp_path / "api.log" and logging.getLogger().level == logging.DEBUG


def test_level_override_reaches_application_loggers_but_not_libraries(tmp_path, restore_logging) -> None:
    """LOG_LEVEL=DEBUG вмикає тривалість звернень до сховищ (журнал db, у файлі
    закріплений на INFO), а журнали драйверів лишаються на WARNING."""
    LoggingConfigurator(tmp_path, process="ingest-worker", config_path=CONFIG).configure("DEBUG")
    assert logging.getLogger("db.mongo").isEnabledFor(logging.DEBUG)
    assert logging.getLogger("crawler").isEnabledFor(logging.DEBUG)
    assert not logging.getLogger("pymongo").isEnabledFor(logging.INFO)
    assert not logging.getLogger("neo4j.io").isEnabledFor(logging.INFO)
    LoggingConfigurator(tmp_path, process="ingest-worker", config_path=CONFIG).configure()
    assert not logging.getLogger("db.mongo").isEnabledFor(logging.DEBUG)


def test_scaled_processes_write_own_files(tmp_path, restore_logging) -> None:
    settings = Settings(log_dir=str(tmp_path), log_config=str(tmp_path / "none.yaml"), hostname="c0ffee")
    assert Container(settings).configure_logging("ingest").name == "ingest-worker-c0ffee.log"
    assert Container(settings).configure_logging("csv").name == "csv-loader.log"


# --- API на заглушках вхідних портів ---


class _Search:
    async def handle(self, query):
        return SearchResultDTO(total=0, normalized_query=query.raw_text)


class _Missing:
    async def handle(self, *args):
        raise EntityNotFound("adventure", "x")


class _Rate:
    async def handle(self, setting):
        setting.validate()
        return setting

    async def current(self, source_name="generator"):
        return RateSettingDTO(100)


class _Live:
    async def handle(self):
        return LiveStatsDTO(generated_at=datetime.now(timezone.utc))


@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.presentation.rest import RouteRegistry

    registry = RouteRegistry()
    missing = _Missing()
    registry.bind(SimpleNamespace(
        search_adventures=_Search(), search_monsters=_Search(), search_encounters=_Search(),
        adventure_card=missing, monster_card=missing, character_card=missing, author_card=missing,
        recommendations=missing, play_statistics=missing, live_stats=_Live(), configure_rate=_Rate(),
    ))
    app = FastAPI()
    registry.register(app, data_dir=PROJECT / "app" / "data")
    return TestClient(app, raise_server_exceptions=False)


def test_routes_of_table_4_15(client) -> None:
    paths = {route.path for route in client.app.routes}
    for path in ("/api/search/{kind}", "/api/adventures/{adventure_id}", "/api/monsters/{monster_id}",
                 "/api/characters/{character_id}", "/api/authors/{author_id}", "/api/adventures/{adventure_id}/similar",
                 "/api/adventures/{adventure_id}/chain", "/api/statistics/{report}", "/api/stats/stream", "/api/rate", "/"):
        assert path in paths, path


def test_search_and_error_translation(client) -> None:
    assert client.get("/api/search/adventures", params={"q": "cursed village", "level": 3}).json()["total"] == 0
    assert client.get("/api/search/planets").status_code == 422
    assert client.get("/api/search/adventures", params={"level": 99}).status_code == 422
    assert client.get("/api/adventures/x").status_code == 404
    assert client.post("/api/rate", json={"documents_per_second": 9000}).status_code == 422
    assert client.post("/api/rate", json={"documents_per_second": 2000}).json()["documents_per_second"] == 2000
    assert client.get("/api/rate").json()["documents_per_second"] == 100


def test_static_page_and_manual_lists(client) -> None:
    page = client.get("/")
    assert page.status_code == 200 and "Моніторинг" in page.text and "chart.umd.min.js" in page.text
    assert client.get("/static/chart.umd.min.js").status_code == 200
    assert len(client.get("/static/data/settings.json").json()) == 13


def test_expected_errors_do_not_reach_the_server(client, caplog) -> None:
    """404 і 422 сценаріїв — відповіді, а не збої: виняток не доходить до
    сервера й не пишеться в журнал рівня ERROR (таблиця 4.17)."""
    from fastapi.testclient import TestClient

    strict = TestClient(client.app)  # виняток, що дійшов до сервера, піднявся б тут
    with caplog.at_level(logging.INFO):
        assert strict.get("/api/adventures/x").status_code == 404
        assert strict.post("/api/rate", json={"documents_per_second": 1}).status_code == 422
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_error_translator_maps_domain_errors() -> None:
    from app.presentation.rest import ErrorTranslator

    assert ErrorTranslator().to_response(ValidationError("rate", ["поза діапазоном"]))[0] == 422
    assert ErrorTranslator().to_response(EntityNotFound("monster", "m"))[0] == 404
    assert ErrorTranslator().to_response(RuntimeError("x"))[0] == 500
