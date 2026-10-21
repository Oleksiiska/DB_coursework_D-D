"""Пошуковий рушій: збіг основ доменної служби й аналізатора english
(через API _analyze), пошук із фільтрами, підсвічуванням і кількостями
за тегами (п. 4.6–4.7).

    SEARCH_URL=http://localhost:9200 python -m pytest tests/integration/test_search_index.py
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest

from app.domain.services import QueryNormalizer
from app.domain.values import AdventureFilters, Page, SortOrder
from app.infrastructure.persistence import SearchIndexAdapter

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif("SEARCH_URL" not in os.environ, reason="потребує SEARCH_URL"),
]

DATA = Path(__file__).resolve().parents[2] / "app" / "data"


@pytest.fixture
async def client():
    from elasticsearch import AsyncElasticsearch

    es = AsyncElasticsearch(os.environ["SEARCH_URL"])
    yield es
    await es.close()


def corpus() -> list[str]:
    """Словник корпусу: описи ігрових світів, типові формулювання пригод і
    запити з розділовими знаками всередині слів."""
    texts = [s["description"] for s in json.loads((DATA / "settings.json").read_text(encoding="utf-8"))]
    texts += [
        "Cursed villages: the CULT of swamp!", "The dragon's lair hides ancient treasures and traps.",
        "Investigating the murders, the heroes questioned witnesses in the flooded catacombs.",
        "D&D 3.5e one-shot for 1,000 gp: U.S.A. heroes' re:zero rock'n'roll kobold_king v2.0 (1.5 hours)",
        "Tolkien’s hobbits don’t visit Mörk Borg; naïve résumé café — Æsir saga",
    ]
    return texts


async def test_normalizer_stems_match_the_english_analyzer(client) -> None:
    normalizer = QueryNormalizer()
    for text in corpus():
        response = await client.indices.analyze(analyzer="english", text=text)
        engine = sorted({token["token"] for token in response["tokens"]})
        ours = sorted(set(normalizer.normalize(text).split()))
        assert ours == engine, text


async def test_search_highlights_and_counts_tags(client) -> None:
    name = f"test_{uuid.uuid4().hex[:8]}_adventures_idx"
    index = SearchIndexAdapter(client, name)
    try:
        await index.ensure_index()
        await index.index_many([
            {"id": "a1", "title": "The Cursed Village", "synopsis": "A cult haunts the swamp village.",
             "tags": ["horror", "one-shot"], "level_min": 1, "level_max": 3, "published_year": 2024, "is_free": True},
            {"id": "a2", "title": "Halls of the Mountain King", "synopsis": "A dungeon crawl.",
             "tags": ["dungeon"], "level_min": 5, "level_max": 8, "published_year": 2020, "is_free": False},
        ])
        await client.indices.refresh(index=name)
        hits = await index.search("cult cursed swamp villages", AdventureFilters(), Page(), SortOrder.RELEVANCE)
        assert hits.ids[0] == "a1" and hits.tag_counts["horror"] == 1
        assert any(s.highlights for s in hits.snippets["a1"])
        filtered = await index.search("", AdventureFilters(level=6), Page(), SortOrder.RELEVANCE)
        assert filtered.ids == ("a2",)
        everything = await index.search("", AdventureFilters(is_free=False, max_duration_hours=None), Page(), SortOrder.YEAR)
        assert everything.ids == ("a2",)
        mapping = await client.indices.get_mapping(index=name)
        assert mapping[name]["mappings"]["properties"]["tags"]["type"] == "keyword"
    finally:
        await client.options(ignore_status=404).indices.delete(index=name)
