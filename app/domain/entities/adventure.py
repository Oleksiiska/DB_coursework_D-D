"""Сутність «Пригода» — центральна сутність каталогу (п. 1.4, додаток А, таблиця А.2)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.domain.values import (
    AdventureFilters,
    LevelRange,
    Money,
    PartySize,
    Provenance,
)


@dataclass(slots=True)
class Review:
    """Відгук. Вкладена структура, не самостійна сутність (п. 1.4).

    Джерело: блок коментарів картки, у середньому 9,6 на пригоду.
    """

    review_id: str
    author_name: str
    text: str
    author_profile_url: str | None = None
    posted_relative: str | None = None


@dataclass(slots=True)
class Adventure:
    """Пригода (модуль) — сценарій для однієї чи кількох ігрових сесій."""

    adventure_id: str
    title: str
    synopsis: str
    provenance: Provenance

    slug: str | None = None
    itch_id: str | None = None

    # Тексти
    hook: str | None = None
    full_text: str | None = None
    location_names: list[str] = field(default_factory=list)

    # Ігрові параметри
    system: str | None = None
    edition: str | None = None
    language: str | None = None
    levels: LevelRange = field(default_factory=LevelRange)
    party: PartySize = field(default_factory=PartySize)
    duration_hours: float | None = None
    session_count: int | None = None
    difficulty: int | None = None
    required_books: list[str] = field(default_factory=list)

    # Видання
    published_year: int | None = None
    page_count: int | None = None
    publisher: str | None = None
    price: Money = field(default_factory=Money)
    cover_image_url: str | None = None
    source_url: str | None = None
    status: str | None = None

    # Зв'язки
    author_ids: list[str] = field(default_factory=list)
    setting_id: str | None = None
    tags: list[str] = field(default_factory=list)
    monster_ids: list[str] = field(default_factory=list)
    prev_adventure_id: str | None = None
    next_adventure_id: str | None = None
    similar_urls: list[str] = field(default_factory=list)

    # Оцінки та відгуки
    rating_avg: float | None = None
    ratings_count: int | None = None
    reviews: list[Review] = field(default_factory=list)

    # Технічні
    shard_key: str | None = None
    created_at: datetime | None = None
    indexed_at: datetime | None = None

    # --- інваріанти та доменні правила ---

    def validate(self) -> list[str]:
        """Перелік порушених інваріантів; порожній — документ валідний.

        Обов'язкові: title, synopsis, provenance.origin (п. 2.11, правило 3).
        """
        raise NotImplementedError

    def is_playable_by(self, level: int, players: int) -> bool:
        """Чи підходить пригода партії вказаного рівня та розміру.

        Невизначений параметр не є підставою для відмови: запис лишається
        доступним через пошук, але не потрапляє у видачу з фільтром.
        """
        raise NotImplementedError

    def matches(self, filters: AdventureFilters) -> bool:
        """Перевірка відповідності фільтрам на рівні домену.

        Використовується у модульних тестах як еталон поведінки адаптера.
        """
        raise NotImplementedError

    def reviews_count(self) -> int:
        raise NotImplementedError

    def shares_monsters_with(self, other: Adventure) -> set[str]:
        """Спільні монстри — основа рекомендації «схожі пригоди»."""
        raise NotImplementedError

    def can_follow(self, previous: Adventure) -> bool:
        """Чи може пригода продовжити ланцюжок кампанії.

        Умови: той самий світ і суміжні діапазони рівнів.
        """
        raise NotImplementedError
