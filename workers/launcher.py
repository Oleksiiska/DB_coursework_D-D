"""Точка входу окремих процесів.

Складає процес через композиційний корінь і запускає його::

    python -m workers.launcher generator

Самі процеси отримують лише порти: контейнер знає про них, а вони
про контейнер — ні. Тому залежності кожного процесу видно з його
конструктора, а заміна адаптера не зачіпає код процесу.
"""

from __future__ import annotations

from app.infrastructure.config import Container, Settings
from workers.crawler_worker import CrawlerWorker
from workers.csv_loader import CsvLoader
from workers.generator_worker import GeneratorWorker
from workers.ingest_worker import IngestWorker

#: Процеси за назвою в командному рядку
PROCESSES: dict[str, type] = {
    "csv": CsvLoader,
    "generator": GeneratorWorker,
    "crawler": CrawlerWorker,
    "ingest": IngestWorker,
}


def build(name: str, container: Container):
    """Процес із портами, отриманими від композиційного кореня."""
    raise NotImplementedError


async def run(name: str, settings: Settings | None = None) -> None:
    """Складання, запуск і коректна зупинка процесу."""
    raise NotImplementedError


def main(argv: list[str] | None = None) -> None:
    raise NotImplementedError
