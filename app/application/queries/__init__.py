"""Обробники запитів — шлях читання CQRS."""

from app.application.queries.get_cards import (
    GetAdventureCardHandler,
    GetAuthorCardHandler,
    GetCharacterCardHandler,
    GetMonsterCardHandler,
)
from app.application.queries.get_live_stats import GetLiveStatsHandler
from app.application.queries.play_statistics import GetPlayStatisticsHandler
from app.application.queries.recommendations import GetRecommendationsHandler
from app.application.queries.search import (
    CachedSearchHandler,
    SearchAdventuresHandler,
    SearchEncountersHandler,
    SearchMonstersHandler,
)

__all__ = [
    "CachedSearchHandler",
    "GetAdventureCardHandler",
    "GetAuthorCardHandler",
    "GetCharacterCardHandler",
    "GetLiveStatsHandler",
    "GetMonsterCardHandler",
    "GetPlayStatisticsHandler",
    "GetRecommendationsHandler",
    "SearchAdventuresHandler",
    "SearchEncountersHandler",
    "SearchMonstersHandler",
]
