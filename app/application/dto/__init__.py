"""Об'єкти передачі даних між шарами.

DTO відрізняються від сутностей навмисно: за CQRS модель читання є зрізом
під конкретну видачу й не містить повного тексту пригоди.
"""

from __future__ import annotations

import dataclasses
import enum
import types
import typing
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar, Generic, TypeVar

from app.domain.errors import ValidationError
from app.domain.values import (
    AdventureFilters,
    EncounterFilters,
    MonsterFilters,
    Page,
    SearchSpecification,
    Snippet,
    SortOrder,
)

TCard = TypeVar("TCard")

#: Вид пошуку в маршруті /api/search/{kind} → тип фільтрів (таблиця 4.15)
SEARCH_FILTERS: dict[str, type] = {
    "adventures": AdventureFilters,
    "monsters": MonsterFilters,
    "encounters": EncounterFilters,
}

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
        """Розбір параметрів HTTP-запиту: kind (adventures, monsters,
        encounters), q, sort, page і фільтри цього виду пошуку.

        Значення приводяться до типів полів фільтра; помилка формату —
        ValidationError, тобто відповідь 422, а не збій сценарію.
        """
        kind = params.get("kind", "adventures")
        if kind not in SEARCH_FILTERS:
            raise ValidationError("search", [f"kind: невідомий вид пошуку {kind!r}"])
        filters_type = SEARCH_FILTERS[kind]
        hints = typing.get_type_hints(filters_type)
        values: dict[str, Any] = {}
        reasons: list[str] = []
        for f in dataclasses.fields(filters_type):
            raw = params.get(f.name)
            if raw is None or raw == "" or raw == []:
                continue
            try:
                values[f.name] = _parse_value(raw, hints[f.name])
            except (TypeError, ValueError):
                reasons.append(f"{f.name}: невірне значення {raw!r}")
        try:
            sort = SortOrder(params.get("sort") or SortOrder.RELEVANCE.value)
        except ValueError:
            reasons.append(f"sort: невідомий порядок {params.get('sort')!r}")
            sort = SortOrder.RELEVANCE
        try:
            page = Page(number=max(1, int(params.get("page") or 1)))
        except (TypeError, ValueError):
            reasons.append(f"page: невірне значення {params.get('page')!r}")
            page = Page()
        if reasons:
            raise ValidationError("search", reasons)
        return cls(raw_text=str(params.get("q") or ""), filters=filters_type(**values), page=page, sort=sort)


@dataclass(frozen=True, slots=True)
class PlayStatisticsQueryDTO:
    """Запит статистики реальної гри (група запитів 7).

    `report` — розділ статистики з маршруту /api/statistics/{report}:
    monsters — найпопулярніші монстри, spells — типові закляття класу й
    рівня, party — розподіл класів і рас і склад партії, combat — медіани
    тривалості й раундів бою.
    """

    report: str = "monsters"
    char_class: str | None = None
    level: int | None = None
    limit: int = 10

    REPORTS: ClassVar[tuple[str, ...]] = ("monsters", "spells", "party", "combat")

    def validate(self) -> None:
        reasons: list[str] = []
        if self.report not in self.REPORTS:
            reasons.append(f"report: невідомий розділ {self.report!r}")
        if self.report == "spells" and (not self.char_class or self.level is None):
            reasons.append("spells: потрібні клас і рівень персонажа")
        if not 1 <= self.limit <= 100:
            reasons.append("limit: від 1 до 100")
        if reasons:
            raise ValidationError("statistics", reasons)


