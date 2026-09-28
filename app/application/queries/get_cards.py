"""Сценарії читання карток об'єктів (групи запитів 4, 5)."""

from __future__ import annotations

from app.application.dto import AdventureCardDTO, AuthorCardDTO, CharacterCardDTO, MonsterCardDTO
from app.domain.entities import Author
from app.domain.ports import (
    AdventureRepository,
    CacheStore,
    CharacterRepository,
    CrudRepository,
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


class GetMonsterCardHandler:
    """Картка монстра разом із переліком пригод, де він зустрічається."""

    CACHE_TTL_SECONDS = 600

    def __init__(self, repository: MonsterRepository, graph: GraphRepository, cache: CacheStore) -> None:
        self._repository = repository
        self._graph = graph
        self._cache = cache

    async def handle(self, monster_id: str) -> MonsterCardDTO:
        raise NotImplementedError


class GetCharacterCardHandler:
    """Картка персонажа гравця — читання одного документа за ключем."""

    CACHE_TTL_SECONDS = 600

    def __init__(self, repository: CharacterRepository, cache: CacheStore) -> None:
        self._repository = repository
        self._cache = cache

    async def handle(self, character_id: str) -> CharacterCardDTO:
        raise NotImplementedError


class GetAuthorCardHandler:
    """Картка автора: профіль, інші роботи, співавтори.

    Автор зберігається узагальненим CRUD-репозиторієм без спеціалізації.
    """

    CACHE_TTL_SECONDS = 600

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
        raise NotImplementedError
