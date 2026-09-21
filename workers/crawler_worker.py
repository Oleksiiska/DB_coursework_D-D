"""Процес краулера.

Швидкість обмежена мережею, тому виконується незалежно від
основного завантаження.
"""

from __future__ import annotations

from app.infrastructure.config import Container


class CrawlerWorker:
    def __init__(self, container: Container, tag: str = "dnd", pages: int = 54) -> None:
        self._container = container
        self._tag = tag
        self._pages = pages

    async def run(self) -> None:
        raise NotImplementedError


async def main() -> None:
    raise NotImplementedError
