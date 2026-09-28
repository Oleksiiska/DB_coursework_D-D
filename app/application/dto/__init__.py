"""Об'єкти передачі даних між шарами.

DTO відрізняються від сутностей навмисно: за CQRS модель читання є зрізом
під конкретну видачу й не містить повного тексту пригоди.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Generic, TypeVar

from app.domain.values import (
    AdventureFilters,
    Page,
    SearchSpecification,
    Snippet,
    SortOrder,
)

TCard = TypeVar("TCard")

# --------------------------------------------------------------------------
# Вхідні DTO
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SearchQueryDTO:
    """Пошуковий запит. Спільний для пошуку пригод, монстрів і зустрічей;
    вид фільтрів визначає обробник."""

    raw_text: str
    filters: SearchSpecification = field(default_factory=AdventureFilters)
    page: Page = field(default_factory=Page)
    sort: SortOrder = SortOrder.RELEVANCE

    @classmethod
    def from_request(cls, params: dict) -> "SearchQueryDTO":
        """Розбір параметрів HTTP-запиту."""
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class PlayStatisticsQueryDTO:
    """Запит статистики реальної гри (група запитів 7)."""

    char_class: str | None = None
    level: int | None = None
    limit: int = 10


@dataclass(frozen=True, slots=True)
class CampaignQueryDTO:
    """Запит ланцюжка кампанії (група запитів 6)."""

    setting_id: str
    level_from: int = 1
    level_to: int = 10


@dataclass(frozen=True, slots=True)
class RateSettingDTO:
    """Цільова швидкість генерації, керована слайдером інтерфейсу."""

    documents_per_second: int
    source_name: str = "generator"

    MIN_RATE: int = 10
    MAX_RATE: int = 5000

    def validate(self) -> None:
        raise NotImplementedError


# --------------------------------------------------------------------------
# Вихідні DTO: картки
# --------------------------------------------------------------------------


@dataclass(slots=True)
class AdventureCardDTO:
    """Картка пригоди. Без повного тексту — він потрібен лише індексу."""

    adventure_id: str
    title: str
    synopsis: str
    hook: str | None = None
    system: str | None = None
    level_min: int | None = None
    level_max: int | None = None
    players_min: int | None = None
    players_max: int | None = None
    duration_hours: float | None = None
    authors: list[str] = field(default_factory=list)
    setting_name: str | None = None
    tags: list[str] = field(default_factory=list)
    monster_names: list[str] = field(default_factory=list)
    price: float | None = None
    is_free: bool = False
    rating_avg: float | None = None
    ratings_count: int | None = None
    reviews_count: int = 0
    cover_image_url: str | None = None
    source_url: str | None = None
    snippets: list[Snippet] = field(default_factory=list)

    @classmethod
    def from_entity(cls, entity, snippets: list[Snippet] | None = None) -> "AdventureCardDTO":
        raise NotImplementedError


@dataclass(slots=True)
class MonsterCardDTO:
    monster_id: str
    name: str
    type: str
    challenge_rating: float
    size: str | None = None
    alignment: str | None = None
    environment: list[str] = field(default_factory=list)
    lore: str | None = None
    image_url: str | None = None
    play_count: int = 0
    statblock: dict = field(default_factory=dict)
    appears_in: list[str] = field(default_factory=list)
    snippets: list[Snippet] = field(default_factory=list)

    @classmethod
    def from_entity(cls, entity, snippets: list[Snippet] | None = None) -> "MonsterCardDTO":
        raise NotImplementedError


@dataclass(slots=True)
class EncounterCardDTO:
    """Картка реальної зустрічі — результат пошуку за наративом."""

    encounter_id: str
    started_at: datetime
    duration_minutes: float | None = None
    rounds_count: int | None = None
    participants_count: int | None = None
    monster_names: list[str] = field(default_factory=list)
    narrative_excerpt: str | None = None
    snippets: list[Snippet] = field(default_factory=list)

    @classmethod
    def from_entity(cls, entity, snippets: list[Snippet] | None = None) -> "EncounterCardDTO":
        raise NotImplementedError


@dataclass(slots=True)
class CharacterCardDTO:
    """Картка персонажа гравця з реальної гри (група запитів 4)."""

    character_id: str
    name: str
    race: str | None = None
    char_class: str | None = None
    level: int | None = None
    is_multiclass: bool = False
    spells: list[str] = field(default_factory=list)
    encounters_count: int = 0

    @classmethod
    def from_entity(cls, entity) -> "CharacterCardDTO":
        raise NotImplementedError


@dataclass(slots=True)
class AuthorCardDTO:
    """Картка автора: інші роботи та співавтори (група запитів 5)."""

    author_id: str
    name: str
    bio: str | None = None
    profile_url: str | None = None
    avg_rating: float | None = None
    adventures: list[AdventureCardDTO] = field(default_factory=list)
    co_authors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RecommendationDTO:
    """Рекомендація з поясненням підстави — потрібне для панелі."""

    adventure_id: str
    title: str
    score: float
    shared_tags: list[str] = field(default_factory=list)
    shared_monsters: list[str] = field(default_factory=list)
    reason: str | None = None


# --------------------------------------------------------------------------
# Вихідні DTO: результати та статистика
# --------------------------------------------------------------------------


@dataclass(slots=True)
class SearchResultDTO(Generic[TCard]):
    """Результат пошуку. Параметризований типом картки."""

    items: list[TCard] = field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 20
    cache_hit: bool = False
    latency_ms: int = 0
    normalized_query: str = ""


@dataclass(slots=True)
class PlayStatisticsDTO:
    """Статистика реальної гри. Медіани — через викиди в розподілах."""

    popular_monsters: list[tuple[str, int]] = field(default_factory=list)
    typical_spells: list[tuple[str, int]] = field(default_factory=list)
    class_race: list[tuple[str, str, int]] = field(default_factory=list)
    party_composition: list[tuple[str, int]] = field(default_factory=list)
    median_duration_minutes: float | None = None
    median_rounds: float | None = None


@dataclass(slots=True)
class IngestReport:
    """Підсумок обробки пакета на шляху запису."""

    source_name: str
    received: int = 0
    saved: int = 0
    rejected: int = 0
    indexed: int = 0
    graph_edges: int = 0
    unknown_terms: list[str] = field(default_factory=list)
    duration_ms: int = 0

    @property
    def throughput(self) -> float:
        """Документів за секунду на цьому пакеті."""
        raise NotImplementedError


@dataclass(slots=True)
class QueryLogItemDTO:
    """Рядок стрічки останніх запитів на панелі моніторингу."""

    raw_query: str
    status: str
    results_count: int
    latency_ms: int
    cache_hit: bool
    created_at: datetime


@dataclass(slots=True)
class LiveStatsDTO:
    """Зріз стану системи, що віддається потоком раз на секунду."""

    documents_saved: int = 0
    documents_rejected: int = 0
    write_rps: float = 0.0
    search_rps: float = 0.0
    queue_depth: int = 0
    cache_hit_ratio: float = 0.0
    avg_search_latency_ms: float = 0.0
    target_rate: int = 0
    by_origin: dict[str, int] = field(default_factory=dict)
    recent_queries: list[QueryLogItemDTO] = field(default_factory=list)


__all__ = [
    "AdventureCardDTO",
    "AuthorCardDTO",
    "CampaignQueryDTO",
    "CharacterCardDTO",
    "EncounterCardDTO",
    "IngestReport",
    "LiveStatsDTO",
    "MonsterCardDTO",
    "PlayStatisticsDTO",
    "PlayStatisticsQueryDTO",
    "QueryLogItemDTO",
    "RateSettingDTO",
    "RecommendationDTO",
    "SearchQueryDTO",
    "SearchResultDTO",
]
