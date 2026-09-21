"""Конфігурація журналювання з рівнями.

Рівень задається змінною середовища без зміни коду: вимога
до гнучкого логування.
"""

from __future__ import annotations

import logging


class LoggingConfigurator:
    LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")

    def configure(self, level: str = "INFO", json_format: bool = False) -> None:
        raise NotImplementedError

    def get_logger(self, name: str) -> logging.Logger:
        raise NotImplementedError


class RejectedDocumentLog:
    """Журнал браку: документи, що не пройшли валідацію."""

    def __init__(self, path: str) -> None:
        self._path = path

    async def write(self, document: dict, reasons: list[str]) -> None:
        raise NotImplementedError


class UnknownTermLog:
    """Журнал нерозпізнаних значень словників."""

    def __init__(self, path: str) -> None:
        self._path = path

    async def write(self, vocabulary: str, term: str) -> None:
        raise NotImplementedError
