"""Джерело: краулер каталогу.

Обмеження зчитані з robots.txt: заборонені маршрути не запитуються,
посилання на файли модулів не збираються.
"""

from __future__ import annotations

from typing import AsyncIterator


class PolitenessPolicy:
    """Правила ввічливого обходу.

    Директива Crawl-delay у джерелі відсутня, тому затримка
    обирається самостійно.
    """

    DISALLOWED_PREFIXES: tuple[str, ...] = (
        "/embed/", "/embed-upload/", "/search", "/checkout/",
        "/game/download/", "/bundle/download/",
        "/register-for-purchase/", "/email-feedback/",
    )

    def __init__(self, min_delay: float = 1.5, max_delay: float = 3.0) -> None:
        self._min_delay = min_delay
        self._max_delay = max_delay

    def is_allowed(self, path: str) -> bool:
        raise NotImplementedError

    async def wait(self) -> None:
        """Випадкова затримка в заданому діапазоні."""
        raise NotImplementedError

    def next_user_agent(self) -> str:
        """Ротація заголовка."""
        raise NotImplementedError


class CrawlerSource:
    """Реалізує порт `DocumentSource`.

    Швидкість обмежена мережею: 5–20 документів за секунду, тому
    обсяг для навантаження дає не він, а генератор.
    """

    LIST_PAGE_SIZE = 36

    def __init__(self, base_url: str, tag: str, policy: PolitenessPolicy) -> None:
        self._base_url = base_url
        self._tag = tag
        self._policy = policy

    @property
    def name(self) -> str:
        return "crawler"

    async def stream(self, batch_size: int = 20) -> AsyncIterator[list[dict]]:
        """Двоетапний обхід: сторінки списку, потім картки."""
        raise NotImplementedError

    async def set_rate(self, documents_per_second: int) -> None:
        """Для краулера — верхня межа, що не перевищує ввічливої затримки."""
        raise NotImplementedError

    async def _collect_links(self, pages: int) -> list[str]:
        raise NotImplementedError

    async def _parse_card(self, url: str) -> dict:
        """Розбір картки: таблиця властивостей, ціна, коментарі."""
        raise NotImplementedError
