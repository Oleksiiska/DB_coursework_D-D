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
import contextlib
import logging
import signal
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
    settings = container.settings
    flow = {"queue_limit": settings.queue_limit, "pause_seconds": settings.queue_pause_ms / 1000}
    if name == "storage-check":
        await container.connect_documents()
        return StorageCheck(container.repository(DocumentKind.ADVENTURE), container.shard_keys())
    if name == "csv":
        return CsvLoader(container.file_sources(), container.queue(), container.metrics(), **flow)
    if name == "generator":
        return GeneratorWorker(container.generator_source(), container.queue(), container.cache(), container.metrics(),
                               default_rate=settings.default_rate, **flow)
    if name == "crawler":
        return CrawlerWorker(container.crawler_source(), container.queue(), container.metrics(), **flow)
    if name == "ingest":
        adapters = await container.build_adapters()
        await container.ensure_storage(adapters)
        return IngestWorker(adapters.consumer, container.ingest_handler(adapters), batch_size=settings.batch_size)
    raise ValueError(f"невідомий процес {name}")


async def run(name: str, settings: Settings | None = None) -> int:
    """Складання, запуск і коректна зупинка процесу; повертає код завершення.

    SIGTERM від `docker compose stop` скасовує роботу процесу, і з'єднання
    зі сховищами закриваються штатно.
    """
    settings = settings or Settings.from_env()
    container = Container(settings)
    log_path = container.configure_logging(name)
    log.info("процес %s, сховище %s, журнал %s", name, settings.redacted_mongo_uri(), log_path)
    task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(signum, task.cancel)
    try:
        worker = await build(name, container)
        result = await worker.run()
    except asyncio.CancelledError:
        log.info("процес %s зупинено", name)
        return 0
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
