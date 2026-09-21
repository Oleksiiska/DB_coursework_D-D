"""Ведучі адаптери: контролери HTTP.

Залежать від вхідних портів, а не від конкретних обробників.
Єдина задача — переклад між транспортом і DTO.
"""

from __future__ import annotations

from typing import Any

from app.application.dto import (
    AdventureCardDTO,
    MonsterCardDTO,
    RateSettingDTO,
    RecommendationDTO,
    SearchQueryDTO,
    SearchResultDTO,
)
from app.application.ports import (
    ConfigureRateUseCase,
    GetAdventureCardUseCase,
    GetMonsterStatsUseCase,
    GetRecommendationsUseCase,
    SearchAdventuresUseCase,
)


class SearchController:
    """`GET /api/search`."""

    def __init__(self, use_case: SearchAdventuresUseCase) -> None:
        self._use_case = use_case

    async def search(self, params: dict[str, Any]) -> SearchResultDTO:
        """Розбирає параметри, делегує сценарію, повертає DTO."""
        raise NotImplementedError


class CardController:
    """`GET /api/adventures/{id}`, `/api/monsters/{id}`, `/api/similar/{id}`."""

    def __init__(
        self,
        adventure_use_case: GetAdventureCardUseCase,
        monster_use_case: GetMonsterStatsUseCase,
        recommendations_use_case: GetRecommendationsUseCase,
    ) -> None:
        self._adventures = adventure_use_case
        self._monsters = monster_use_case
        self._recommendations = recommendations_use_case

    async def adventure(self, adventure_id: str) -> AdventureCardDTO:
        raise NotImplementedError

    async def monster(self, monster_id: str) -> MonsterCardDTO:
        raise NotImplementedError

    async def similar(self, adventure_id: str, limit: int = 10) -> list[RecommendationDTO]:
        raise NotImplementedError


class AdminController:
    """`POST /api/rate` — керування швидкістю зі слайдера інтерфейсу."""

    def __init__(self, use_case: ConfigureRateUseCase) -> None:
        self._use_case = use_case

    async def set_rate(self, payload: dict[str, Any]) -> RateSettingDTO:
        raise NotImplementedError

    async def get_rate(self) -> RateSettingDTO:
        raise NotImplementedError


class ErrorTranslator:
    """Переклад доменних помилок у коди відповіді.

    Домен не знає про HTTP, тому відображення живе тут.
    """

    MAPPING: dict[str, int] = {
        "EntityNotFound": 404,
        "ValidationError": 422,
        "UnknownVocabularyTerm": 422,
        "DomainError": 500,
    }

    def to_response(self, error: Exception) -> tuple[int, dict[str, Any]]:
        raise NotImplementedError
