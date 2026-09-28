"""Сценарії обходу графа: рекомендації та ланцюжок кампанії (групи 6, 8)."""

from __future__ import annotations

from app.application.dto import CampaignQueryDTO, RecommendationDTO
from app.domain.ports import AdventureRepository, CacheStore, GraphRepository


class GetRecommendationsHandler:
    """Реалізує порт `GetRecommendationsUseCase`."""

    CACHE_TTL_SECONDS = 900

    def __init__(self, graph: GraphRepository, repository: AdventureRepository, cache: CacheStore) -> None:
        self._graph = graph
        self._repository = repository
        self._cache = cache

    async def handle(self, adventure_id: str, limit: int = 10) -> list[RecommendationDTO]:
        """Схожі за спільними тегами та монстрами, з поясненням підстави."""
        raise NotImplementedError

    async def campaign_chain(self, query: CampaignQueryDTO) -> list[RecommendationDTO]:
        """Послідовність пригод у межах світу без повторення монстрів.

        Найдорожчий запит системи: обхід змінної глибини з обмеженнями.
        """
        raise NotImplementedError
