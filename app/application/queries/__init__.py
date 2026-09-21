"""Обробники запитів — шлях читання CQRS."""

from app.application.queries.get_cards import (
    GetAdventureCardHandler,
    GetMonsterStatsHandler,
    GetRecommendationsHandler,
)
from app.application.queries.get_live_stats import GetLiveStatsHandler
from app.application.queries.search_adventures import SearchAdventuresHandler

__all__ = [
    "GetAdventureCardHandler",
    "GetLiveStatsHandler",
    "GetMonsterStatsHandler",
    "GetRecommendationsHandler",
    "SearchAdventuresHandler",
]
