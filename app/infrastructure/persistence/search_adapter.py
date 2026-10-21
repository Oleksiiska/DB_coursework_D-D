"""Адаптер пошукового рушія (Elasticsearch, п. 4.6, таблиця 4.12)."""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, datetime
from typing import Any, Sequence

from app.domain.values import (
    AdventureFilters,
    EncounterFilters,
    MonsterFilters,
    Page,
    SearchHits,
    SearchSpecification,
    Snippet,
    SortOrder,
)

log = logging.getLogger("db.es")

_TEXT = {"type": "text", "analyzer": "english"}
_KEYWORD = {"type": "keyword"}
_INT = {"type": "integer"}
_FLOAT = {"type": "float"}
_BOOL = {"type": "boolean"}

#: Склад індексів (таблиця 4.12): текстові поля з вагами, поля фільтрів і
#: сортування точних типів, порядок видачі без тексту за замовчуванням
INDEX_DEFINITIONS: dict[str, dict[str, Any]] = {
    "adventures": {
        "text_fields": {"title": 3, "hook": 2, "synopsis": 2, "location_names": 1, "full_text": 1, "reviews.text": 1},
        "properties": {
            "title": _TEXT, "hook": _TEXT, "synopsis": _TEXT, "location_names": _TEXT, "full_text": _TEXT,
            "reviews": {"properties": {"text": _TEXT}},
            "tags": _KEYWORD, "system": _KEYWORD, "edition": _KEYWORD, "setting_id": _KEYWORD,
            "publication_language": _KEYWORD, "status": _KEYWORD, "genre": _KEYWORD, "origin": _KEYWORD,
            "level_min": _INT, "level_max": _INT, "players_min": _INT, "players_max": _INT,
            "duration_hours": _FLOAT, "session_count": _INT, "difficulty": _INT, "published_year": _INT,
            "price": _FLOAT, "rating_avg": _FLOAT, "ratings_count": _INT, "reviews_count": _INT,
            "is_free": _BOOL,
        },
        "default_sort": [{"published_year": {"order": "desc", "missing": "_last"}}],
        "sorts": {
            SortOrder.RATING: [{"rating_avg": {"order": "desc", "missing": "_last"}}],
            SortOrder.REVIEWS: [{"reviews_count": {"order": "desc", "missing": "_last"}}],
            SortOrder.YEAR: [{"published_year": {"order": "desc", "missing": "_last"}}],
            SortOrder.DURATION: [{"duration_hours": {"order": "asc", "missing": "_last"}}],
        },
        "tag_aggregation": True,
    },
    "monsters": {
        "text_fields": {"name": 3, "abilities_text": 1, "lore": 1},
        "properties": {
            "name": _TEXT, "abilities_text": _TEXT, "lore": _TEXT,
            "type": _KEYWORD, "subtype": _KEYWORD, "size": _KEYWORD, "alignment": _KEYWORD, "environment": _KEYWORD,
            "challenge_rating": _FLOAT, "play_count": _INT,
        },
        "default_sort": [{"play_count": {"order": "desc"}}],
        "sorts": {},
        "tag_aggregation": False,
    },
    "encounters": {
        "text_fields": {"narrative_text": 1},
        "properties": {
            "narrative_text": _TEXT, "commands": _KEYWORD, "started_at": {"type": "date"},
            "duration_seconds": _INT, "rounds_count": _INT, "participants_count": _INT, "is_abandoned": _BOOL,
        },
        "default_sort": [{"started_at": {"order": "desc"}}],
        "sorts": {SortOrder.DURATION: [{"duration_seconds": {"order": "asc", "missing": "_last"}}]},
        "tag_aggregation": False,
    },
}

_MARK = re.compile(r"<mark>(.*?)</mark>", re.S)


