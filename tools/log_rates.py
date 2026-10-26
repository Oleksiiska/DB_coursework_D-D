"""Пропускна здатність компонентів за журналами процесів (розділ 6).

Рахує за рядками журналів api, ingest-worker і crawler (формат п. 4.10):

* шлях команд — документів за секунду для кожного сховища (DEBUG db.mongo
  add_many, db.es «проіндексовано», db.neo4j merge) і для пакета цілком
  (INFO ingest «пакет від …»);
* шлях запитів — час пошуку з кешем і без нього (INFO search) та час
  звернень до кожного сховища (DEBUG db.redis get, db.es запит, db.mongo get_many);
* краулер — сторінок за секунду за рядками INFO «отримано URL».

Пропускна здатність тут — для одного послідовного виконавця: пакетні
операції — сума документів на сумарний час, поодинокі — 1000 / середній
час у мілісекундах (запитів за секунду через одне з'єднання). Рядки DEBUG
з'являються, якщо процеси запущено з LOG_LEVEL=DEBUG::

    python -m tools.log_rates logs/api.log logs/ingest-worker-*.log logs/crawler.log
"""

from __future__ import annotations

import math
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3}) (\w+)\s+([\w.\-]+): (.*)$")

#: (журнал, шаблон повідомлення, компонент, операція або назва групи шаблону)
PATTERNS: tuple[tuple[str, re.Pattern, str, str], ...] = (
    ("db.mongo", re.compile(r"^(?P<coll>\w+)\.add_many (?P<n>\d+) док\. — (?P<ms>\d+) мс"), "MongoDB", "запис {coll}"),
    ("db.mongo", re.compile(r"^(?P<coll>\w+)\.(?P<op>get_many|get) (?P<n>\d+) док\. — (?P<ms>\d+) мс"),
     "MongoDB", "читання {coll}"),
    ("db.es", re.compile(r"^(?P<index>[\w.\-]+): проіндексовано (?P<n>\d+) — (?P<ms>\d+) мс"),
     "Elasticsearch", "індексування {index}"),
    ("db.es", re.compile(r"^(?P<index>[\w.\-]+): (?P<ms>\d+) мс, запит"), "Elasticsearch", "пошук {index}"),
    ("db.neo4j", re.compile(r"^merge (?P<kind>\w+) (?P<n>\d+) док\. — (?P<ms>\d+) мс"), "Neo4j", "запис {kind}"),
    ("db.neo4j", re.compile(r"^(?!merge )(?P<query>.+) — (?P<ms>\d+) мс$"), "Neo4j", "читання"),
    ("db.redis", re.compile(r"^get \S+ — (?P<result>влучання|промах), (?P<ms>[\d.]+) мс"), "Redis", "кеш, {result}"),
    ("ingest", re.compile(r"^пакет від (?P<source>\S+): отримано \d+, збережено (?P<n>\d+), відхилено \d+ за (?P<ms>\d+) мс"),
     "процес запису", "пакет {source}"),
    ("search", re.compile(r"^(?P<kind>\w+) «.*» — \w+, \d+ \S+, (?P<ms>\d+) мс, кеш: (?P<cache>так|ні)"),
     "API, пошук", "{kind}, кеш: {cache}"),
)
CRAWLER = re.compile(r"^отримано \S+")


@dataclass
class Series:
    """Тривалості звернень однієї операції та кількість документів у них."""

    durations_ms: list[float] = field(default_factory=list)
    documents: int = 0
    batched: bool = False

    def percentile(self, share: float) -> float:
        ordered = sorted(self.durations_ms)
        rank = max(1, math.ceil(share * len(ordered)))  # метод найближчого рангу
        return ordered[rank - 1]

    def throughput(self) -> float:
        seconds = sum(self.durations_ms) / 1000
        if self.batched:
            return self.documents / seconds if seconds else float("inf")
        mean = sum(self.durations_ms) / len(self.durations_ms)
        return 1000 / mean if mean else float("inf")


def summarize(lines: Iterable[str]) -> tuple[dict[tuple[str, str], Series], float | None]:
    """Ряди за (компонент, операція) і темп краулера, сторінок за секунду."""
    series: dict[tuple[str, str], Series] = defaultdict(Series)
    pages: list[datetime] = []
    for line in lines:
        match = LINE.match(line.rstrip("\n"))
        if match is None:
            continue
        stamp, _level, logger, message = match.groups()
        if logger == "crawler" and CRAWLER.match(message):
            pages.append(datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S,%f"))
            continue
        for name, pattern, component, operation in PATTERNS:
            found = pattern.match(message) if logger == name else None
            if found is None:
                continue
            values = found.groupdict()
            item = series[(component, operation.format(**values))]
            item.durations_ms.append(float(values["ms"]))
            if "n" in values:
                item.batched = True
                item.documents += int(values["n"])
            break
    crawl_rate = None
    if len(pages) > 1:
        span = (max(pages) - min(pages)).total_seconds()
        crawl_rate = (len(pages) - 1) / span if span else None
    return dict(series), crawl_rate


def _ms(value: float) -> str:
    """Короткі звернення (кеш) — з десятими частками мілісекунди."""
    return f"{value:.1f}" if value < 10 else f"{value:.0f}"


def render(series: dict[tuple[str, str], Series], crawl_rate: float | None) -> str:
    rows = [("Компонент", "Операція", "Звернень", "Документів", "Медіана, мс", "p95, мс", "Темп одного виконавця")]
    for (component, operation), item in sorted(series.items()):
        unit = "док/с" if item.batched else "запитів/с"
        rows.append((component, operation, str(len(item.durations_ms)), str(item.documents) if item.batched else "—",
                     _ms(item.percentile(0.5)), _ms(item.percentile(0.95)), f"{item.throughput():.0f} {unit}"))
    if crawl_rate is not None:
        rows.append(("краулер", "сторінки itch.io", "—", "—", "—", "—", f"{crawl_rate:.2f} сторінок/с"))
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    return "\n".join("  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip() for row in rows)


def main(paths: list[str]) -> int:
    if not paths:
        print(__doc__.strip().splitlines()[-1].strip(), file=sys.stderr)
        return 2

    def lines() -> Iterable[str]:
        for path in paths:
            with open(path, encoding="utf-8", errors="replace") as handle:
                yield from handle

    series, crawl_rate = summarize(lines())
    print(render(series, crawl_rate))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
