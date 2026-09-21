"""Об'єкти-значення та переліки предметної області.

Доменний шар не залежить ні від чого: лише стандартна бібліотека.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SourceOrigin(str, Enum):
    """Походження документа (розділ 3)."""

    CSV = "csv"
    GENERATED = "generated"
    CRAWLER = "crawler"


class HpStatus(str, Enum):
    """Статус здоров'я. Перелік виміряний на реальних даних."""

    HEALTHY = "Healthy"
    INJURED = "Injured"
    BLOODIED = "Bloodied"
    CRITICAL = "Critical"
    DEAD = "Dead"


class TagCategory(str, Enum):
    TYPE = "Types"
    GAMEPLAY = "Gameplay"
    FORMAT = "Format"
    THEME = "Theme"


class QueryStatus(str, Enum):
    FOUND = "FOUND"
    EMPTY = "EMPTY"


@dataclass(frozen=True, slots=True)
class LevelRange:
    """Діапазон рівнів персонажів. Межі можуть бути невідомі."""

    minimum: int | None = None
    maximum: int | None = None

    def covers(self, level: int) -> bool:
        """Чи підходить пригода партії вказаного рівня."""
        raise NotImplementedError

    def overlaps(self, other: LevelRange) -> bool:
        """Чи перетинаються діапазони — для побудови ланцюжка кампанії."""
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class PartySize:
    minimum: int | None = None
    maximum: int | None = None

    def fits(self, players: int) -> bool:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class Money:
    """Ціна після нормалізації валют."""

    amount: float | None = None
    currency: str = "USD"

    @property
    def is_free(self) -> bool:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class Filters:
    """Специфікація обмежень видачі.

    Незмінна: бере участь у формуванні ключа кешу.
    """

    level: int | None = None
    players: int | None = None
    system: str | None = None
    tags: tuple[str, ...] = ()
    setting_id: str | None = None
    is_free: bool | None = None
    max_duration_hours: float | None = None
    language: str | None = None
    min_rating: float | None = None

    def is_empty(self) -> bool:
        raise NotImplementedError

    def cache_fragment(self) -> str:
        """Стабільне впорядковане представлення для ключа кешу."""
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class Page:
    number: int = 1
    size: int = 20

    @property
    def offset(self) -> int:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class Snippet:
    """Фрагмент тексту з позиціями збігів для підсвічування."""

    field_name: str
    text: str
    highlights: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True, slots=True)
class ShardKey:
    """Ключ розподілу документа між вузлами."""

    value: str
    shard_index: int


@dataclass(slots=True)
class Provenance:
    """Походження документа та перелік похідних полів."""

    origin: SourceOrigin
    derived_fields: list[str] = field(default_factory=list)

    def mark_derived(self, field_name: str) -> None:
        raise NotImplementedError
