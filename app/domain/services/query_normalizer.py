"""Нормалізація пошукового запиту.

Розміщено в доменному шарі, а не в адаптері кешу: зведення до нижнього
регістру та впорядкування слів — правило предметної області, а не
деталь зберігання.
"""

from __future__ import annotations

from app.domain.values import Filters


class QueryNormalizer:
    """Доменна служба без стану."""

    #: Префікс простору ключів кешу
    CACHE_PREFIX = "search"

    def normalize(self, raw: str) -> str:
        """Нижній регістр, згортання пробілів, впорядкування слів.

        Запити «прокляте село» і «село прокляте» дають однаковий ключ.
        """
        raise NotImplementedError

    def cache_key(self, raw: str, filters: Filters) -> str:
        """Складений ключ: нормалізований текст плюс відбиток фільтрів."""
        raise NotImplementedError

    def is_empty(self, raw: str) -> bool:
        raise NotImplementedError
