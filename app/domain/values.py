"""Об'єкти-значення та переліки предметної області.

Доменний шар не залежить ні від чого: лише стандартна бібліотека.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable


class SourceOrigin(str, Enum):
    """Походження документа (розділ 2)."""

    CSV = "csv"
    GENERATED = "generated"
    CRAWLER = "crawler"


class DocumentKind(str, Enum):
    """Вид документа на шляху запису.

    За видом конвеєр обирає сховище, індекс і правила валідації.
    Кожному виду відповідає власний файл підготовлених даних; ігрові
    світи — список, складений вручну, у тому самому форматі.
    """

    ADVENTURE = "adventure"
    AUTHOR = "author"
    TAG = "tag"
    SETTING = "setting"
    MONSTER = "monster"
    CHARACTER = "character"
    ENCOUNTER = "encounter"
    PARTICIPATION = "participation"

    @property
    def is_indexed(self) -> bool:
        """Чи індексується текст документів цього виду для повнотекстового пошуку.

        Правило предметної області, а не обробника: новий вид документа
        оголошується тут і не потребує зміни коду шляху запису.
        """
        return self in (DocumentKind.ADVENTURE, DocumentKind.MONSTER, DocumentKind.ENCOUNTER)


class HpStatus(str, Enum):
    """Статус здоров'я. Перелік виміряний на реальних даних (п. 2.5.4)."""

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


class SortOrder(str, Enum):
    """Порядок видачі (група запитів 9)."""

    RELEVANCE = "relevance"
    RATING = "rating"
    REVIEWS = "reviews"
    YEAR = "year"
    DURATION = "duration"


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
    """Ціна після нормалізації валют (у джерелі — $, £, EUR)."""

    amount: float | None = None
    currency: str = "USD"

    @property
    def is_free(self) -> bool:
        raise NotImplementedError


# --------------------------------------------------------------------------
# Специфікації пошуку
# --------------------------------------------------------------------------


@runtime_checkable
class SearchSpecification(Protocol):
    """Спільний контракт обмежень видачі.

    Кожен вид пошуку має власний набір фільтрів, але всі вони
    однаково беруть участь у формуванні ключа кешу.
    """

    def is_empty(self) -> bool:
        ...

    def cache_fragment(self) -> str:
        """Стабільне впорядковане представлення для ключа кешу."""
        ...


@dataclass(frozen=True, slots=True)
class AdventureFilters:
    """Обмеження видачі пригод (групи запитів 2 і 6).

    `core_books_only` — лише пригоди без додаткових книг правил: частина
    складеного запиту групи 6 «на вечір для трьох гравців».
    """

    level: int | None = None
    players: int | None = None
    system: str | None = None
    difficulty: int | None = None
    tags: tuple[str, ...] = ()
    setting_id: str | None = None
    is_free: bool | None = None
    max_duration_hours: float | None = None
    language: str | None = None
    year_from: int | None = None
    year_to: int | None = None
    min_rating: float | None = None
    core_books_only: bool = False

    def is_empty(self) -> bool:
        raise NotImplementedError

    def cache_fragment(self) -> str:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class MonsterFilters:
    """Обмеження видачі монстрів (група запитів 3).

    Рівень партії перекладається в діапазон рівня небезпеки.
    """

    challenge_min: float | None = None
    challenge_max: float | None = None
    party_level: int | None = None
    creature_type: str | None = None
    environment: str | None = None

    def is_empty(self) -> bool:
        raise NotImplementedError

    def cache_fragment(self) -> str:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class EncounterFilters:
    """Обмеження видачі зустрічей для пошуку за наративом."""

    monster_id: str | None = None
    min_rounds: int | None = None
    max_rounds: int | None = None
    exclude_abandoned: bool = True

    def is_empty(self) -> bool:
        raise NotImplementedError

    def cache_fragment(self) -> str:
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
class SearchHits:
    """Результат пошуку в індексі.

    Індекс повертає лише ідентифікатори й фрагменти; повні документи
    читаються з документного сховища за ключами.
    """

    ids: tuple[str, ...] = ()
    snippets: dict[str, tuple[Snippet, ...]] = field(default_factory=dict)
    total: int = 0


@dataclass(frozen=True, slots=True)
class QueuedBatch:
    """Пакет документів, отриманий із черги.

    Ідентифікатор потрібен для підтвердження обробки: непідтверджений
    пакет черга доставить повторно.
    """

    batch_id: str
    documents: list[dict] = field(default_factory=list)


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
