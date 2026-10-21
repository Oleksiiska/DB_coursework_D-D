"""Службова сутність: журнал пошукових запитів (додаток А, таблиця А.12).

Живить панель моніторингу: останні запити, статус, влучання в кеш.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.domain.values import DocumentKind, QueryStatus


@dataclass(slots=True)
class SearchLogEntry:
    query_id: str
    raw_query: str
    normalized_query: str
    created_at: datetime
    kind: DocumentKind = DocumentKind.ADVENTURE
    filters: dict[str, object] = field(default_factory=dict)
    results_count: int = 0
    status: QueryStatus = QueryStatus.EMPTY
    latency_ms: int = 0
    cache_hit: bool = False

    @classmethod
    def from_result(
        cls,
        query_id: str,
        raw_query: str,
        normalized_query: str,
        kind: DocumentKind,
        filters: dict[str, object],
        results_count: int,
        latency_ms: int,
        cache_hit: bool,
        created_at: datetime,
    ) -> "SearchLogEntry":
        """Фабрика з результату виконання запиту: статус FOUND або EMPTY
        визначається кількістю знайдених документів (таблиця А.12)."""
        return cls(
            query_id=query_id,
            raw_query=raw_query,
            normalized_query=normalized_query,
            created_at=created_at,
            kind=kind,
            filters=dict(filters),
            results_count=results_count,
            status=QueryStatus.FOUND if results_count > 0 else QueryStatus.EMPTY,
            latency_ms=latency_ms,
            cache_hit=cache_hit,
        )
