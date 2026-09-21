"""Службова сутність: журнал пошукових запитів.

Живить панель моніторингу: останні запити, статус, влучання в кеш.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.domain.values import Filters, QueryStatus


@dataclass(slots=True)
class SearchLogEntry:
    query_id: str
    raw_query: str
    normalized_query: str
    created_at: datetime
    filters: Filters = field(default_factory=Filters)
    results_count: int = 0
    status: QueryStatus = QueryStatus.EMPTY
    latency_ms: int = 0
    cache_hit: bool = False

    @classmethod
    def from_result(cls, *args, **kwargs) -> "SearchLogEntry":
        """Фабрика з результату виконання запиту."""
        raise NotImplementedError
