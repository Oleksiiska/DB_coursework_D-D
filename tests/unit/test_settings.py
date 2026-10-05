"""Конфігурація зі змінних середовища: той самий образ працює локально й у Compose."""

from __future__ import annotations

from app.infrastructure.config import Settings


def test_from_env_reads_upper_case_names() -> None:
    settings = Settings.from_env(
        {"MONGO_URI": "mongodb://mongo:27017", "MONGO_TIMEOUT_MS": "5000", "LOG_DIR": "/app/logs"}
    )
    assert settings.mongo_uri == "mongodb://mongo:27017"
    assert settings.mongo_timeout_ms == 5000
    assert settings.log_dir == "/app/logs"


def test_from_env_keeps_defaults_for_missing_values() -> None:
    assert Settings.from_env({}) == Settings()


def test_redacted_uri_hides_password_only() -> None:
    settings = Settings(mongo_uri="mongodb://app:s3cret@mongo:27017/dnd?authSource=admin")
    assert settings.redacted_mongo_uri() == "mongodb://app:***@mongo:27017/dnd?authSource=admin"


def test_redacted_uri_without_credentials_is_unchanged() -> None:
    for uri in ("mongodb://mongo:27017", "mongodb://h1:27017,h2:27017/dnd?appName=a@b"):
        assert Settings(mongo_uri=uri).redacted_mongo_uri() == uri
