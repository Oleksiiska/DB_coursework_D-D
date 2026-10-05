"""Журналювання з рівнями та журнали якості даних."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path


class _JsonFormatter(logging.Formatter):
    """Один запис — один рядок JSON: зручно для збирачів журналів."""

    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "time": self.formatTime(record),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            },
            ensure_ascii=False,
        )


class LoggingConfigurator:
    """Журнали процесу — у стандартний вивід і у файл.

    Стандартний вивід читає `docker compose logs`; файл пишеться в
    каталог, який у контейнері змонтовано з хоста, тож журнал лишається
    на комп'ютері після видалення контейнера. Рівень задається змінною
    середовища без зміни коду.
    """

    LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
    FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

    def __init__(self, log_dir: str | Path | None = None, process: str = "app") -> None:
        self._log_dir = Path(log_dir) if log_dir else None
        self._process = process

    def configure(self, level: str = "INFO", json_format: bool = False) -> Path | None:
        """Налаштовує кореневий журнал; повертає шлях до файлу журналу."""
        level = level.upper()
        if level not in self.LEVELS:
            raise ValueError(f"невідомий рівень журналювання: {level}")
        formatter = _JsonFormatter() if json_format else logging.Formatter(self.FORMAT)
        root = logging.getLogger()
        for handler in list(root.handlers):
            root.removeHandler(handler)
            handler.close()
        root.setLevel(level)

        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(formatter)
        root.addHandler(console)

        path = None
        if self._log_dir is not None:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            path = self._log_dir / f"{self._process}.log"
            to_file = logging.FileHandler(path, encoding="utf-8")
            to_file.setFormatter(formatter)
            root.addHandler(to_file)

        # службові повідомлення драйвера сховища — лише попередження
        logging.getLogger("pymongo").setLevel(max(logging.WARNING, root.level))
        return path

    def get_logger(self, name: str) -> logging.Logger:
        return logging.getLogger(name)


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
