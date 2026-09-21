"""Адаптер графового сховища."""

from __future__ import annotations

from typing import Any, Sequence


class GraphAdapter:
    """Реалізує порт `GraphRepository`.

    Граф тримає лише ідентифікатори, назви та ребра: описи й тексти
    лишаються в документному сховищі, інакше обходи сповільнюються.
    """

    def __init__(self, driver: Any) -> None:
        self._driver = driver

    async def similar_adventures(self, adventure_id: str, limit: int = 10) -> list[str]:
        """Обхід через спільні теги та монстрів із ранжуванням."""
        raise NotImplementedError

    async def adventures_with_monster(self, monster_id: str) -> list[str]:
        raise NotImplementedError

    async def co_authors(self, author_id: str) -> list[str]:
        raise NotImplementedError

    async def campaign_chain(self, setting_id: str, level_from: int, level_to: int) -> list[str]:
        """Найдорожчий запит системи: обхід змінної глибини з обмеженнями."""
        raise NotImplementedError

    async def merge_nodes_and_edges(self, batch: Sequence[dict]) -> int:
        """Ідемпотентне оновлення: повторний запис пакета не дублює ребер."""
        raise NotImplementedError
