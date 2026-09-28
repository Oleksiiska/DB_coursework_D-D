"""Ведучі адаптери: контролери HTTP.

Залежать від вхідних портів, а не від конкретних обробників.
Єдина задача — переклад між транспортом і DTO.
"""

from __future__ import annotations

from typing import Any

from app.application.dto import (
    AdventureCardDTO,
    AuthorCardDTO,
    CharacterCardDTO,
    MonsterCardDTO,
    PlayStatisticsDTO,
    RateSettingDTO,
    RecommendationDTO,
    SearchResultDTO,
)
from app.application.ports import (
    ConfigureRateUseCase,
    GetAdventureCardUseCase,
    GetAuthorCardUseCase,
    GetCharacterCardUseCase,
    GetMonsterCardUseCase,
    GetPlayStatisticsUseCase,
    GetRecommendationsUseCase,
    SearchUseCase,
)


class SearchController:
    """`GET /api/search/adventures`, `/api/search/monsters`, `/api/search/encounters`.

    Три екземпляри одного порту `SearchUseCase` — по одному на вид пошуку.
    """

    def __init__(
        self,
        adventures: SearchUseCase,
        monsters: SearchUseCase,
        encounters: SearchUseCase,
    ) -> None:
        self._adventures = adventures
        self._monsters = monsters
        self._encounters = encounters

    async def search_adventures(self, params: dict[str, Any]) -> SearchResultDTO:
        raise NotImplementedError

    async def search_monsters(self, params: dict[str, Any]) -> SearchResultDTO:
        raise NotImplementedError

    async def search_encounters(self, params: dict[str, Any]) -> SearchResultDTO:
        raise NotImplementedError


class CardController:
    """Картки й навігація: пригода, монстр, персонаж, автор, схожі, ланцюжок кампанії."""

    def __init__(
        self,
        adventure_card: GetAdventureCardUseCase,
        monster_card: GetMonsterCardUseCase,
        character_card: GetCharacterCardUseCase,
        author_card: GetAuthorCardUseCase,
        recommendations: GetRecommendationsUseCase,
    ) -> None:
        self._adventure_card = adventure_card
        self._monster_card = monster_card
        self._character_card = character_card
        self._author_card = author_card
        self._recommendations = recommendations

    async def adventure(self, adventure_id: str) -> AdventureCardDTO:
        raise NotImplementedError

    async def monster(self, monster_id: str) -> MonsterCardDTO:
        raise NotImplementedError

    async def character(self, character_id: str) -> CharacterCardDTO:
        raise NotImplementedError

    async def author(self, author_id: str) -> AuthorCardDTO:
        raise NotImplementedError

    async def similar(self, adventure_id: str, limit: int = 10) -> list[RecommendationDTO]:
        raise NotImplementedError

    async def campaign(self, params: dict[str, Any]) -> list[RecommendationDTO]:
        raise NotImplementedError


class StatisticsController:
    """`GET /api/play-stats` — статистика реальної гри."""

    def __init__(self, play_statistics: GetPlayStatisticsUseCase) -> None:
        self._play_statistics = play_statistics

    async def play_stats(self, params: dict[str, Any]) -> PlayStatisticsDTO:
        raise NotImplementedError


class AdminController:
    """`GET/POST /api/rate` — керування швидкістю зі слайдера інтерфейсу."""

    def __init__(self, configure_rate: ConfigureRateUseCase) -> None:
        self._configure_rate = configure_rate

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
