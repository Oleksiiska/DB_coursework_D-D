"""Сценарій статистики реальної гри (група запитів 7)."""

from __future__ import annotations

from app.application.dto import PlayStatisticsDTO, PlayStatisticsQueryDTO
from app.domain.ports import (
    CacheStore,
    CharacterRepository,
    EncounterRepository,
    MonsterRepository,
)


class GetPlayStatisticsHandler:
    """Агрегації по персонажах, зустрічах і монстрах.

    Усі розподіли тривалості й кількості раундів рахуються медіанами:
    у даних є викиди на три порядки через покинуті сесії.
    Результат кешується надовго — дані реальних ігор завантажуються
    одноразово й не змінюються під навантаженням.
    """

    CACHE_TTL_SECONDS = 3600

    def __init__(
        self,
        monsters: MonsterRepository,
        characters: CharacterRepository,
        encounters: EncounterRepository,
        cache: CacheStore,
    ) -> None:
        self._monsters = monsters
        self._characters = characters
        self._encounters = encounters
        self._cache = cache

    async def handle(self, query: PlayStatisticsQueryDTO) -> PlayStatisticsDTO:
        raise NotImplementedError
