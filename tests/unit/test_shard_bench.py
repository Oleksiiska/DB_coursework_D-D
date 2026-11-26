"""Замір запису на один і кілька шардів (п. 6.4) — без сховищ: розміщення
колекції заміру, порядок сценаріїв, зведення результатів і запуск у кластері."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from bson.max_key import MaxKey
from bson.min_key import MinKey

from app.domain.services import ShardKeyService
from tools.shard_bench import report
from tools.shard_bench.__main__ import parse_args
from tools.shard_bench.cluster import ZoneLayout
from tools.shard_bench.workload import RunResult, copy_of, plan_ids

PROJECT = Path(__file__).resolve().parents[2]


class _Admin:
    def __init__(self) -> None:
        self.commands: list[dict] = []

    async def command(self, command):
        self.commands.append(command if isinstance(command, dict) else {command: 1})
        return {"ok": 1, "shards": [{"_id": "shard2"}, {"_id": "shard1"}, {"_id": "shard3"}], "version": "8.0.4"}


class _Cursor:
    async def to_list(self):
        return []


class _Collection:
    def find(self, query):
        return _Cursor()


class _Database:
    def __init__(self) -> None:
        self.dropped: list[str] = []
        self.tags = _Collection()

    async def drop_collection(self, name: str) -> None:
        self.dropped.append(name)


class _Client(dict):
    def __init__(self) -> None:
        super().__init__()
        self.admin = _Admin()
        self["config"] = _Database()
        self["dnd_bench"] = _Database()


@pytest.mark.parametrize("count", [1, 2, 3])
async def test_collection_is_spread_over_the_first_n_shards(count: int) -> None:
    """N зон за ShardKeyService(N), кожна — на своєму шарді; зони заміру не
    чіпають зон колекції пригод системи; зони задано до shardCollection."""
    client = _Client()
    layout = ZoneLayout(client, "dnd_bench")
    shards = await layout.shards()
    assert shards == ["shard1", "shard2", "shard3"]
    ranges = await layout.prepare(f"adventures_s{count}", shards[:count])
    commands = client.admin.commands[1:]
    assert [c.get("addShardToZone") for c in commands if "addShardToZone" in c] == shards[:count]
    assert all(c["zone"].startswith("bench-") for c in commands if "zone" in c and c["zone"])
    updates = [c for c in commands if "updateZoneKeyRange" in c]
    expected = ShardKeyService(count).zone_bounds()
    assert [(r["lower"], r["upper"]) for r in ranges] == expected and len(updates) == count
    assert updates[0]["min"] == {"shard_key": MinKey(), "_id": MinKey()}
    assert updates[-1]["max"] == {"shard_key": MaxKey(), "_id": MaxKey()}
    if count > 1:
        assert updates[0]["max"] == {"shard_key": expected[0][1], "_id": MinKey()}
    assert commands[-1] == {"shardCollection": f"dnd_bench.adventures_s{count}", "key": {"shard_key": 1, "_id": 1}}
    assert client["dnd_bench"].dropped == [f"adventures_s{count}"]


def test_every_scenario_holds_every_position_once() -> None:
    orders = report.rotations([1, 2, 3], 3)
    assert orders == [[1, 2, 3], [2, 3, 1], [3, 1, 2]]
    for position in range(3):
        assert sorted(order[position] for order in orders) == [1, 2, 3]
    assert report.rotations([], 3) == []


def test_document_keys_are_reproducible() -> None:
    first, second = plan_ids(100, 7), plan_ids(100, 7)
    assert first == second and len({i for i, _ in first}) == 100
    keys = ShardKeyService()
    assert all(shard_key == keys.key_value(i) for i, shard_key in first)
    copy = copy_of({"_id": "old", "adventure_id": "old", "title": "T", "shard_key": "0000"}, *first[0])
    assert copy["_id"] == copy["adventure_id"] == first[0][0] and copy["shard_key"] == first[0][1]
    assert copy["title"] == "T"


def _run(shards: int, writers: int, seconds: float, repeat: int = 1) -> RunResult:
    per_shard = {f"shard{i + 1}": 1000 // shards for i in range(shards)}
    return RunResult(shards=shards, writers=writers, repeat=repeat, documents=1000, seconds=seconds,
                     batch_ms=[100.0, 200.0, 300.0, 400.0], per_shard=per_shard, client_cpu_seconds=seconds / 10)


def test_summary_compares_with_one_shard_at_the_same_writers() -> None:
    results = [_run(1, 4, 1.0), _run(1, 4, 1.2, 2), _run(1, 4, 0.8, 3), _run(3, 4, 0.5), _run(3, 4, 0.4, 2),
               _run(3, 4, 0.6, 3), _run(1, 1, 2.0), _run(3, 1, 1.0)]
    rows = {(r["shards"], r["writers"]): r for r in report.summarize(results)}
    assert rows[(1, 4)]["docs_per_second"] == 1000 and rows[(1, 4)]["speedup"] == 1
    assert rows[(3, 4)]["docs_per_second"] == 2000 and rows[(3, 4)]["speedup"] == 2
    assert rows[(3, 4)]["efficiency"] == pytest.approx(2 / 3)
    assert rows[(3, 1)]["speedup"] == 2 and rows[(1, 1)]["runs"] == 1
    assert rows[(3, 4)]["shares"] == pytest.approx({"shard1": 1 / 3, "shard2": 1 / 3, "shard3": 1 / 3})
    assert rows[(1, 4)]["batch_p50_ms"] == 200 and rows[(1, 4)]["batch_p95_ms"] == 400
    assert rows[(1, 4)]["client_cpu_share"] == pytest.approx(0.1)
    environment = {"finished_at": "24.11.2026 21:00", "duration_min": 3.0, "server_version": "8.0.4",
                   "cluster_shards": 3, "shard_ids": ["shard1", "shard2", "shard3"], "write_concern": "w: majority",
                   "documents": 1000, "batch_size": 500, "writers": [1, 4], "repeats": 3,
                   "pool_source": "генератор системи", "pool_size": 200, "avg_document_bytes": 3200.0, "cpu_count": 8}
    text = report.render_markdown(environment, report.summarize(results), results)
    assert "| 3 | 4 | 2 000 |" in text and "2,00×" in text and "w: majority" in text


def test_percentile_is_nearest_rank() -> None:
    assert report.percentile([5, 1, 3, 2, 4], 0.5) == 3
    assert report.percentile(range(1, 101), 0.95) == 95
    assert report.percentile([], 0.5) is None


def test_defaults_match_the_write_path_of_the_system() -> None:
    args = parse_args([])
    assert (args.shards, args.writers, args.batch, args.repeats) == ([1, 2, 3], [1, 4], 500, 3)
    assert parse_args(["--shards", "1,3", "--writers", "2"]).shards == [1, 3]
    with pytest.raises(SystemExit):
        parse_args(["--shards", "0"])


def test_bench_joins_the_cluster_without_entering_the_image() -> None:
    """Таблиця 4.6: tools/ монтується лише для читання; той самий образ і та
    сама мережа, що й у кластера; кластер піднімається до заміру."""
    bench = yaml.safe_load((PROJECT / "docker-compose.bench.yml").read_text(encoding="utf-8"))["services"]["shard-bench"]
    cluster = yaml.safe_load((PROJECT / "docker-compose.cluster.yml").read_text(encoding="utf-8"))["services"]
    assert bench["image"] == cluster["api"]["image"] and bench["networks"] == ["backend"]
    assert bench["entrypoint"] == ["python", "-m", "tools.shard_bench"]
    assert "./tools:/app/tools:ro" in bench["volumes"] and "./logs:/app/logs" in bench["volumes"]
    assert bench["environment"]["MONGO_URI"] == "mongodb://mongos:27017"
    assert set(bench["depends_on"]) == {"mongo-init", "mongos"}
    for suffix in ("ps1", "sh"):
        assert (PROJECT / "scripts" / f"shard-bench.{suffix}").is_file()
