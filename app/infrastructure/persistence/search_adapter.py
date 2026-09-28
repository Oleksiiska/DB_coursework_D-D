"""Адаптер пошукового рушія."""

from __future__ import annotations

from typing import Any, Sequence

from app.domain.values import Page, SearchHits, SearchSpecification, Snippet, SortOrder


class SearchIndexAdapter:
    """Реалізує порт `SearchIndex`.

    Один клас — три екземпляри з різними індексами: пригод, монстрів,
    зустрічей. Індекс зберігає лише поля, потрібні для пошуку й видачі;
    повні документи читаються з документного сховища за ключами.
    """

    def __init__(self, client: Any, index_name: str) -> None:
        self._client = client
        self._index_name = index_name

    async def search(
        self,
        text: str,
        spec: SearchSpecification,
        page: Page,
        sort: SortOrder = SortOrder.RELEVANCE,
    ) -> SearchHits:
        raise NotImplementedError

    async def index_many(self, documents: Sequence[dict]) -> int:
        """Пакетна індексація."""
        raise NotImplementedError

    async def delete(self, document_id: str) -> bool:
        raise NotImplementedError

    def _build_filter_clause(self, spec: SearchSpecification) -> dict[str, Any]:
        raise NotImplementedError

    def _parse_highlights(self, raw: dict[str, Any]) -> dict[str, tuple[Snippet, ...]]:
        """Перетворення відповіді рушія на доменні фрагменти."""
        raise NotImplementedError
