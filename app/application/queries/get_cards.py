"""Сценарії читання карток об'єктів (групи запитів 4, 5; картка монстра — також 8)."""

from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

from app.application.dto import (
    AdventureCardDTO,
    AuthorCardDTO,
    CharacterCardDTO,
    MonsterCardDTO,
    dto_from_dict,
    dto_to_dict,
)
from app.domain.entities import Author
from app.domain.errors import EntityNotFound
from app.domain.ports import (
    AdventureRepository,
    CacheStore,
    CharacterRepository,
    CrudRepository,
    GraphRepository,
    MonsterRepository,
)

log = logging.getLogger("api")


async def _cached(
    cache: CacheStore, key: str, ttl: int, card_type: type, build: Callable[[], Awaitable[Any]]
) -> Any:
    """Картка з кешу або з побудови з подальшим записом у кеш (п. 4.7).

    Недоступний кеш не зупиняє читання: картка будується зі сховищ.
    """
    try:
        payload = await cache.get(key)
    except Exception as error:  # noqa: BLE001
        log.warning("кеш недоступний, картка читається зі сховищ: %s", error)
        payload = None
    if payload is not None:
        return dto_from_dict(card_type, json.loads(payload))
    card = await build()
    try:
        await cache.set(key, json.dumps(dto_to_dict(card), ensure_ascii=False).encode("utf-8"), ttl)
    except Exception as error:  # noqa: BLE001
        log.warning("кеш недоступний, картку не збережено: %s", error)
    return card


class GetAdventureCardHandler:
    """Читання картки за ключем — найдешевший шлях читання."""

    CACHE_TTL_SECONDS = 600

    def __init__(self, repository: AdventureRepository, cache: CacheStore) -> None:
        self._repository = repository
        self._cache = cache

    async def handle(self, adventure_id: str) -> AdventureCardDTO:
        """Кидає EntityNotFound, якщо документа немає."""

        async def build() -> AdventureCardDTO:
            adventure = await self._repository.get(adventure_id)
            if adventure is None:
                raise EntityNotFound("adventure", adventure_id)
            return AdventureCardDTO.from_entity(adventure, with_reviews=True)

        return await _cached(self._cache, f"card:adventure:{adventure_id}", self.CACHE_TTL_SECONDS,
                             AdventureCardDTO, build)


class GetMonsterCardHandler:
    """Картка монстра разом із переліком пригод, де він зустрічається,
    і монстрами, що трапляються з ним у тих самих реальних боях."""

    CACHE_TTL_SECONDS = 600
    APPEARS_IN_LIMIT = 20
    SEEN_TOGETHER_LIMIT = 10

    def __init__(self, repository: MonsterRepository, graph: GraphRepository, cache: CacheStore) -> None:
        self._repository = repository
        self._graph = graph
        self._cache = cache

    async def handle(self, monster_id: str) -> MonsterCardDTO:
        async def build() -> MonsterCardDTO:
            monster = await self._repository.get(monster_id)
            if monster is None:
                raise EntityNotFound("monster", monster_id)
            card = MonsterCardDTO.from_entity(monster)
            card.appears_in = await self._graph.adventures_with_monster(monster_id, self.APPEARS_IN_LIMIT)
            together = await self._graph.seen_together(monster_id, self.SEEN_TOGETHER_LIMIT)
            names = {m.monster_id: m.name for m in await self._repository.get_many([m for m, _ in together])}
            card.seen_together = [(other, names.get(other, other), count) for other, count in together]
            return card

        return await _cached(self._cache, f"card:monster:{monster_id}", self.CACHE_TTL_SECONDS, MonsterCardDTO, build)


class GetCharacterCardHandler:
    """Картка персонажа гравця — читання одного документа за ключем."""

    CACHE_TTL_SECONDS = 600

    def __init__(self, repository: CharacterRepository, cache: CacheStore) -> None:
        self._repository = repository
        self._cache = cache

    async def handle(self, character_id: str) -> CharacterCardDTO:
        async def build() -> CharacterCardDTO:
            character = await self._repository.get(character_id)
            if character is None:
                raise EntityNotFound("character", character_id)
            return CharacterCardDTO.from_entity(character)

        return await _cached(self._cache, f"card:character:{character_id}", self.CACHE_TTL_SECONDS,
                             CharacterCardDTO, build)


class GetAuthorCardHandler:
    """Картка автора: профіль, інші роботи, співавтори.

    Автор зберігається узагальненим CRUD-репозиторієм без спеціалізації.
    """

    CACHE_TTL_SECONDS = 600
    WORKS_LIMIT = 20

    def __init__(
        self,
        authors: CrudRepository[Author],
        adventures: AdventureRepository,
        graph: GraphRepository,
        cache: CacheStore,
    ) -> None:
        self._authors = authors
        self._adventures = adventures
        self._graph = graph
        self._cache = cache

    async def handle(self, author_id: str) -> AuthorCardDTO:
        async def build() -> AuthorCardDTO:
            author = await self._authors.get(author_id)
            if author is None:
                raise EntityNotFound("author", author_id)
            works = await self._adventures.find_by_author(author_id, self.WORKS_LIMIT)
            co_ids = await self._graph.co_authors(author_id)
            names = {a.author_id: a.name for a in await self._authors.get_many(co_ids)}
            return AuthorCardDTO(
                author_id=author.author_id,
                name=author.name,
                bio=author.bio,
                profile_url=author.profile_url,
                platform=author.platform,
                avg_rating=author.avg_rating,
                adventures_count=author.adventures_count,
                first_published_year=author.first_published_year,
                origin=author.provenance.origin.value,
                adventures=[AdventureCardDTO.from_entity(work) for work in works],
                co_authors=[(co, names.get(co, co)) for co in co_ids],
            )

        return await _cached(self._cache, f"card:author:{author_id}", self.CACHE_TTL_SECONDS, AuthorCardDTO, build)
