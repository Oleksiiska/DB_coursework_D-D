"""Замір швидкості запису на 1–N шардах: ``python -m tools.shard_bench [параметри]``.

Кожен сценарій — кількість шардів N і кількість паралельних записувачів W —
повторюється кілька разів у порядку циклічного зсуву. Кожен запуск пише той
самий набір документів у свіжу колекцію, розподілену між першими N шардами
кластера; час вимірюється лише для запису. Підсумок — медіана швидкості серед
повторів і прискорення відносно одного шарда за тієї самої W.

Коди завершення: 0 — замір виконано; 1 — замір неможливий (не кластер,
немає документів для пулу); 2 — невірні параметри.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from app.infrastructure.config import Container, Settings
from tools.prepare.check import tag_aliases
from tools.shard_bench import report
from tools.shard_bench.cluster import ZoneLayout
from tools.shard_bench.workload import (
    RunResult,
    plan_ids,
    pool_from_collection,
    pool_from_generator,
    repository,
    write,
)

log = logging.getLogger("shard-bench")


def _numbers(text: str) -> list[int]:
    try:
        values = [int(part) for part in text.replace(" ", "").split(",") if part]
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"очікувалися цілі числа через кому: {text!r}") from error
    if not values or min(values) < 1:
        raise argparse.ArgumentTypeError(f"очікувалися додатні числа: {text!r}")
    return values


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m tools.shard_bench", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--shards", type=_numbers, default=[1, 2, 3],
                        help="кількість шардів у сценаріях, через кому (за замовчуванням 1,2,3)")
    parser.add_argument("--writers", type=_numbers, default=[1, 4],
                        help="паралельних записувачів, через кому (1,4): 1 — як один ingest-worker, "
                             "4 — як кілька екземплярів")
    parser.add_argument("--documents", type=int, default=50_000, help="документів у кожному запуску")
    parser.add_argument("--batch", type=int, default=Settings().batch_size, help="розмір пакета (500, як у системі)")
    parser.add_argument("--repeats", type=int, default=3, help="повторів кожного сценарію")
    parser.add_argument("--warmup", type=int, default=10_000, help="документів у прогрівальному запуску (0 — без)")
    parser.add_argument("--pool", type=int, default=2_000, help="різних документів у пулі")
    parser.add_argument("--source", choices=("auto", "live", "generator"), default="auto",
                        help="звідки пул: вибірка згенерованих пригод із колекції системи (live), генератор "
                             "(generator) або live, якщо в колекції достатньо пригод (auto)")
    parser.add_argument("--database", default="dnd_bench", help="окрема база для колекцій заміру")
    parser.add_argument("--seed", type=int, default=20261124, help="зерно ключів документів")
    parser.add_argument("--out", type=Path, default=None, help="каталог звіту (за замовчуванням LOG_DIR/shard-bench)")
    parser.add_argument("--keep", action="store_true", help="не видаляти колекції заміру")
    args = parser.parse_args(argv)
    for name in ("documents", "batch", "repeats", "pool"):
        if getattr(args, name) < 1:
            parser.error(f"--{name}: має бути не менше 1")
    return args


async def load_pool(container: Container, client, settings: Settings, args: argparse.Namespace) -> tuple[list[dict], str]:
    if args.source in ("auto", "live"):
        pool = await pool_from_collection(client, settings.mongo_database, args.pool)
        if len(pool) >= min(args.pool, 100) or args.source == "live":
            return pool, f"вибірка згенерованих пригод із {settings.mongo_database}.adventures"
        log.info("у колекції системи замало згенерованих пригод (%d) — пул дає генератор", len(pool))
    pool = pool_from_generator(container, args.pool, tag_aliases(Path(settings.data_dir)))
    return pool, "генератор системи" + (" (калібрування за CSV)" if Path(settings.csv_dir).is_dir() else "")


async def run_once(layout: ZoneLayout, client, args: argparse.Namespace, shard_ids: list[str], shards: int,
                   writers: int, repeat: int, pool: list[dict], ids: list[tuple[str, str]]) -> RunResult:
    collection = f"adventures_s{shards}"
    ranges = await layout.prepare(collection, shard_ids[:shards])
    target = repository(client, args.database, collection)
    await target.ensure_indexes()
    chunks = await layout.chunks(collection)
    seconds, latencies, cpu = await write(target, pool, ids, args.batch, writers)
    exact = await target.count_in_zones([(r["lower"], r["upper"]) for r in ranges])
    if sum(exact) != len(ids):
        raise RuntimeError(f"{collection}: у колекції {sum(exact)} документів замість {len(ids)}")
    placed = await target.count_by_shard()
    return RunResult(shards=shards, writers=writers, repeat=repeat, documents=len(ids), seconds=seconds,
                     batch_ms=latencies, per_shard=placed or {r["shard"]: n for r, n in zip(ranges, exact)},
                     chunks=chunks, client_cpu_seconds=cpu)


async def run(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    container = Container(settings)
    container.configure_logging("shard-bench")
    await container.connect_documents()
    client = container.documents_database().client
    try:
        layout = ZoneLayout(client, args.database)
        shard_ids = await layout.shards()
        if not shard_ids:
            log.error("%s — не шардований кластер: замір потребує маршрутизатора mongos",
                      settings.redacted_mongo_uri())
            return 1
        scenarios = sorted({n for n in args.shards if n <= len(shard_ids)})
        if skipped := sorted(set(args.shards) - set(scenarios)):
            log.warning("у кластері шардів: %d — сценарії %s пропущено", len(shard_ids), skipped)
        pool, source = await load_pool(container, client, settings, args)
        if not pool:
            log.error("немає документів для пулу: ні в колекції системи, ні від генератора")
            return 1
        from bson import encode

        avg_bytes = sum(len(encode(document)) for document in pool) / len(pool)
        ids = plan_ids(args.documents, args.seed)
        log.info("замір: шарди %s із %s, записувачі %s, %d док. × %d повтори, пакет %d; пул — %s, %d док. "
                 "по %.1f КБ", scenarios, shard_ids, args.writers, args.documents, args.repeats, args.batch, source,
                 len(pool), avg_bytes / 1024)
        started = time.monotonic()
        if args.warmup:
            warm = await run_once(layout, client, args, shard_ids, max(scenarios), max(args.writers), 0, pool,
                                  plan_ids(args.warmup, args.seed + 1))
            log.info("прогрівання: %d док., %.0f док/с (не враховується)", warm.documents, warm.throughput)
        results: list[RunResult] = []
        plan = [(shards, writers) for writers in args.writers for shards in scenarios]
        for repeat, order in enumerate(report.rotations(plan, args.repeats), start=1):
            for shards, writers in order:
                result = await run_once(layout, client, args, shard_ids, shards, writers, repeat, pool, ids)
                results.append(result)
                log.info("%d шард(и) × %d записувач(і), повтор %d: %d док. за %.1f с — %.0f док/с; пакет p50 %.0f мс; %s",
                         shards, writers, repeat, result.documents, result.seconds, result.throughput,
                         report.percentile(result.batch_ms, 0.5) or 0,
                         ", ".join(f"{s} {n}" for s, n in sorted(result.per_shard.items())))
        if not args.keep:
            for shards in scenarios:
                await layout.drop(f"adventures_s{shards}")
            await layout.release(shard_ids)
        summary = report.summarize(results)
        environment = {
            "finished_at": datetime.now(timezone.utc).astimezone().strftime("%d.%m.%Y %H:%M"),
            "duration_min": (time.monotonic() - started) / 60,
            "server_version": await layout.server_version(),
            "cluster_shards": len(shard_ids),
            "shard_ids": shard_ids,
            "write_concern": ", ".join(f"{k}: {v}" for k, v in (client.write_concern.document or {}).items())
                             or "за замовчуванням",
            "documents": args.documents,
            "batch_size": args.batch,
            "writers": args.writers,
            "repeats": args.repeats,
            "pool_source": source,
            "pool_size": len(pool),
            "avg_document_bytes": avg_bytes,
            "cpu_count": os.cpu_count(),
            "seed": args.seed,
            "warmup": args.warmup,
        }
        out = args.out or Path(settings.log_dir) / "shard-bench"
        out.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        markdown = out / f"shard-bench-{stamp}.md"
        markdown.write_text(report.render_markdown(environment, summary, results), encoding="utf-8")
        (out / f"shard-bench-{stamp}.json").write_text(
            json.dumps(report.as_json(environment, summary, results), ensure_ascii=False, indent=2), encoding="utf-8")
        for row in summary:
            log.info("підсумок: %d шард(и) × %d записувач(і) — %.0f док/с (медіана), прискорення %s",
                     row["shards"], row["writers"], row["docs_per_second"],
                     "—" if row["speedup"] is None else f"{row['speedup']:.2f}×")
        log.info("звіт: %s", markdown)
        return 0
    finally:
        await container.shutdown()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
