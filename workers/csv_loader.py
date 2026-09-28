"""Процес завантаження підготовлених CSV із заданою швидкістю."""

from __future__ import annotations

from app.domain.ports import DocumentSource, EventPublisher
from app.domain.values import DocumentKind


class CsvLoader:
    """Публікує вміст CSV-файлів у чергу в фіксованому порядку.

    Кожен наступний вид посилається на попередні: зустрічі — на монстрів,
    пригоди — на авторів і теги.
    """

    TOPIC = "documents"

    ORDER: tuple[DocumentKind, ...] = (
        DocumentKind.MONSTER,
        DocumentKind.CHARACTER,
        DocumentKind.ENCOUNTER,
        DocumentKind.PARTICIPATION,
        DocumentKind.AUTHOR,
        DocumentKind.TAG,
        DocumentKind.SETTING,
        DocumentKind.ADVENTURE,
    )

    def __init__(self, sources: dict[DocumentKind, DocumentSource], publisher: EventPublisher) -> None:
        self._sources = sources
        self._publisher = publisher

    async def run(self) -> None:
        raise NotImplementedError

    async def load_one(self, kind: DocumentKind) -> int:
        raise NotImplementedError
