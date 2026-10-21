"""Об'єкти-значення та переліки предметної області.

Доменний шар не залежить ні від чого: лише стандартна бібліотека.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, fields
from enum import Enum
from typing import Any, Protocol, runtime_checkable

#: Основа просторів імен детермінованих ключів (п. 2.8.3). Простір імен
#: виду документа — UUIDv5 від цієї адреси з назвою колекції в кінці,
#: наприклад …/settings для ігрових світів (app/data/README.md)
ID_NAMESPACE_URL = "https://github.com/Oleksiiska/DB_coursework_D-D/"


class SourceOrigin(str, Enum):
    """Шлях надходження документа (п. 2.1.2).

    Разом із видом документа однозначно вказує джерело з таблиці 2.1:
    підготовлені CSV — це SRD, FIREBALL або каталог itch.io залежно від виду.
    MANUAL — списки, складені вручну (app/data/): ігрові світи.
    """

    CSV = "csv"
    GENERATED = "generated"
    CRAWLER = "crawler"
    MANUAL = "manual"


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

    @property
    def plural(self) -> str:
        """Назва колекції й виду пошуку: adventures, monsters, encounters…"""
        return f"{self.value}s"

    def natural_id(self, natural_key: str) -> str:
        """Детермінований ключ документа — UUID версії 5 від природного ключа (п. 2.8.3).

        Природний ключ: itch_id для пригод із каталогу, index SRD для
        монстрів, profile_url або псевдонім для авторів, нормалізована
        назва для тегів, slug для світів, пара «name:controller_id» для
        персонажів. Повторно завантажений документ отримує той самий ключ,
        тож запис ідемпотентний.
        """
        namespace = uuid.uuid5(uuid.NAMESPACE_URL, ID_NAMESPACE_URL + self.plural)
        return str(uuid.uuid5(namespace, natural_key))


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
        """Чи підходить пригода партії вказаного рівня.

        Невідомий діапазон не підходить жодному рівню: запис доступний
        через пошук, але не потрапляє у видачу з фільтром (таблиця 1.5).
        """
        if self.minimum is None or self.maximum is None:
            return False
        return self.minimum <= level <= self.maximum

    def overlaps(self, other: LevelRange) -> bool:
        """Чи перетинаються діапазони — для побудови ланцюжка кампанії."""
        if None in (self.minimum, self.maximum, other.minimum, other.maximum):
            return False
        return self.minimum <= other.maximum and other.minimum <= self.maximum  # type: ignore[operator]


@dataclass(frozen=True, slots=True)
class PartySize:
    minimum: int | None = None
    maximum: int | None = None

    def fits(self, players: int) -> bool:
        """Чи розрахована пригода на вказану кількість гравців (невідомо — ні)."""
        if self.minimum is None or self.maximum is None:
            return False
        return self.minimum <= players <= self.maximum


@dataclass(frozen=True, slots=True)
class Money:
    """Ціна після нормалізації валют (у джерелі — $, £, EUR)."""

    amount: float | None = None
    currency: str = "USD"

    @property
    def is_free(self) -> bool:
        """Безкоштовна — ціна нульова або відсутня (таблиця А.2, поле is_free)."""
        return self.amount is None or self.amount == 0


def challenge_window(level: int, party_size: int = 4) -> tuple[float, float]:
    """Діапазон рівня небезпеки супротивника для партії (групи запитів 3 і 6).

    Нижня межа — чверть рівня партії: слабші істоти загрожують лише
    великою групою. Верхня — рівень партії + 2 з поправкою на розмір
    партії відносно типових чотирьох гравців. Тим самим правилом генератор
    добирає монстрів пригоди (таблиця 2.13), а пошук — монстрів під партію.
    """
    level = max(1, min(20, level))
    high = level + 2 + (party_size - 4) / 2
    low = level / 4
    return low, max(low, high)


def _canonical_json(values: dict[str, Any]) -> str:
    return json.dumps(values, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _active_values(spec: Any) -> dict[str, Any]:
    """Задані поля специфікації: без None і порожніх переліків;
    значення-переліки впорядковано, тож порядок вибору не змінює ключа."""
    values: dict[str, Any] = {}
    for f in fields(spec):
        value = getattr(spec, f.name)
        if value is None or (isinstance(value, (tuple, list, set, frozenset)) and not value):
            continue
        if isinstance(value, (tuple, list, set, frozenset)):
            value = sorted(value)
        values[f.name] = value
    return values


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

    Поля відповідають фільтрам інтерфейсу (таблиця 4.16): рівень
    персонажів, кількість гравців, тривалість, система, складність, світ,
    теги, безкоштовність, мова видання, рік. Потреба в додаткових книгах
    правил не визначається (таблиця 1.5), тому такого фільтра немає.
    """

    level: int | None = None
    players: int | None = None
    system: str | None = None
    difficulty: int | None = None
    tags: tuple[str, ...] = ()
    setting_id: str | None = None
    is_free: bool | None = None
    max_duration_hours: float | None = None
    publication_language: str | None = None
    year_from: int | None = None
    year_to: int | None = None

    def is_empty(self) -> bool:
        return not _active_values(self)

    def cache_fragment(self) -> str:
        return _canonical_json(_active_values(self))