@dataclass(frozen=True, slots=True)
class CampaignQueryDTO:
    """Запит ланцюжка кампанії (група запитів 6).

    З маршруту /api/adventures/{id}/chain приходить пригода, через яку має
    пройти ланцюжок, — світ береться з неї; без пригоди потрібен світ.
    """

    adventure_id: str | None = None
    setting_id: str | None = None
    level_from: int = 1
    level_to: int = 10

    def validate(self) -> None:
        reasons: list[str] = []
        if not self.adventure_id and not self.setting_id:
            reasons.append("потрібна пригода або ігровий світ")
        if not 1 <= self.level_from <= self.level_to <= 20:
            reasons.append("рівні: 1 ≤ level_from ≤ level_to ≤ 20")
        if reasons:
            raise ValidationError("campaign", reasons)


@dataclass(frozen=True, slots=True)
class RateSettingDTO:
    """Цільова сумарна швидкість генерації, керована слайдером інтерфейсу."""

    documents_per_second: int
    source_name: str = "generator"

    MIN_RATE: ClassVar[int] = 10
    MAX_RATE: ClassVar[int] = 5000

    def validate(self) -> None:
        if not self.MIN_RATE <= self.documents_per_second <= self.MAX_RATE:
            raise ValidationError(
                "rate", [f"швидкість має бути від {self.MIN_RATE} до {self.MAX_RATE} документів за секунду"]
            )


# --------------------------------------------------------------------------
# Вихідні DTO: картки
# --------------------------------------------------------------------------


@dataclass(slots=True)
class ReviewDTO:
    author_name: str
    text: str
    author_profile_url: str | None = None
    posted_at: datetime | None = None


@dataclass(slots=True)
class AdventureCardDTO:
    """Картка пригоди. Без повного тексту — він потрібен лише індексу.

    Автори, монстри й світ подано ключами: їхні картки відкриваються
    окремими маршрутами (таблиця 4.15), а назви світів — зі списку вручну.
    """

    adventure_id: str
    title: str
    synopsis: str | None = None
    hook: str | None = None
    system: str | None = None
    edition: str | None = None
    publication_language: str | None = None
    genre: str | None = None
    level_min: int | None = None
    level_max: int | None = None
    players_min: int | None = None
    players_max: int | None = None
    duration_hours: float | None = None
    session_count: int | None = None
    difficulty: int | None = None
    published_year: int | None = None
    page_count: int | None = None
    publisher: str | None = None
    required_books: list[str] = field(default_factory=list)
    author_ids: list[str] = field(default_factory=list)
    setting_id: str | None = None
    tags: list[str] = field(default_factory=list)
    monster_ids: list[str] = field(default_factory=list)
    location_names: list[str] = field(default_factory=list)
    prev_adventure_id: str | None = None
    next_adventure_id: str | None = None
    price: float | None = None
    is_free: bool = False
    rating_avg: float | None = None
    ratings_count: int | None = None
    reviews_count: int = 0
    reviews: list[ReviewDTO] = field(default_factory=list)
    cover_image_url: str | None = None
    source_url: str | None = None
    similar_urls: list[str] = field(default_factory=list)
    status: str | None = None
    origin: str | None = None
    snippets: list[Snippet] = field(default_factory=list)

    @classmethod
    def from_entity(cls, entity, snippets: list[Snippet] | None = None, with_reviews: bool = False) -> "AdventureCardDTO":
        """Зріз пригоди під видачу; відгуки — лише в повній картці."""
        return cls(
            adventure_id=entity.adventure_id,
            title=entity.title,
            synopsis=entity.synopsis,
            hook=entity.hook,
            system=entity.system,
            edition=entity.edition,
            publication_language=entity.publication_language,
            genre=entity.genre,
            level_min=entity.levels.minimum,
            level_max=entity.levels.maximum,
            players_min=entity.party.minimum,
            players_max=entity.party.maximum,
            duration_hours=entity.duration_hours,
            session_count=entity.session_count,
            difficulty=entity.difficulty,
            published_year=entity.published_year,
            page_count=entity.page_count,
            publisher=entity.publisher,
            required_books=list(entity.required_books),
            author_ids=list(entity.author_ids),
            setting_id=entity.setting_id,
            tags=list(entity.tags),
            monster_ids=list(entity.monster_ids),
            location_names=list(entity.location_names),
            prev_adventure_id=entity.prev_adventure_id,
            next_adventure_id=entity.next_adventure_id,
            price=entity.price.amount,
            is_free=entity.is_free,
            rating_avg=entity.rating_avg,
            ratings_count=entity.ratings_count,
            reviews_count=entity.reviews_count(),
            reviews=[
                ReviewDTO(r.author_name, r.text, r.author_profile_url, r.posted_at) for r in entity.reviews
            ] if with_reviews else [],
            cover_image_url=entity.cover_image_url,
            source_url=entity.source_url,
            similar_urls=list(entity.similar_urls),
            status=entity.status,
            origin=entity.provenance.origin.value,
            snippets=list(snippets or []),
        )


