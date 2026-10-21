"""Графове сховище: ідемпотентне злиття вузлів і ребер, рекомендації,
монстри, що трапляються разом, і ланцюжок кампанії (групи 5, 6, 8).

    GRAPH_URI=bolt://localhost:7687 python -m pytest tests/integration/test_graph.py
"""

from __future__ import annotations

import os
import uuid

import pytest

from app.infrastructure.persistence import GraphAdapter

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif("GRAPH_URI" not in os.environ, reason="потребує GRAPH_URI"),
]


@pytest.fixture
async def graph():
    from neo4j import AsyncGraphDatabase

    driver = AsyncGraphDatabase.driver(os.environ["GRAPH_URI"], auth=None)
    adapter = GraphAdapter(driver)
    await adapter.ensure_schema()
    prefix = uuid.uuid4().hex[:8]
    yield adapter, prefix
    await driver.execute_query(
        "MATCH (n) WHERE any(k IN ['adventure_id','author_id','monster_id','setting_id','encounter_id','name'] "
        "WHERE n[k] STARTS WITH $p) DETACH DELETE n", p=prefix)
    await driver.close()


def adventure(prefix, n, level_min, level_max, monsters, tags, prev=None, nxt=None, authors=("a1",)):
    return {"kind": "adventure", "id": f"{prefix}-adv{n}", "title": f"Adventure {n}", "setting_id": f"{prefix}-world",
            "level_min": level_min, "level_max": level_max, "author_ids": [f"{prefix}-{a}" for a in authors],
            "tags": [f"{prefix}-{t}" for t in tags], "monster_ids": [f"{prefix}-{m}" for m in monsters],
            "prev_adventure_id": f"{prefix}-adv{prev}" if prev else None,
            "next_adventure_id": f"{prefix}-adv{nxt}" if nxt else None}


async def test_graph_queries(graph) -> None:
    adapter, p = graph
    batch = [
        adventure(p, 1, 1, 3, ["skeleton", "zombie"], ["horror"], nxt=2, authors=("a1", "a2")),
        adventure(p, 2, 3, 6, ["ghoul"], ["horror"], prev=1, nxt=3),
        adventure(p, 3, 7, 10, ["wight"], ["horror"], prev=2),
        adventure(p, 4, 2, 4, ["skeleton", "zombie"], ["horror", "gothic"]),
        adventure(p, 5, 2, 4, ["owlbear"], ["forest"]),
    ]
    await adapter.merge_nodes_and_edges(batch)
    await adapter.merge_nodes_and_edges(batch)  # повторна доставка не дублює ребер
    await adapter.merge_nodes_and_edges([
        {"kind": "participation", "encounter_id": f"{p}-e{i}", "monster_id": f"{p}-{m}", "instance_label": label}
        for i, pair in enumerate([("skeleton", "zombie"), ("skeleton", "zombie"), ("skeleton", "ghoul")])
        for m, label in zip(pair, ("A1", "B1"))
    ])
    assert (await adapter.similar_adventures(f"{p}-adv1", 2))[0] == f"{p}-adv4"
    assert await adapter.seen_together(f"{p}-skeleton") == [(f"{p}-zombie", 2), (f"{p}-ghoul", 1)]
    assert await adapter.co_authors(f"{p}-a1") == [f"{p}-a2"]
    assert sorted(a for a, _ in await adapter.adventures_with_monster(f"{p}-skeleton")) == [f"{p}-adv1", f"{p}-adv4"]
    assert await adapter.campaign_chain(f"{p}-world", 1, 10, through=f"{p}-adv2") == [f"{p}-adv1", f"{p}-adv2", f"{p}-adv3"]
    assert await adapter.campaign_chain(f"{p}-world", 1, 10) == [f"{p}-adv1", f"{p}-adv2", f"{p}-adv3"]