@dataclass(frozen=True, slots=True)
class MonsterFilters:
    """Обмеження видачі монстрів (група запитів 3).

    Рівень партії перекладається в діапазон рівня небезпеки
    (`challenge_window`); явно задані межі його звужують.
    """

    challenge_min: float | None = None
    challenge_max: float | None = None
    party_level: int | None = None
    creature_type: str | None = None
    environment: str | None = None

    def is_empty(self) -> bool:
        return not _active_values(self)

    def cache_fragment(self) -> str:
        return _canonical_json(_active_values(self))

    def challenge_bounds(self) -> tuple[float | None, float | None]:
        """Підсумкові межі рівня небезпеки з урахуванням рівня партії."""
        low, high = self.challenge_min, self.challenge_max
        if self.party_level is not None:
            party_low, party_high = challenge_window(self.party_level)
            low = party_low if low is None else max(low, party_low)
            high = party_high if high is None else min(high, party_high)
        return low, high


@dataclass(frozen=True, slots=True)
class EncounterFilters:
    """Обмеження видачі зустрічей для пошуку за наративом (таблиця 4.12).

    Зустрічі-викиди тривалістю понад 8 годин за замовчуванням виключено
    (п. 2.5.6): це політика видачі, а не фільтр користувача, тож порожньою
    вважається специфікація, у якій лише вона й задана.
    """

    command: str | None = None
    min_rounds: int | None = None
    max_rounds: int | None = None
    exclude_abandoned: bool = True

    def is_empty(self) -> bool:
        return self.command is None and self.min_rounds is None and self.max_rounds is None and self.exclude_abandoned

    def cache_fragment(self) -> str:
        return _canonical_json(_active_values(self))


@dataclass(frozen=True, slots=True)
class Page:
    """Сторінка видачі: 20 результатів (п. 4.6)."""

    number: int = 1
    size: int = 20

    @property
    def offset(self) -> int:
        return (max(1, self.number) - 1) * self.size


@dataclass(frozen=True, slots=True)
class Snippet:
    """Фрагмент тексту з позиціями збігів для підсвічування.

    Позиції — пари [початок, кінець) у символах `text`; розмітку
    (тег mark) накладає подання, домен про HTML не знає.
    """

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
    #: Кількість знайдених документів за кожним тегом — агрегація в тому
    #: самому запиті, що й пошук (група запитів 2, таблиця 1.2)
    tag_counts: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class QueuedBatch:
    """Пакет документів, отриманий із черги.

    Ідентифікатор потрібен для підтвердження обробки: непідтверджений
    пакет черга доставить повторно.
    """

    batch_id: str
    documents: list[dict] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SourceStats:
    """Звіт джерела про один пакет (п. 4.8, таблиця 4.14).

    Скільки ресурсів запитано, скільки отримано успішно, скільки запитів
    завершилися помилкою чи обмеженням частоти, скільки адрес пропущено
    за robots.txt. Для краулера додано етап обходу та кількість адрес
    етапу, виявлених і оброблених у цьому пакеті, — з них панель показує
    поточний етап. Читання CSV і генератор звітують лише кількістю записів.
    """

    requested: int = 0
    succeeded: int = 0
    failed: int = 0
    throttled: int = 0
    robots_skipped: int = 0
    stage: str | None = None
    discovered: int = 0
    completed: int = 0


@dataclass(frozen=True, slots=True)
class SourceBatch:
    """Пакет від джерела: сирі документи разом зі звітом джерела.

    Лічильники рахуються там, де ресурси завантажуються, а адаптери
    джерел не залежать від інших портів: процес-джерело сам передає
    звіт у MetricsSink.
    """

    documents: list[dict] = field(default_factory=list)
    stats: SourceStats = field(default_factory=SourceStats)


@dataclass(frozen=True, slots=True)
class ShardKey:
    """Ключ розподілу документа між вузлами.

    `value` — номер кошика 0–1023 рядком із чотирьох цифр («0517»), саме
    він зберігається в полі shard_key; `shard_index` — шард, за яким
    закріплено зону з цим кошиком.
    """

    value: str
    shard_index: int


@dataclass(slots=True)
class Provenance:
    """Походження документа та перелік похідних полів."""

    origin: SourceOrigin
    derived_fields: list[str] = field(default_factory=list)

    def mark_derived(self, field_name: str) -> None:
        """Позначає поле як отримане обчисленням або розбором тексту."""
        if field_name not in self.derived_fields:
            self.derived_fields.append(field_name)
