"""Розрахунок пропускної здатності компонентів за журналами (розділ 6)."""

from __future__ import annotations

from tools.log_rates import render, summarize

LINES = """\
2026-10-21 14:38:32,258 DEBUG   db.neo4j: merge adventure 301 док. — 40 мс
2026-10-21 14:38:32,330 DEBUG   db.es: adventures_idx: проіндексовано 301 — 82 мс
2026-10-21 14:38:32,388 DEBUG   db.mongo: adventures.add_many 301 док. — 164 мс
2026-10-21 14:38:33,030 INFO    ingest: пакет від generated:adventure: отримано 301, збережено 301, відхилено 0 за 1085 мс
2026-10-21 14:38:33,456 DEBUG   db.mongo: adventures.add_many 299 док. — 136 мс
2026-10-21 14:35:28,560 DEBUG   db.redis: get search:v1:adventures:c221d0b8f648b07f4a9025c3e890661f23ca9cb0 — влучання, 0.4 мс
2026-10-21 14:35:28,575 DEBUG   db.mongo: adventures.get_many 20 док. — 5 мс; explain: shard1 5 мс, shard2 4 мс, shard3 3 мс
2026-10-21 14:35:28,580 INFO    search: adventures «cursed villages» — FOUND, 36 результатів, 20 мс, кеш: так
2026-10-21 14:35:57,740 DEBUG   db.es: adventures_idx: 40 мс, запит {"query": {"bool": {}}}
2026-10-21 14:35:57,800 INFO    search: adventures «cult crypt» — FOUND, 3217 результатів, 60 мс, кеш: ні
2026-10-21 14:36:00,000 DEBUG   db.neo4j: MATCH (a:Adventure {adventure_id: $id})-[:HAS_TAG]->(t:Tag) — 12 мс
2026-10-21 15:00:00,000 INFO    crawler: отримано https://itch.io/game-assets/tag-dnd
2026-10-21 15:00:02,500 INFO    crawler: отримано https://itch.io/game-assets/tag-dnd?page=2
2026-10-21 15:00:05,000 INFO    crawler: пропущено за robots.txt: https://itch.io/game/download/1
2026-10-21 15:00:05,000 INFO    crawler: отримано https://itch.io/game-assets/tag-dnd?page=3
сторонній рядок без формату
"""


def test_batched_operations_are_documents_per_second_of_total_time() -> None:
    series, _ = summarize(LINES.splitlines())
    mongo = series[("MongoDB", "запис adventures")]
    assert (mongo.documents, len(mongo.durations_ms)) == (600, 2)
    assert round(mongo.throughput()) == 2000  # 600 док. за 0,3 с
    assert round(series[("Elasticsearch", "індексування adventures_idx")].throughput()) == 3671
    assert round(series[("Neo4j", "запис adventure")].throughput()) == 7525
    assert round(series[("процес запису", "пакет generated:adventure")].throughput()) == 277


def test_single_requests_are_per_connection_rates_with_percentiles() -> None:
    series, _ = summarize(LINES.splitlines())
    assert series[("Redis", "кеш, влучання")].throughput() == 2500
    assert series[("Elasticsearch", "пошук adventures_idx")].throughput() == 25
    assert series[("MongoDB", "читання adventures")].documents == 20
    assert series[("API, пошук", "adventures, кеш: так")].percentile(0.95) == 20
    assert series[("API, пошук", "adventures, кеш: ні")].percentile(0.5) == 60
    assert ("Neo4j", "читання") in series


def test_crawler_rate_and_rendered_table() -> None:
    series, crawl_rate = summarize(LINES.splitlines())
    assert crawl_rate == 0.4  # дві паузи за 5 с; пропуск за robots.txt не рахується
    table = render(series, crawl_rate)
    assert table.splitlines()[0].split()[:2] == ["Компонент", "Операція"]
    assert "0.40 сторінок/с" in table and "2000 док/с" in table
