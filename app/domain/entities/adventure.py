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
    SourceOrigin,
)


@dataclass(slots=True)
class Review:
    """Відгук. Вкладена структура, не самостійна сутність (п. 1.4, таблиця А.3).

    Джерело: блок коментарів картки, у середньому 9,6 на пригоду. Власної
    оцінки коментар не містить — оцінка модуля береться з поля Rating.
    """

    review_id: str
    author_name: str
    text: str
    author_profile_url: str | None = None
    #: Дата, обчислена з відносної («12 days ago») відносно часу обходу; точність — до доби
    posted_at: datetime | None = None


@dataclass(slots=True)
class Adventure:
    """Пригода (модуль) — сценарій для однієї чи кількох ігрових сесій.

    Пари level_min/level_max і players_min/players_max та ціну згруповано
    в об'єкти-значення LevelRange, PartySize і Money (таблиця А.2).
    """

    adventure_id: str
    title: str
    #: Обов'язковий лише для згенерованих пригод; у каталозі є в 96% карток
    synopsis: str | None
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
    publication_language: str | None = None
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
    genre: str | None = None

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

        Обов'язкові: title, provenance.origin; synopsis — лише для
        згенерованих пригод (п. 2.11, правило 3).
        """
        reasons: list[str] = []
        if not self.title:
            reasons.append("title: обов'язкове поле")
        if self.provenance is None or self.provenance.origin is None:
            reasons.append("origin: обов'язкове поле")
        elif self.provenance.origin is SourceOrigin.GENERATED and not self.synopsis:
            reasons.append("synopsis: обов'язкове для згенерованої пригоди")
        for name, bounds in (("levels", self.levels), ("party", self.party)):
            if bounds.minimum is not None and bounds.maximum is not None and bounds.minimum > bounds.maximum:
                reasons.append(f"{name}: мінімум більший за максимум")
        if self.difficulty is not None and not 1 <= self.difficulty <= 5:
            reasons.append("difficulty: поза діапазоном 1–5")
        for name in ("author_ids", "monster_ids", "tags"):
            if any(not value for value in getattr(self, name)):
                reasons.append(f"{name}: порожнє значення в переліку")
        return reasons

    @property
    def is_free(self) -> bool:
        """Похідне поле is_free: ціна нульова або відсутня."""
        return self.price.is_free

    def is_playable_by(self, level: int, players: int) -> bool:
        """Чи підходить пригода партії вказаного рівня та розміру.

        Невизначений параметр не є підставою для відмови: запис лишається
        доступним через пошук, але не потрапляє у видачу з фільтром.
        """
        return self.levels.covers(level) and self.party.fits(players)

    def matches(self, filters: AdventureFilters) -> bool:
        """Перевірка відповідності фільтрам на рівні домену.

        Еталон поведінки адаптерів: невідоме значення не задовольняє
        фільтр за цим полем (таблиця 1.5).
        """
        if filters.level is not None and not self.levels.covers(filters.level):
            return False
        if filters.players is not None and not self.party.fits(filters.players):
            return False
        if filters.system is not None and self.system != filters.system:
            return False
        if filters.difficulty is not None and self.difficulty != filters.difficulty:
            return False
        if filters.tags and not set(filters.tags) <= set(self.tags):
            return False
        if filters.setting_id is not None and self.setting_id != filters.setting_id:
            return False
        if filters.is_free is not None and self.is_free != filters.is_free:
            return False
        if filters.max_duration_hours is not None and (
            self.duration_hours is None or self.duration_hours > filters.max_duration_hours
        ):
            return False
        if filters.publication_language is not None and self.publication_language != filters.publication_language:
            return False
        if filters.year_from is not None and (self.published_year is None or self.published_year < filters.year_from):
            return False
        if filters.year_to is not None and (self.published_year is None or self.published_year > filters.year_to):
            return False
        return True

    def reviews_count(self) -> int:
        """Похідне поле reviews_count — довжина вкладеного масиву відгуків."""
        return len(self.reviews)

    def shares_monsters_with(self, other: Adventure) -> set[str]:
        """Спільні монстри — основа рекомендації «схожі пригоди»."""
        return set(self.monster_ids) & set(other.monster_ids)

    def can_follow(self, previous: Adventure) -> bool:
        """Чи може пригода продовжити ланцюжок кампанії.

        Умови (таблиця 2.13): той самий світ, неперервні рівні — level_min
        наступної дорівнює level_max попередньої або на 1 більший — і жодного
        спільного монстра.
        """
        if self.setting_id is None or self.setting_id != previous.setting_id:
            return False
        if previous.levels.maximum is None or self.levels.minimum is None:
            return False
        if self.levels.minimum - previous.levels.maximum not in (0, 1):
            return False
        return not self.shares_monsters_with(previous)
