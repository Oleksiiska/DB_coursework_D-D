"""Ведучі адаптери: контролери HTTP.

Залежать від вхідних портів, а не від конкретних обробників.
Єдина задача — переклад між транспортом і DTO: перевірені фреймворком
параметри запиту стають об'єктами передачі даних сценаріїв, результати —
JSON-сумісними словниками.
"""

from __future__ import annotations

import logging
from typing import Any

from app.application.dto import (
    CampaignQueryDTO,
    PlayStatisticsQueryDTO,
    RateSettingDTO,
    SearchQueryDTO,
    dto_to_dict,
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
log = logging.getLogger("api")


class SearchController:
    """`GET /api/search/{kind}`, kind — adventures, monsters або encounters.

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

    async def search(self, kind: str, params: dict[str, Any]) -> dict[str, Any]:
        """Невідомий вид пошуку відхиляє SearchQueryDTO.from_request (відповідь 422)."""
        query = SearchQueryDTO.from_request({**params, "kind": kind})
        use_case = {"adventures": self._adventures, "monsters": self._monsters, "encounters": self._encounters}[kind]
        return dto_to_dict(await use_case.handle(query))

    async def search_adventures(self, params: dict[str, Any]) -> dict[str, Any]:
        return await self.search("adventures", params)

    async def search_monsters(self, params: dict[str, Any]) -> dict[str, Any]:
        return await self.search("monsters", params)

    async def search_encounters(self, params: dict[str, Any]) -> dict[str, Any]:
        return await self.search("encounters", params)


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

    async def adventure(self, adventure_id: str) -> dict[str, Any]:
        return dto_to_dict(await self._adventure_card.handle(adventure_id))

    async def monster(self, monster_id: str) -> dict[str, Any]:
        return dto_to_dict(await self._monster_card.handle(monster_id))

    async def character(self, character_id: str) -> dict[str, Any]:
        return dto_to_dict(await self._character_card.handle(character_id))

    async def author(self, author_id: str) -> dict[str, Any]:
        return dto_to_dict(await self._author_card.handle(author_id))

    async def similar(self, adventure_id: str, limit: int = 10) -> list[dict[str, Any]]:
        return dto_to_dict(await self._recommendations.handle(adventure_id, limit))

    async def campaign(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        query = CampaignQueryDTO(
            adventure_id=params.get("adventure_id"),
            setting_id=params.get("setting_id"),
            level_from=int(params.get("level_from") or 1),
            level_to=int(params.get("level_to") or 10),
        )
        return dto_to_dict(await self._recommendations.campaign_chain(query))


class StatisticsController:
    """`GET /api/statistics/{report}` — статистика реальної гри."""

    def __init__(self, play_statistics: GetPlayStatisticsUseCase) -> None:
        self._play_statistics = play_statistics

    async def play_stats(self, params: dict[str, Any]) -> dict[str, Any]:
        query = PlayStatisticsQueryDTO(
            report=params.get("report", "monsters"),
            char_class=params.get("char_class") or None,
            level=int(params["level"]) if params.get("level") not in (None, "") else None,
            limit=int(params.get("limit") or 10),
        )
        return dto_to_dict(await self._play_statistics.handle(query))


class AdminController:
    """`GET/POST /api/rate` — керування швидкістю зі слайдера інтерфейсу."""

    def __init__(self, configure_rate: ConfigureRateUseCase) -> None:
        self._configure_rate = configure_rate

    async def set_rate(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Тип значення перевіряє фреймворк, діапазон 10–5000 — сценарій."""
        setting = RateSettingDTO(documents_per_second=int(payload["documents_per_second"]))
        return dto_to_dict(await self._configure_rate.handle(setting))

    async def get_rate(self) -> dict[str, Any]:
        return dto_to_dict(await self._configure_rate.current())


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
        """Код за найближчим відомим класом помилки в ієрархії; невідома
        помилка — 500 і запис рівня ERROR (таблиця 4.17)."""
        for cls in type(error).__mro__:
            if cls.__name__ in self.MAPPING:
                status = self.MAPPING[cls.__name__]
                break
        else:
            status = 500
        body: dict[str, Any] = {"error": type(error).__name__, "detail": str(error)}
        reasons = getattr(error, "reasons", None)
        if reasons:
            body["reasons"] = list(reasons)
        if status >= 500:
            log.error("необроблений виняток у сценарії: %s: %s", type(error).__name__, error, exc_info=error)
        return status, body
