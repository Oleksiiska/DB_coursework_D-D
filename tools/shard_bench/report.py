"""Зведення запусків і звіт заміру (Markdown і JSON)."""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from typing import Any, Iterable

from tools.shard_bench.workload import RunResult


def rotations(scenarios: list[Any], repeats: int) -> list[list[Any]]:
    """Порядок сценаріїв у кожному повторі — циклічний зсув (латинський
    квадрат): кожен сценарій однаково часто стоїть першим і останнім, тож
    прогрівання кешів і дрейф машини не дають переваги одному з них."""
    count = len(scenarios)
    return [scenarios[r % count:] + scenarios[:r % count] for r in range(repeats)] if count else []


def percentile(values: Iterable[float], share: float) -> float | None:
    """Процентиль найближчого рангу."""
    ordered = sorted(values)
    if not ordered:
        return None
    return ordered[max(0, math.ceil(share * len(ordered)) - 1)]


def summarize(results: list[RunResult]) -> list[dict]:
    """Підсумок за кожною парою (шарди, записувачі): медіана швидкості серед
    повторів, розкид, процентилі часу пакета за всіма повторами, прискорення
    відносно одного шарда за тієї самої кількості записувачів."""
    grouped: dict[tuple[int, int], list[RunResult]] = defaultdict(list)
    for result in results:
        grouped[(result.shards, result.writers)].append(result)
    rows = []
    for (shards, writers), runs in sorted(grouped.items(), key=lambda item: (item[0][1], item[0][0])):
        speeds = [r.throughput for r in runs]
        latencies = [ms for r in runs for ms in r.batch_ms]
        documents = sum(r.documents for r in runs)
        shares: dict[str, float] = defaultdict(float)
        for run in runs:
            total = sum(run.per_shard.values()) or 1
            for shard, count in run.per_shard.items():
                shares[shard] += count / total / len(runs)
        rows.append({
            "shards": shards,
            "writers": writers,
            "runs": len(runs),
            "docs_per_second": statistics.median(speeds),
            "min": min(speeds),
            "max": max(speeds),
            "batch_p50_ms": percentile(latencies, 0.50),
            "batch_p95_ms": percentile(latencies, 0.95),
            "batch_p99_ms": percentile(latencies, 0.99),
            "shares": dict(sorted(shares.items())),
            "client_cpu_share": sum(r.client_cpu_seconds for r in runs) / max(1e-9, sum(r.seconds for r in runs)),
            "documents": documents,
        })
    baseline = {row["writers"]: row["docs_per_second"] for row in rows if row["shards"] == 1}
    for row in rows:
        base = baseline.get(row["writers"])
        row["speedup"] = row["docs_per_second"] / base if base else None
        row["efficiency"] = row["speedup"] / row["shards"] if row["speedup"] else None
    return rows


def _num(value: float | None, digits: int = 0) -> str:
    if value is None:
        return "—"
    text = f"{value:,.{digits}f}".replace(",", " ").replace(".", ",")
    return text


def render_markdown(environment: dict, summary: list[dict], results: list[RunResult]) -> str:
    lines = [
        "# Запис на один і кілька шардів",
        "",
        f"Замір {environment['finished_at']} (тривалість {environment['duration_min']:.1f} хв). "
        "Колекція з ключем { shard_key: 1, _id: 1 }, вторинними індексами колекції пригод і шляхом "
        "запису MongoAdventureRepository: невпорядкований bulkWrite із заміною зі вставкою.",
        "",
        "## Умови",
        "",
        "| Параметр | Значення |",
        "|---|---|",
        f"| Кластер | MongoDB {environment['server_version']}, шардів: {environment['cluster_shards']} "
        f"({', '.join(environment['shard_ids'])}), маршрутизатор mongos |",
        f"| Підтвердження запису | {environment['write_concern']} |",
        f"| Документів у запуску | {_num(environment['documents'])} |",
        f"| Розмір пакета | {environment['batch_size']} |",
        f"| Паралельних записувачів | {', '.join(map(str, environment['writers']))} |",
        f"| Повторів кожного сценарію | {environment['repeats']} (порядок — циклічний зсув) |",
        f"| Документи | {environment['pool_source']}: пул із {_num(environment['pool_size'])}, "
        f"у середньому {_num(environment['avg_document_bytes'] / 1024, 1)} КБ BSON |",
        f"| Ядер процесора, видимих заміру | {environment['cpu_count']} |",
        "",
        "## Результати",
        "",
        "| Шардів | Записувачів | док/с, медіана | мін – макс | Прискорення | Ефективність | "
        "Пакет p50, мс | p95, мс | p99, мс | Частки шардів | CPU клієнта |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in summary:
        shares = ", ".join(f"{shard} {share * 100:.1f} %" for shard, share in row["shares"].items())
        lines.append(
            f"| {row['shards']} | {row['writers']} | {_num(row['docs_per_second'])} | "
            f"{_num(row['min'])} – {_num(row['max'])} | "
            f"{'—' if row['speedup'] is None else _num(row['speedup'], 2) + '×'} | "
            f"{'—' if row['efficiency'] is None else _num(row['efficiency'] * 100) + ' %'} | "
            f"{_num(row['batch_p50_ms'])} | {_num(row['batch_p95_ms'])} | {_num(row['batch_p99_ms'])} | "
            f"{shares} | {_num(row['client_cpu_share'] * 100)} % |")
    lines += ["", "## Швидкість, док/с (медіана)", "", "```"]
    peak = max((row["docs_per_second"] for row in summary), default=1) or 1
    for row in summary:
        bar = "█" * max(1, round(40 * row["docs_per_second"] / peak))
        lines.append(f"{row['shards']} шард. × {row['writers']} зап.  {bar} {_num(row['docs_per_second'])}")
    lines += ["```", "", "## Запуски", "", "| № | Шардів | Записувачів | Повтор | док/с | Тривалість, с | "
              "Документів на шардах | Фрагментів на шардах |", "|---|---|---|---|---|---|---|---|"]
    for number, run in enumerate(results, start=1):
        placed = ", ".join(f"{s} {_num(n)}" for s, n in sorted(run.per_shard.items()))
        chunks = ", ".join(f"{s} {n}" for s, n in sorted(run.chunks.items()))
        lines.append(f"| {number} | {run.shards} | {run.writers} | {run.repeat} | {_num(run.throughput)} | "
                     f"{_num(run.seconds, 1)} | {placed} | {chunks} |")
    lines.append("")
    return "\n".join(lines)


def as_json(environment: dict, summary: list[dict], results: list[RunResult]) -> dict:
    return {
        "environment": environment,
        "summary": summary,
        "runs": [{
            "shards": r.shards, "writers": r.writers, "repeat": r.repeat, "documents": r.documents,
            "seconds": r.seconds, "docs_per_second": r.throughput, "batch_ms": r.batch_ms,
            "per_shard": r.per_shard, "chunks": r.chunks, "client_cpu_seconds": r.client_cpu_seconds,
        } for r in results],
    }
