"""Точка входу окремих процесів.

Складає процес через композиційний корінь і запускає його::

    python -m workers.launcher storage-check

Самі процеси отримують лише порти: контейнер знає про них, а вони
про контейнер — ні. Тому залежності кожного процесу видно з його
конструктора, а заміна адаптера не зачіпає код процесу.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from app.domain.values import DocumentKind
from app.infrastructure.config import Container, Settings
from workers.crawler_worker import CrawlerWorker
from workers.csv_loader import CsvLoader
from workers.generator_worker import GeneratorWorker
from workers.ingest_worker import IngestWorker
from workers.storage_check import StorageCheck

#: Процеси за назвою в командному рядку
PROCESSES: dict[str, type] = {
    "csv": CsvLoader,
    "generator": GeneratorWorker,
    "crawler": CrawlerWorker,
    "ingest": IngestWorker,
    "storage-check": StorageCheck,
}

log = logging.getLogger("launcher")


async def build(name: str, container: Container):
    """Процес із портами, отриманими від композиційного кореня."""
    if name == "storage-check":
        await container.connect_documents()
        return StorageCheck(container.repository(DocumentKind.ADVENTURE))
    raise NotImplementedError(f"процес {name} ще не реалізовано")


async def run(name: str, settings: Settings | None = None) -> int:
    """Складання, запуск і коректна зупинка процесу; повертає код завершення."""
    settings = settings or Settings.from_env()
    container = Container(settings)
    log_path = container.configure_logging(name)
    log.info("процес %s, сховище %s, журнал %s", name, settings.redacted_mongo_uri(), log_path)
    try:
        worker = await build(name, container)
        result = await worker.run()
    except Exception:
        log.exception("процес %s завершився з помилкою", name)
        return 1
    finally:
        await container.shutdown()
    return 1 if result is False else 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Запуск окремого процесу системи")
    parser.add_argument("process", choices=sorted(PROCESSES))
    args = parser.parse_args(argv)
    sys.exit(asyncio.run(run(args.process)))


if __name__ == "__main__":
    main()
