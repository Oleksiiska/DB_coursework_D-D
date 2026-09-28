"""Вхідні порти — сценарії використання, доступні ведучим адаптерам.

Розміщені в шарі сценаріїв, а не в доменному: контракт вхідного порту
оперує об'єктами передачі даних, які є межовими, а не доменними
поняттями. Якби порти лежали в `domain`, доменний шар залежав би від
DTO, чим правило залежностей було б порушено.

Вихідні порти, навпаки, оголошені в доменному шарі: без цього
неможлива інверсія залежності від сховищ.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.application.dto import (
    AdventureCardDTO,
    AuthorCardDTO,
    CampaignQueryDTO,
    CharacterCardDTO,
    IngestReport,
    LiveStatsDTO,
    MonsterCardDTO,
    PlayStatisticsDTO,
    PlayStatisticsQueryDTO,
    RateSettingDTO,
    RecommendationDTO,
    SearchQueryDTO,
    SearchResultDTO,
)

# --------------------------------------------------------------------------
# Шлях запитів
# --------------------------------------------------------------------------


@runtime_checkable
class SearchUseCase(Protocol):
    """Повнотекстовий пошук (групи запитів 1, 2, 3, 6, 9).

    Один контракт — шаблон `CachedSearchHandler` і три його нащадки:
    пошук пригод, монстрів і зустрічей. З групи 6 обслуговує складений
    запит із фільтрами («без додаткових книг, на трьох гравців, на вечір»).
    """

    async def handle(self, query: SearchQueryDTO) -> SearchResultDTO:
        ...


@runtime_checkable
class GetAdventureCardUseCase(Protocol):
    """Картка пригоди за ключем (група 4)."""

    async def handle(self, adventure_id: str) -> AdventureCardDTO:
        ...


@runtime_checkable
class GetMonsterCardUseCase(Protocol):
    """Картка монстра з переліком пригод, де він зустрічається (групи 4, 5)."""

    async def handle(self, monster_id: str) -> MonsterCardDTO:
        ...


@runtime_checkable
class GetCharacterCardUseCase(Protocol):
    """Картка персонажа гравця: клас, раса, рівень, закляття (група 4)."""

    async def handle(self, character_id: str) -> CharacterCardDTO:
        ...


@runtime_checkable
class GetAuthorCardUseCase(Protocol):
    """Картка автора з іншими роботами та співавторами (групи 4, 5)."""

    async def handle(self, author_id: str) -> AuthorCardDTO:
        ...


@runtime_checkable
class GetRecommendationsUseCase(Protocol):
    """Рекомендації та ланцюжок кампанії (групи 6, 8)."""

    async def handle(self, adventure_id: str, limit: int = 10) -> list[RecommendationDTO]:
        ...

    async def campaign_chain(self, query: CampaignQueryDTO) -> list[RecommendationDTO]:
        ...


@runtime_checkable
class GetPlayStatisticsUseCase(Protocol):
    """Статистика реальної гри (група 7)."""

    async def handle(self, query: PlayStatisticsQueryDTO) -> PlayStatisticsDTO:
        ...


@runtime_checkable
class GetLiveStatsUseCase(Protocol):
    """Стан системи для панелі моніторингу (група 10)."""

    async def handle(self) -> LiveStatsDTO:
        ...


# --------------------------------------------------------------------------
# Шлях команд
# --------------------------------------------------------------------------


@runtime_checkable
class IngestDocumentUseCase(Protocol):
    async def handle(self, batch: list[dict], source_name: str) -> IngestReport:
        ...


@runtime_checkable
class ConfigureRateUseCase(Protocol):
    async def handle(self, setting: RateSettingDTO) -> RateSettingDTO:
        ...

    async def current(self, source_name: str = "generator") -> RateSettingDTO:
        ...


#: Відповідність груп запитів (таблиця 1.2) вхідним портам.
#: Перевіряється тестом: кожна група має обслуговуватися хоча б одним портом.
QUERY_GROUP_COVERAGE: dict[int, tuple[type, ...]] = {
    1: (SearchUseCase,),
    2: (SearchUseCase,),
    3: (SearchUseCase,),
    4: (GetAdventureCardUseCase, GetMonsterCardUseCase, GetCharacterCardUseCase, GetAuthorCardUseCase),
    5: (GetAuthorCardUseCase, GetMonsterCardUseCase),
    6: (GetRecommendationsUseCase, SearchUseCase),
    7: (GetPlayStatisticsUseCase,),
    8: (GetRecommendationsUseCase,),
    9: (SearchUseCase,),
    10: (GetLiveStatsUseCase,),
}


__all__ = [
    "ConfigureRateUseCase",
    "GetAdventureCardUseCase",
    "GetAuthorCardUseCase",
    "GetCharacterCardUseCase",
    "GetLiveStatsUseCase",
    "GetMonsterCardUseCase",
    "GetPlayStatisticsUseCase",
    "GetRecommendationsUseCase",
    "IngestDocumentUseCase",
    "QUERY_GROUP_COVERAGE",
    "SearchUseCase",
]