@dataclass(slots=True)
class MonsterCardDTO:
    monster_id: str
    name: str
    type: str
    challenge_rating: float
    subtype: str | None = None
    size: str | None = None
    xp: int | None = None
    alignment: str | None = None
    environment: list[str] = field(default_factory=list)
    lore: str | None = None
    image_url: str | None = None
    play_count: int = 0
    statblock: dict = field(default_factory=dict)
    #: Пригоди, де зустрічається монстр: пари (adventure_id, title) з графа
    appears_in: list[tuple[str, str]] = field(default_factory=list)
    #: Монстри, що найчастіше трапляються з цим у тих самих реальних боях:
    #: (monster_id, назва, кількість спільних боїв) — група запитів 8
    seen_together: list[tuple[str, str, int]] = field(default_factory=list)
    snippets: list[Snippet] = field(default_factory=list)

    @classmethod
    def from_entity(cls, entity, snippets: list[Snippet] | None = None) -> "MonsterCardDTO":
        return cls(
            monster_id=entity.monster_id,
            name=entity.name,
            type=entity.type,
            challenge_rating=entity.challenge_rating,
            subtype=entity.subtype,
            size=entity.size,
            xp=entity.xp,
            alignment=entity.alignment,
            environment=list(entity.environment),
            lore=entity.lore,
            image_url=entity.image_url,
            play_count=entity.play_count,
            statblock=dto_to_dict(entity.statblock),
            snippets=list(snippets or []),
        )


@dataclass(slots=True)
class EncounterCardDTO:
    """Картка реальної зустрічі — результат пошуку за наративом."""

    encounter_id: str
    started_at: datetime
    ended_at: datetime | None = None
    duration_minutes: float | None = None
    rounds_count: int | None = None
    participants_count: int | None = None
    commands: list[str] = field(default_factory=list)
    is_abandoned: bool = False
    player_character_ids: list[str] = field(default_factory=list)
    narrative_excerpt: str | None = None
    snippets: list[Snippet] = field(default_factory=list)

    EXCERPT_LENGTH: ClassVar[int] = 300

    @classmethod
    def from_entity(cls, entity, snippets: list[Snippet] | None = None) -> "EncounterCardDTO":
        narrative = entity.narrative_text or ""
        excerpt = narrative[: cls.EXCERPT_LENGTH] + ("…" if len(narrative) > cls.EXCERPT_LENGTH else "")
        return cls(
            encounter_id=entity.encounter_id,
            started_at=entity.started_at,
            ended_at=entity.ended_at,
            duration_minutes=None if entity.duration_seconds is None else round(entity.duration_seconds / 60, 1),
            rounds_count=entity.rounds_count,
            participants_count=entity.participants_count,
            commands=sorted(set(entity.commands)),
            is_abandoned=entity.is_abandoned,
            player_character_ids=list(entity.player_character_ids),
            narrative_excerpt=excerpt or None,
            snippets=list(snippets or []),
        )


