"""Сценарій статистики реальної гри (група запитів 7)."""

from __future__ import annotations

import json
import logging

from app.application.dto import PlayStatisticsDTO, PlayStatisticsQueryDTO, dto_from_dict, dto_to_dict
from app.domain.ports import (
    CacheStore,
    CharacterRepository,
    EncounterRepository,
    MonsterRepository,
)

log = logging.getLogger("api")


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
        query.validate()
        key = f"stats:play:{query.report}:{query.char_class or '-'}:{query.level or '-'}:{query.limit}"
        try:
            payload = await self._cache.get(key)
        except Exception as error:  # noqa: BLE001
            log.warning("кеш недоступний, статистика рахується у сховищі: %s", error)
            payload = None
        if payload is not None:
            return dto_from_dict(PlayStatisticsDTO, json.loads(payload))

        result = PlayStatisticsDTO(report=query.report, char_class=query.char_class, level=query.level)
        if query.report == "monsters":
            result.popular_monsters = [
                (m.monster_id, m.name, m.play_count) for m in await self._monsters.most_played(query.limit)
            ]
        elif query.report == "spells":
            result.typical_spells = await self._characters.typical_spells(query.char_class, query.level, query.limit)
        elif query.report == "party":
            result.class_race = (await self._characters.class_race_distribution())[: query.limit * 3]
            result.party_composition = await self._encounters.party_composition(query.level)
        else:  # combat
            median_seconds = await self._encounters.median_duration(exclude_abandoned=True)
            result.median_duration_minutes = None if median_seconds is None else round(median_seconds / 60, 1)
            result.median_rounds = await self._encounters.median_rounds(exclude_abandoned=True)

        try:
            await self._cache.set(key, json.dumps(dto_to_dict(result), ensure_ascii=False).encode("utf-8"),
                                  self.CACHE_TTL_SECONDS)
        except Exception as error:  # noqa: BLE001
            log.warning("кеш недоступний, статистику не збережено: %s", error)
        return result
