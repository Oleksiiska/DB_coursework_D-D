"""Одноразове завантаження підготовлених CSV із заданою швидкістю."""

from __future__ import annotations

from app.infrastructure.config import Container


class CsvLoader:
    """Порядок завантаження фіксований: кожен крок споживає
    результати попередніх."""

    ORDER: tuple[str, ...] = (
        "monsters",
        "characters",
        "encounters",
        "participations",
        "authors",
        "tags",
        "adventures",
    )

    def __init__(self, container: Container, rate: int = 1000) -> None:
        self._container = container
        self._rate = rate

    async def run(self) -> None:
        raise NotImplementedError

    async def load_one(self, kind: str) -> int:
        raise NotImplementedError


async def main() -> None:
    raise NotImplementedError