class SearchIndexAdapter:
    """Реалізує порт `SearchIndex`.

    Один клас — три екземпляри з різними індексами: пригод, монстрів,
    зустрічей. Індекс зберігає лише поля, потрібні для пошуку й видачі;
    повні документи читаються з документного сховища за ключами.
    """

    #: Один первинний шард без реплік: на одному вузлі репліку розмістити ніде (п. 4.6)
    SETTINGS = {"number_of_shards": 1, "number_of_replicas": 0}
    #: Підсвічувач unified: до трьох фрагментів по 150 символів, слова — у тегу mark
    HIGHLIGHT = {"type": "unified", "number_of_fragments": 3, "fragment_size": 150,
                 "pre_tags": ["<mark>"], "post_tags": ["</mark>"]}
    #: Запит з одного-двох слів вимагає всіх слів, довший — трьох чвертей
    MINIMUM_SHOULD_MATCH = "2<75%"
    TAG_BUCKETS = 30

    def __init__(self, client: Any, index_name: str, kind: str | None = None) -> None:
        self._client = client
        self._index_name = index_name
        key = kind or next((k for k in INDEX_DEFINITIONS if k in index_name), "adventures")
        self._definition = INDEX_DEFINITIONS[key]

    async def ensure_index(self) -> None:
        """Створення індексу з відображенням таблиці 4.12, якщо його ще немає."""
        if await self._client.indices.exists(index=self._index_name):
            return
        try:
            await self._client.indices.create(
                index=self._index_name,
                settings=self.SETTINGS,
                mappings={"dynamic": False, "properties": self._definition["properties"]},
            )
            log.info("індекс %s створено", self._index_name)
        except Exception as error:  # noqa: BLE001 — індекс створив інший процес
            if "resource_already_exists_exception" not in str(error):
                raise

    async def search(
        self,
        text: str,
        spec: SearchSpecification,
        page: Page,
        sort: SortOrder = SortOrder.RELEVANCE,
    ) -> SearchHits:
        body = self.build_query(text, spec, page, sort)
        started = time.perf_counter()
        response = await self._client.search(index=self._index_name, **body)
        if log.isEnabledFor(logging.DEBUG):
            log.debug("%s: %.0f мс, запит %s", self._index_name, (time.perf_counter() - started) * 1000,
                      json.dumps(body, ensure_ascii=False, default=str))
        return self._parse(response)

    def build_query(self, text: str, spec: SearchSpecification, page: Page, sort: SortOrder) -> dict[str, Any]:
        """Тіло запиту (п. 4.6): multi_match best_fields за полями з вагами,
        допуск помилок AUTO, minimum_should_match «2<75%», фільтри в контексті
        filter, сортування, сторінка з 20 результатів, агрегація terms за
        тегами й підсвічування. Без тексту — match_all із тими самими
        фільтрами й сортуванням за замовчуванням для виду документа."""
        fields = [f"{name}^{weight}" if weight != 1 else name for name, weight in self._definition["text_fields"].items()]
        if text.strip():
            must: dict[str, Any] = {"multi_match": {
                "query": text, "type": "best_fields", "fields": fields,
                "fuzziness": "AUTO", "minimum_should_match": self.MINIMUM_SHOULD_MATCH,
            }}
        else:
            must = {"match_all": {}}
        body: dict[str, Any] = {
            "query": {"bool": {"must": [must], "filter": self._build_filter_clause(spec)}},
            "from_": page.offset,
            "size": page.size,
            "track_total_hits": True,
            "source": False,
            "highlight": {**self.HIGHLIGHT, "fields": {name: {} for name in self._definition["text_fields"]}},
        }
        sorting = self._definition["sorts"].get(sort)
        if sorting is None and (sort is not SortOrder.RELEVANCE or not text.strip()):
            sorting = self._definition["default_sort"]
        if sorting is not None:
            body["sort"] = sorting + ["_score"]
        if self._definition["tag_aggregation"]:
            body["aggs"] = {"tags": {"terms": {"field": "tags", "size": self.TAG_BUCKETS}}}
        return body

    async def index_many(self, documents: Sequence[dict]) -> int:
        """Пакетна індексація; повторна індексація того самого ключа перезаписує документ."""
        from elasticsearch.helpers import async_bulk

        if not documents:
            return 0
        fields = self._definition["properties"].keys()
        actions = [
            {
                "_op_type": "index",
                "_index": self._index_name,
                "_id": document["id"],
                "_source": {k: _plain(document.get(k)) for k in fields if document.get(k) is not None},
            }
            for document in documents
        ]
        started = time.perf_counter()
        success, _ = await async_bulk(self._client, actions, chunk_size=500, raise_on_error=True)
        log.debug("%s: проіндексовано %d — %.0f мс", self._index_name, success, (time.perf_counter() - started) * 1000)
        return success

    async def delete(self, document_id: str) -> bool:
        response = await self._client.options(ignore_status=404).delete(index=self._index_name, id=document_id)
        return response.get("result") == "deleted"

    def _build_filter_clause(self, spec: SearchSpecification) -> list[dict[str, Any]]:
        """Фільтри груп 2 і 3: без обчислення релевантності, кешуються рушієм.
        Документ без значення поля під фільтр не потрапляє (таблиця 1.5)."""
        clauses: list[dict[str, Any]] = []

        def term(field: str, value: Any) -> None:
            if value is not None:
                clauses.append({"term": {field: value}})

        def between(field: str, low: Any = None, high: Any = None) -> None:
            bounds = {k: v for k, v in (("gte", low), ("lte", high)) if v is not None}
            if bounds:
                clauses.append({"range": {field: bounds}})

        if isinstance(spec, AdventureFilters):
            if spec.level is not None:
                between("level_min", high=spec.level)
                between("level_max", low=spec.level)
            if spec.players is not None:
                between("players_min", high=spec.players)
                between("players_max", low=spec.players)
            term("system", spec.system)
            term("difficulty", spec.difficulty)
            term("setting_id", spec.setting_id)
            term("publication_language", spec.publication_language)
            term("is_free", spec.is_free)
            for tag in spec.tags:
                term("tags", tag)
            between("duration_hours", high=spec.max_duration_hours)
            between("published_year", spec.year_from, spec.year_to)
        elif isinstance(spec, MonsterFilters):
            low, high = spec.challenge_bounds()
            between("challenge_rating", low, high)
            term("type", spec.creature_type)
            term("environment", spec.environment)
        elif isinstance(spec, EncounterFilters):
            term("commands", spec.command)
            between("rounds_count", spec.min_rounds, spec.max_rounds)
            if spec.exclude_abandoned:
                term("is_abandoned", False)
        return clauses

    def _parse(self, response: Any) -> SearchHits:
        hits = response["hits"]["hits"]
        total = response["hits"]["total"]
        buckets = (response.get("aggregations") or {}).get("tags", {}).get("buckets", [])
        return SearchHits(
            ids=tuple(hit["_id"] for hit in hits),
            snippets={hit["_id"]: self._parse_highlights(hit.get("highlight") or {}) for hit in hits},
            total=total["value"] if isinstance(total, dict) else int(total),
            tag_counts={bucket["key"]: bucket["doc_count"] for bucket in buckets},
        )

    def _parse_highlights(self, raw: dict[str, Any]) -> tuple[Snippet, ...]:
        """Перетворення відповіді рушія на доменні фрагменти: текст без
        розмітки й позиції підсвічених слів."""
        snippets = []
        for field_name, fragments in raw.items():
            for fragment in fragments:
                text: list[str] = []
                highlights: list[tuple[int, int]] = []
                position = last = 0
                for match in _MARK.finditer(fragment):
                    text.append(fragment[last:match.start()])
                    position += match.start() - last
                    highlights.append((position, position + len(match.group(1))))
                    text.append(match.group(1))
                    position += len(match.group(1))
                    last = match.end()
                text.append(fragment[last:])
                snippets.append(Snippet(field_name, "".join(text), tuple(highlights)))
        return tuple(snippets)


def _plain(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value
