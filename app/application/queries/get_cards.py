"""Сценарії читання карток об'єктів (запити групи 4)."""

from __future__ import annotations

from app.application.dto import AdventureCardDTO, MonsterCardDTO, RecommendationDTO
from app.domain.ports import (
    AdventureRepository,
    CacheStore,
    GraphRepository,
    MonsterRepository,
)


class GetAdventureCardHandler:
    """Читання картки за ключем — найдешевший шлях читання."""

    CACHE_TTL_SECONDS = 600

    def __init__(self, repository: AdventureRepository, cache: CacheStore) -> None:
        self._repository = repository
        self._cache = cache

    async def handle(self, adventure_id: str) -> AdventureCardDTO:
        """Кидає EntityNotFound, якщо документа немає."""
        raise NotImplementedError


class GetMonsterStatsHandler:
    """Картка монстра разом із популярністю в реальних іграх."""

    def __init__(self, repository: MonsterRepository, graph: GraphRepository) -> None:
        self._repository = repository
        self._graph = graph

    async def handle(self, monster_id: str) -> MonsterCardDTO:
        raise NotImplementedError

    async def most_played(self, limit: int = 20) -> list[MonsterCardDTO]:
        """Запит групи 7: найчастіші монстри реальних ігор."""
        raise NotImplementedError


class GetRecommendationsHandler:
    """Рекомендації через обхід графа (запити групи 8)."""

    CACHE_TTL_SECONDS = 900

    def __init__(self, graph: GraphRepository, repository: AdventureRepository, cache: CacheStore) -> None:
        self._graph = graph
        self._repository = repository
        self._cache = cache

    async def handle(self, adventure_id: str, limit: int = 10) -> list[RecommendationDTO]:
        """Схожі за спільними тегами та монстрами, з поясненням підстави."""
        raise NotImplementedError

    async def campaign_chain(self, setting_id: str, level_from: int, level_to: int) -> list[RecommendationDTO]:
        """Ланцюжок пригод у межах світу без повторення монстрів."""
        raise NotImplementedError
