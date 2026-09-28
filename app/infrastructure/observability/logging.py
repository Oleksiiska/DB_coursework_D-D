"""Журналювання з рівнями та журнали якості даних."""

from __future__ import annotations

import logging
from pathlib import Path


class LoggingConfigurator:
    """Рівень задається змінною середовища без зміни коду."""

    LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")

    def configure(self, level: str = "INFO", json_format: bool = False) -> None:
        raise NotImplementedError

    def get_logger(self, name: str) -> logging.Logger:
        raise NotImplementedError


class FileDataQualityLog:
    """Реалізує порт `DataQualityLog`.

    Два файли у форматі JSON Lines: журнал браку та журнал
    нерозпізнаних значень словників.
    """

    def __init__(self, rejected_path: Path, unknown_terms_path: Path) -> None:
        self._rejected_path = rejected_path
        self._unknown_terms_path = unknown_terms_path

    async def rejected(self, document: dict, reasons: list[str]) -> None:
        raise NotImplementedError

    async def unknown_term(self, vocabulary: str, term: str) -> None:
        raise NotImplementedError
