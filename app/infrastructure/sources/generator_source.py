"""Джерело: генератор документів (Шлях В).

Забезпечує обсяг, потрібний для навантажувального тестування.
Працює окремим процесом: навантаження на процесор, а не на введення-виведення.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator


@dataclass(slots=True)
class ThemeProfile:
    """Тематичний профіль — джерело узгодженості полів (п. 2.7.1).

    Без кореляції між темами й монстрами запит «схожі пригоди»
    повертає шум, і роботу графової бази неможливо продемонструвати.
    """

    name: str
    tag_weights: dict[str, float] = field(default_factory=dict)
    monster_type_weights: dict[str, float] = field(default_factory=dict)
    environment_weights: dict[str, float] = field(default_factory=dict)
    keywords: list[str] = field(default_factory=list)
    tone: str | None = None


class MarkovTextBuilder:
    """Марковський ланцюг третього порядку.

    Корпус — описи довідника та анотації каталогу. Наратив реальних
    ігор не використовується: середня довжина репліки 28 символів
    зруйнувала б зв'язність (п. 2.7.4).
    """

    def __init__(self, order: int = 3) -> None:
        self._order = order

    def train(self, corpus: list[str]) -> None:
        raise NotImplementedError

    def generate(self, sentences: int, keywords: list[str]) -> str:
        """Генерація з примусовою вставкою ключових слів профілю."""
        raise NotImplementedError


class GeneratorSource:
    """Реалізує порт `DocumentSource`."""

    MIN_RATE = 10
    MAX_RATE = 5000

    def __init__(
        self,
        profiles: list[ThemeProfile],
        text_builder: MarkovTextBuilder,
        monster_pool: list[dict],
        author_pool: list[dict],
        rate: int = 100,
    ) -> None:
        self._profiles = profiles
        self._text_builder = text_builder
        self._monster_pool = monster_pool
        self._author_pool = author_pool
        self._rate = rate

    @property
    def name(self) -> str:
        return "generator"

    async def stream(self, batch_size: int = 500) -> AsyncIterator[list[dict]]:
        raise NotImplementedError

    async def set_rate(self, documents_per_second: int) -> None:
        raise NotImplementedError

    def build_document(self) -> dict:
        """Формування одного документа за обраним профілем."""
        raise NotImplementedError

    def _pick_monsters(self, level_min: int, level_max: int, profile: ThemeProfile) -> list[str]:
        """Відбір за рівнем небезпеки під рівень партії.

        Ваги пропорційні реальній популярності: часті істоти
        трапляються часто, рідкісні — рідко.
        """
        raise NotImplementedError

    def _pick_authors(self) -> list[str]:
        """Вибір за законом Ципфа: 1 автор — 99%, 2–3 — 1%."""
        raise NotImplementedError
