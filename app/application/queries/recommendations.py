"""Сценарії обходу графа: рекомендації та ланцюжок кампанії (групи 6, 8)."""

from __future__ import annotations

import json
import logging

from app.application.dto import CampaignQueryDTO, RecommendationDTO, dto_from_dict, dto_to_dict
from app.domain.entities import Adventure
from app.domain.errors import EntityNotFound
from app.domain.ports import AdventureRepository, CacheStore, GraphRepository

log = logging.getLogger("api")


class GetRecommendationsHandler:
    """Реалізує порт `GetRecommendationsUseCase`."""

    CACHE_TTL_SECONDS = 900
    #: Спільний монстр важить удвічі більше за спільний тег: тег описує тему,
    #: а монстр — конкретний матеріал гри; той самий порядок дає граф
    MONSTER_WEIGHT = 2

    def __init__(self, graph: GraphRepository, repository: AdventureRepository, cache: CacheStore) -> None:
        self._graph = graph
        self._repository = repository
        self._cache = cache

    async def handle(self, adventure_id: str, limit: int = 10) -> list[RecommendationDTO]:
        """Схожі за спільними тегами та монстрами, з поясненням підстави."""
        key = f"rec:similar:{adventure_id}:{limit}"
        cached = await self._from_cache(key)
        if cached is not None:
            return cached
        anchor = await self._anchor(adventure_id)
        ids = await self._graph.similar_adventures(adventure_id, limit)
        result = []
        for other in await self._repository.get_many(ids):
            tags = sorted(set(anchor.tags) & set(other.tags))
            monsters = sorted(anchor.shares_monsters_with(other))
            reason = "; ".join(part for part in (
                f"спільні теги: {', '.join(tags)}" if tags else "",
                f"спільних монстрів: {len(monsters)}" if monsters else "",
            ) if part)
            result.append(RecommendationDTO(
                adventure_id=other.adventure_id,
                title=other.title,
                score=float(len(tags) + self.MONSTER_WEIGHT * len(monsters)),
                shared_tags=tags,
                shared_monsters=monsters,
                reason=reason or None,
                level_min=other.levels.minimum,
                level_max=other.levels.maximum,
            ))
        await self._to_cache(key, result)
        return result

    async def campaign_chain(self, query: CampaignQueryDTO) -> list[RecommendationDTO]:
        """Послідовність пригод у межах світу без повторення монстрів.

        Найдорожчий запит системи: обхід змінної глибини з обмеженнями.
        Обмеження за кількістю гравців і тривалістю накладає пошук із
        фільтрами (п. 3.3.1), тут — лише світ, рівні й монстри.
        """
        query.validate()
        key = f"rec:chain:{query.adventure_id or '-'}:{query.setting_id or '-'}:{query.level_from}:{query.level_to}"
        cached = await self._from_cache(key)
        if cached is not None:
            return cached
        setting_id = query.setting_id
        if query.adventure_id:
            anchor = await self._anchor(query.adventure_id)
            setting_id = setting_id or anchor.setting_id
            if setting_id is None:
                return [self._link(anchor, 1)]
        ids = await self._graph.campaign_chain(setting_id, query.level_from, query.level_to, query.adventure_id)
        chain = await self._repository.get_many(ids)
        result = [self._link(adventure, position) for position, adventure in enumerate(chain, start=1)]
        await self._to_cache(key, result)
        return result

    # --- допоміжні кроки ---

    async def _anchor(self, adventure_id: str) -> Adventure:
        adventure = await self._repository.get(adventure_id)
        if adventure is None:
            raise EntityNotFound("adventure", adventure_id)
        return adventure

    @staticmethod
    def _link(adventure: Adventure, position: int) -> RecommendationDTO:
        levels = f"рівні {adventure.levels.minimum}–{adventure.levels.maximum}"
        return RecommendationDTO(
            adventure_id=adventure.adventure_id,
            title=adventure.title,
            score=float(position),
            reason=f"{position}. {levels}",
            level_min=adventure.levels.minimum,
            level_max=adventure.levels.maximum,
        )

    async def _from_cache(self, key: str) -> list[RecommendationDTO] | None:
        try:
            payload = await self._cache.get(key)
        except Exception as error:  # noqa: BLE001
            log.warning("кеш недоступний, рекомендації будуються з графа: %s", error)
            return None
        if payload is None:
            return None
        return [dto_from_dict(RecommendationDTO, item) for item in json.loads(payload)]

    async def _to_cache(self, key: str, items: list[RecommendationDTO]) -> None:
        try:
            await self._cache.set(key, json.dumps(dto_to_dict(items), ensure_ascii=False).encode("utf-8"),
                                  self.CACHE_TTL_SECONDS)
        except Exception as error:  # noqa: BLE001
            log.warning("кеш недоступний, рекомендації не збережено: %s", error)
