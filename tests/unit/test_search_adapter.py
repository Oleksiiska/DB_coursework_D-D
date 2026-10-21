"""Запит до пошукового рушія й склад індексів (п. 4.6, таблиця 4.12) — без рушія."""

from __future__ import annotations

from app.domain.values import AdventureFilters, EncounterFilters, MonsterFilters, Page, SortOrder
from app.infrastructure.persistence import SearchIndexAdapter
from app.infrastructure.persistence.search_adapter import INDEX_DEFINITIONS


def test_text_query_follows_section_4_6() -> None:
    body = SearchIndexAdapter(None, "adventures_idx").build_query(
        "cult cursed swamp villages", AdventureFilters(level=3, tags=("horror",)), Page(number=2), SortOrder.RELEVANCE)
    match = body["query"]["bool"]["must"][0]["multi_match"]
    assert match["type"] == "best_fields" and match["fuzziness"] == "AUTO" and match["minimum_should_match"] == "2<75%"
    assert match["fields"] == ["title^3", "hook^2", "synopsis^2", "location_names", "full_text", "reviews.text"]
    assert {"range": {"level_min": {"lte": 3}}} in body["query"]["bool"]["filter"]
    assert {"term": {"tags": "horror"}} in body["query"]["bool"]["filter"]
    assert (body["from_"], body["size"]) == (20, 20) and "sort" not in body
    assert body["aggs"] == {"tags": {"terms": {"field": "tags", "size": 30}}}
    highlight = body["highlight"]
    assert (highlight["type"], highlight["number_of_fragments"], highlight["fragment_size"]) == ("unified", 3, 150)
    assert highlight["pre_tags"] == ["<mark>"] and highlight["post_tags"] == ["</mark>"]


def test_filters_without_text_use_match_all_and_default_order() -> None:
    body = SearchIndexAdapter(None, "adventures_idx").build_query("", AdventureFilters(level=3), Page(), SortOrder.RELEVANCE)
    assert body["query"]["bool"]["must"] == [{"match_all": {}}]
    assert body["sort"][0] == {"published_year": {"order": "desc", "missing": "_last"}}


def test_monster_and_encounter_indices() -> None:
    monsters = SearchIndexAdapter(None, "monsters_idx").build_query("breath weapon", MonsterFilters(party_level=4),
                                                                    Page(), SortOrder.RELEVANCE)
    assert monsters["query"]["bool"]["must"][0]["multi_match"]["fields"] == ["name^3", "abilities_text", "lore"]
    assert {"range": {"challenge_rating": {"gte": 1.0, "lte": 6.0}}} in monsters["query"]["bool"]["filter"]
    assert "aggs" not in monsters
    encounters = SearchIndexAdapter(None, "encounters_idx").build_query("ambush", EncounterFilters(), Page(),
                                                                        SortOrder.RELEVANCE)
    assert {"term": {"is_abandoned": False}} in encounters["query"]["bool"]["filter"]


def test_index_field_types_match_table_4_12() -> None:
    adventures = INDEX_DEFINITIONS["adventures"]["properties"]
    assert all(adventures[f] == {"type": "keyword"} for f in
               ("tags", "system", "edition", "setting_id", "publication_language", "status", "genre", "origin"))
    assert all(adventures[f]["type"] in ("integer", "float") for f in
               ("level_min", "level_max", "players_min", "players_max", "duration_hours", "session_count",
                "difficulty", "published_year", "price", "rating_avg", "ratings_count", "reviews_count"))
    assert adventures["is_free"] == {"type": "boolean"} and adventures["title"]["analyzer"] == "english"
    encounters = INDEX_DEFINITIONS["encounters"]["properties"]
    assert encounters["started_at"] == {"type": "date"} and encounters["is_abandoned"] == {"type": "boolean"}
    assert SearchIndexAdapter.SETTINGS == {"number_of_shards": 1, "number_of_replicas": 0}


def test_highlights_become_positions() -> None:
    snippets = SearchIndexAdapter(None, "adventures_idx")._parse_highlights(
        {"synopsis": ["a <mark>cursed</mark> old <mark>village</mark>"]})
    assert snippets[0].text == "a cursed old village" and snippets[0].highlights == ((2, 8), (13, 20))
