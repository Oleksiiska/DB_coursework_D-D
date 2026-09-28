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
    def from_result(cls, *args, **kwargs) -> "SearchLogEntry":
        """Фабрика з результату виконання запиту."""
        raise NotImplementedError
