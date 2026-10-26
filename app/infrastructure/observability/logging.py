"""Журналювання з рівнями та журнали якості даних (п. 4.10)."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import logging.config
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


class LoggingConfigurator:
    """Журнали процесу — у стандартний вивід і у файл з ротацією.

    Конфігурацію задає файл config/logging.yaml, змонтований у контейнер лише
    для читання; композиційний корінь читає його під час старту процесу й
    налаштовує журнали функцією logging.config.dictConfig. За відсутності
    файлу діє вбудована конфігурація рівня INFO з тим самим форматом.
    Змінна середовища LOG_LEVEL, якщо задана, перекриває рівні кореневого
    журналу й усіх журналів застосунку з файлу (db, crawler та інших), але не
    сторонніх бібліотек: LOG_LEVEL=DEBUG вмикає тривалість звернень до
    сховищ, а pymongo, neo4j й інші лишаються на WARNING. Замість {process}
    у назві файлу підставляється ім'я процесу.
    """

    LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
    FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    #: Ротація файлу журналу: 10 МБ і п'ять архівних файлів
    MAX_BYTES = 10 * 1024 * 1024
    BACKUP_COUNT = 5
    #: Сторонні бібліотеки пишуть лише попередження
    QUIET_LOGGERS = ("pymongo", "neo4j", "elastic_transport", "elasticsearch", "httpx", "httpcore", "uvicorn.access")

    def __init__(
        self,
        log_dir: str | Path | None = None,
        process: str = "app",
        config_path: str | Path | None = None,
    ) -> None:
        self._log_dir = Path(log_dir) if log_dir else None
        self._process = process
        self._config_path = Path(config_path) if config_path else None

    def configure(self, level: str | None = None) -> Path | None:
        """Налаштовує журнали; повертає шлях до файлу журналу процесу."""
        if level:
            level = level.upper()
            if level not in self.LEVELS:
                raise ValueError(f"невідомий рівень журналювання: {level}")
        config = self._load()
        path: Path | None = None
        for handler in config.get("handlers", {}).values():
            if "filename" in handler:
                path = self._resolve(str(handler["filename"]))
                handler["filename"] = str(path)
        if level:
            config.setdefault("root", {})["level"] = level
            for name, logger in config.get("loggers", {}).items():
                if not self._is_quiet(name):
                    logger["level"] = level
        logging.config.dictConfig(config)
        return path

    @classmethod
    def _is_quiet(cls, name: str) -> bool:
        """Журнал сторонньої бібліотеки або його дочірній журнал."""
        return any(name == quiet or name.startswith(quiet + ".") for quiet in cls.QUIET_LOGGERS)

    def get_logger(self, name: str) -> logging.Logger:
        return logging.getLogger(name)

    def builtin_config(self) -> dict[str, Any]:
        """Вбудована конфігурація: той самий формат, рівень INFO."""
        handlers: dict[str, Any] = {
            "console": {"class": "logging.StreamHandler", "formatter": "plain", "stream": "ext://sys.stdout"},
        }
        if self._log_dir is not None:
            handlers["file"] = {
                "class": "logging.handlers.RotatingFileHandler",
                "formatter": "plain",
                "filename": str(self._log_dir / "{process}.log"),
                "maxBytes": self.MAX_BYTES,
                "backupCount": self.BACKUP_COUNT,
                "encoding": "utf-8",
            }
        return {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {"plain": {"format": self.FORMAT}},
            "handlers": handlers,
            "loggers": {name: {"level": "WARNING"} for name in self.QUIET_LOGGERS},
            "root": {"level": "INFO", "handlers": list(handlers)},
        }

    def _load(self) -> dict[str, Any]:
        if self._config_path is None or not self._config_path.is_file():
            return self.builtin_config()
        import yaml

        loaded = yaml.safe_load(self._config_path.read_text(encoding="utf-8"))
        return copy.deepcopy(loaded)

    def _resolve(self, filename: str) -> Path:
        """Назва файлу з іменем процесу. Якщо каталог із конфігурації
        недоступний (запуск поза контейнером), файл пишеться в каталог
        журналів із налаштувань."""
        path = Path(filename.replace("{process}", self._process))
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            return path
        except OSError:
            fallback = (self._log_dir or Path("logs")) / path.name
            fallback.parent.mkdir(parents=True, exist_ok=True)
            return fallback


class FileDataQualityLog:
    """Реалізує порт `DataQualityLog`.

    Два файли у форматі JSON Lines — журнал браку та журнал нерозпізнаних
    значень словників; із технічними подіями процесів вони не змішуються.
    Запис виконується в окремому потоці, щоб не блокувати цикл подій.
    """

    def __init__(self, rejected_path: Path, unknown_terms_path: Path) -> None:
        self._rejected_path = Path(rejected_path)
        self._unknown_terms_path = Path(unknown_terms_path)
        self._lock = threading.Lock()

    async def rejected(self, document: dict, reasons: list[str]) -> None:
        await asyncio.to_thread(self._append, self._rejected_path, {"document": document, "reasons": reasons})

    async def unknown_term(self, vocabulary: str, term: str) -> None:
        await asyncio.to_thread(self._append, self._unknown_terms_path, {"vocabulary": vocabulary, "term": term})

    def _append(self, path: Path, record: dict[str, Any]) -> None:
        line = json.dumps({"time": datetime.now(timezone.utc).isoformat(), **record},
                          ensure_ascii=False, default=_json_default)
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)
