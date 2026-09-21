"""Адаптер пошукового рушія."""

from __future__ import annotations

from typing import Any, Sequence

from app.domain.values import Filters, Page, Snippet


class SearchIndexAdapter:
    """Реалізує порт `SearchIndex`.

    Індекс зберігає лише поля, потрібні для пошуку й формування видачі;
    повні документи читаються з документного сховища за ключами.
    """

    INDEX_NAME = "adventures_idx"

    def __init__(self, client: Any) -> None:
        self._client = client

    async def search(
        self,
        text: str,
        filters: Filters,
        page: Page,
    ) -> tuple[list[str], dict[str, list[Snippet]], int]:
        raise NotImplementedError

    async def index_many(self, documents: Sequence[dict]) -> int:
        """Пакетна індексація."""
        raise NotImplementedError

    async def delete(self, document_id: str) -> bool:
        raise NotImplementedError

    def _build_filter_clause(self, filters: Filters) -> dict[str, Any]:
        raise NotImplementedError

    def _parse_highlights(self, raw: dict[str, Any]) -> dict[str, list[Snippet]]:
        """Перетворення відповіді рушія на доменні фрагменти."""
        raise NotImplementedError
