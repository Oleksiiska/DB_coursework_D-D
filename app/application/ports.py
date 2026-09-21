"""Вхідні порти — сценарії використання, доступні ведучим адаптерам.

Розміщені в шарі сценаріїв, а не в доменному: контракт вхідного порту
оперує об'єктами передачі даних, які є межовими, а не доменними
поняттями. Якби порти лежали в `domain`, доменний шар залежав би від
DTO, чим правило залежностей було б порушено.

Вихідні порти, навпаки, оголошені саме в доменному шарі: без цього
неможлива інверсія залежності від сховищ.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.application.dto import (
    AdventureCardDTO,
    IngestReport,
    LiveStatsDTO,
    MonsterCardDTO,
    RateSettingDTO,
    RecommendationDTO,
    SearchQueryDTO,
    SearchResultDTO,
)


@runtime_checkable
class SearchAdventuresUseCase(Protocol):
    async def handle(self, query: SearchQueryDTO) -> SearchResultDTO:
        ...


@runtime_checkable
class GetAdventureCardUseCase(Protocol):
    async def handle(self, adventure_id: str) -> AdventureCardDTO:
        ...


@runtime_checkable
class GetMonsterStatsUseCase(Protocol):
    async def handle(self, monster_id: str) -> MonsterCardDTO:
        ...


@runtime_checkable
class GetRecommendationsUseCase(Protocol):
    async def handle(self, adventure_id: str, limit: int = 10) -> list[RecommendationDTO]:
        ...


@runtime_checkable
class IngestDocumentUseCase(Protocol):
    async def handle(self, batch: list[dict], source_name: str) -> IngestReport:
        ...


@runtime_checkable
class ConfigureRateUseCase(Protocol):
    async def handle(self, setting: RateSettingDTO) -> RateSettingDTO:
        ...


@runtime_checkable
class GetLiveStatsUseCase(Protocol):
    async def handle(self) -> LiveStatsDTO:
        ...


__all__ = [
    "ConfigureRateUseCase",
    "GetAdventureCardUseCase",
    "GetLiveStatsUseCase",
    "GetMonsterStatsUseCase",
    "GetRecommendationsUseCase",
    "IngestDocumentUseCase",
    "SearchAdventuresUseCase",
]