@dataclass(slots=True)
class CharacterCardDTO:
    """Картка персонажа гравця з реальної гри (група запитів 4)."""

    character_id: str
    name: str
    race: str | None = None
    char_class: str | None = None
    level: int | None = None
    is_multiclass: bool = False
    classes: list[tuple[str, int]] = field(default_factory=list)
    hp_current: int | None = None
    hp_max: int | None = None
    hp_status: str | None = None
    spells: list[str] = field(default_factory=list)
    attacks: list[str] = field(default_factory=list)
    encounter_ids: list[str] = field(default_factory=list)
    encounters_count: int = 0

    @classmethod
    def from_entity(cls, entity) -> "CharacterCardDTO":
        return cls(
            character_id=entity.character_id,
            name=entity.name,
            race=entity.race,
            char_class=entity.char_class,
            level=entity.level,
            is_multiclass=entity.is_multiclass,
            classes=[(c.char_class, c.level) for c in entity.classes_all],
            hp_current=entity.hp.current,
            hp_max=entity.hp.maximum,
            hp_status=entity.hp.status.value if entity.hp.status else None,
            spells=list(entity.spells),
            attacks=list(entity.combat_payload.attacks),
            encounter_ids=list(entity.encounter_ids),
            encounters_count=len(entity.encounter_ids),
        )


@dataclass(slots=True)
class AuthorCardDTO:
    """Картка автора: інші роботи та співавтори (група запитів 5)."""

    author_id: str
    name: str
    bio: str | None = None
    profile_url: str | None = None
    platform: str | None = None
    avg_rating: float | None = None
    adventures_count: int = 0
    first_published_year: int | None = None
    origin: str | None = None
    adventures: list[AdventureCardDTO] = field(default_factory=list)
    #: Співавтори: пари (author_id, ім'я)
    co_authors: list[tuple[str, str]] = field(default_factory=list)


@dataclass(slots=True)
class RecommendationDTO:
    """Рекомендація з поясненням підстави — потрібне для панелі."""

    adventure_id: str
    title: str
    score: float
    shared_tags: list[str] = field(default_factory=list)
    shared_monsters: list[str] = field(default_factory=list)
    reason: str | None = None
    level_min: int | None = None
    level_max: int | None = None


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
    #: Кількість пригод за кожним тегом у видачі — насиченість тем
    #: (група запитів 2, таблиця 1.2); для монстрів і зустрічей порожня
    tag_counts: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class PlayStatisticsDTO:
    """Статистика реальної гри. Медіани — через викиди в розподілах."""

    report: str = "monsters"
    #: (monster_id, назва, кількість появ у зустрічах)
    popular_monsters: list[tuple[str, str, int]] = field(default_factory=list)
    typical_spells: list[tuple[str, int]] = field(default_factory=list)
    class_race: list[tuple[str, str, int]] = field(default_factory=list)
    party_composition: list[tuple[str, int]] = field(default_factory=list)
    median_duration_minutes: float | None = None
    median_rounds: float | None = None
    char_class: str | None = None
    level: int | None = None


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
        return self.saved * 1000 / self.duration_ms if self.duration_ms > 0 else float(self.saved)


@dataclass(slots=True)
class QueryLogItemDTO:
    """Рядок стрічки останніх запитів на панелі моніторингу."""

    raw_query: str
    kind: str
    status: str
    results_count: int
    latency_ms: int
    cache_hit: bool
    created_at: datetime


