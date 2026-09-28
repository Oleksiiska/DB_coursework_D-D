"""Нормалізація пошукового запиту.

Розміщено в доменному шарі, а не в адаптері кешу: зведення до нижнього
регістру та впорядкування слів — правило предметної області, а не
деталь зберігання (п. 3.8.5).
"""

from __future__ import annotations

from app.domain.values import DocumentKind, SearchSpecification


class QueryNormalizer:
    """Доменна служба без стану."""

    #: Префікс простору ключів кешу
    CACHE_PREFIX = "search"

    def normalize(self, raw: str) -> str:
        """Нижній регістр, згортання пробілів, впорядкування слів.

        Запити «прокляте село» і «село прокляте» дають однаковий ключ.
        """
        raise NotImplementedError

    def cache_key(self, kind: DocumentKind, raw: str, spec: SearchSpecification) -> str:
        """Складений ключ: вид пошуку, нормалізований текст, відбиток фільтрів.

        Вид входить у ключ, щоб пошук пригод і пошук монстрів за тим
        самим текстом не перетиналися в кеші.
        """
        raise NotImplementedError

    def is_empty(self, raw: str) -> bool:
        raise NotImplementedError