@dataclass(slots=True)
class LiveStatsDTO:
    """Зріз стану системи, що віддається потоком раз на секунду (таблиця 4.16).

    `counters` — підсумки лічильників таблиці 4.14, `rates` — їхня середня
    швидкість за останні 5 с; `shards` — кількість документів пригод на
    кожному шарді з метаданих колекції; `fill` — частка пригод із заповненим
    полем окремо для зібраних і згенерованих.
    """

    generated_at: datetime
    counters: dict[str, int] = field(default_factory=dict)
    rates: dict[str, float] = field(default_factory=dict)
    target_rate: int = 0
    generator_instances: int = 0
    queue_depth: int = 0
    queue_limit: int = 50
    shards: dict[str, int] = field(default_factory=dict)
    adventures_total: int = 0
    cache_hit_ratio: float | None = None
    latency_p50_ms: float | None = None
    latency_p95_ms: float | None = None
    recent_queries: list[QueryLogItemDTO] = field(default_factory=list)
    fill: dict[str, dict[str, float | None]] = field(default_factory=dict)
    crawler_stage: dict[str, Any] | None = None

    #: Поля пригод із неповним покриттям у каталозі (таблиця А.2), для яких
    #: панель показує заповненість окремо для зібраних і згенерованих
    FILL_FIELDS: ClassVar[tuple[str, ...]] = (
        "level_min", "players_min", "duration_hours", "price", "publication_language",
        "published_year", "synopsis", "system", "genre", "rating_avg", "reviews",
    )


# --------------------------------------------------------------------------
# Перетворення DTO на JSON-сумісні значення й назад — для кешу й транспорту
# --------------------------------------------------------------------------


def dto_to_dict(value: Any) -> Any:
    """DTO (або вкладене значення) → словники, списки й рядки ISO для дат."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: dto_to_dict(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (list, tuple, set, frozenset)):
        return [dto_to_dict(item) for item in value]
    if isinstance(value, dict):
        return {str(k): dto_to_dict(v) for k, v in value.items()}
    return value


def dto_from_dict(cls: type, data: dict[str, Any]) -> Any:
    """Відновлення DTO з результату `dto_to_dict` за анотаціями полів."""
    return _restore(data, cls)


def _restore(value: Any, hint: Any) -> Any:
    if value is None or hint is Any:
        return value
    origin = typing.get_origin(hint)
    if origin in (typing.Union, types.UnionType):
        options = [a for a in typing.get_args(hint) if a is not type(None)]
        return _restore(value, options[0]) if len(options) == 1 else value
    if isinstance(hint, type) and dataclasses.is_dataclass(hint):
        hints = typing.get_type_hints(hint)
        names = {f.name for f in dataclasses.fields(hint) if f.init}
        return hint(**{k: _restore(v, hints[k]) for k, v in value.items() if k in names})
    if isinstance(hint, type) and issubclass(hint, enum.Enum):
        return hint(value)
    if hint is datetime and isinstance(value, str):
        return datetime.fromisoformat(value)
    if origin in (list, set, frozenset):
        (item,) = typing.get_args(hint) or (Any,)
        return origin(_restore(v, item) for v in value)
    if origin is tuple:
        args = typing.get_args(hint)
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_restore(v, args[0]) for v in value)
        return tuple(_restore(v, a) for v, a in zip(value, args))
    if origin is dict:
        _, item = typing.get_args(hint) or (Any, Any)
        return {k: _restore(v, item) for k, v in value.items()}
    return value


def _parse_value(raw: Any, hint: Any) -> Any:
    """Значення параметра запиту → тип поля фільтра."""
    origin = typing.get_origin(hint)
    if origin in (typing.Union, types.UnionType):
        options = [a for a in typing.get_args(hint) if a is not type(None)]
        hint = options[0]
        origin = typing.get_origin(hint)
    if origin is tuple:
        items = raw if isinstance(raw, (list, tuple)) else str(raw).split(",")
        return tuple(sorted({str(item).strip() for item in items if str(item).strip()}))
    if hint is bool:
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in ("1", "true", "yes", "так"):
            return True
        if text in ("0", "false", "no", "ні"):
            return False
        raise ValueError(raw)
    if hint is int:
        return int(raw)
    if hint is float:
        return float(raw)
    return str(raw).strip()


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
    "ReviewDTO",
    "SEARCH_FILTERS",
    "SearchQueryDTO",
    "SearchResultDTO",
    "dto_from_dict",
    "dto_to_dict",
]
