"""Вихідні порти — залежності ядра від зовнішнього світу.

Оголошені як `Protocol`: структурна типізація дозволяє адаптеру
задовольняти контракт без успадкування, тобто infrastructure
не імпортує domain навіть заради базового класу.
"""

from __future__ import annotations

from typing import AsyncIterator, Protocol, Sequence, TypeVar, runtime_checkable

from app.domain.entities import Adventure, Encounter, Monster, PlayerCharacter, SearchLogEntry
from app.domain.values import Filters, Page, ShardKey, Snippet

T = TypeVar("T")


# --------------------------------------------------------------------------
# Узагальнений CRUD-репозиторій
# --------------------------------------------------------------------------


@runtime_checkable
class CrudRepository(Protocol[T]):
    """Базовий контракт сховища агрегату.

    Узагальнений, щоб репозиторії окремих сутностей не дублювали
    однакові операції. Реалізація — один адаптер документного сховища,
    параметризований колекцією та відображенням документа в сутність.
    """

    async def get(self, entity_id: str) -> T | None:
        """Читання за ключем. Основний шлях картки об'єкта."""
        ...

    async def get_many(self, entity_ids: Sequence[str]) -> list[T]:
        """Пакетне читання. Використовується після пошуку в індексі."""
        ...

    async def add(self, entity: T) -> None:
        ...

    async def add_many(self, entities: Sequence[T]) -> int:
        """Пакетний запис. Основний чинник пропускної здатності."""
        ...

    async def update(self, entity: T) -> bool:
        ...

    async def delete(self, entity_id: str) -> bool:
        ...

    async def count(self, filters: Filters | None = None) -> int:
        ...


@runtime_checkable
class AdventureRepository(CrudRepository[Adventure], Protocol):
    """Репозиторій пригод — шардована колекція."""

    async def find_by_filters(self, filters: Filters, page: Page) -> list[Adventure]:
        ...

    async def find_by_author(self, author_id: str) -> list[Adventure]:
        ...

    async def find_chain(self, setting_id: str, level_from: int, level_to: int) -> list[Adventure]:
        """Ланцюжок пригод у межах одного світу (запит групи 6)."""
        ...


@runtime_checkable
class MonsterRepository(CrudRepository[Monster], Protocol):
    async def find_by_challenge_rating(self, minimum: float, maximum: float) -> list[Monster]:
        ...

    async def most_played(self, limit: int = 20) -> list[Monster]:
        """Найпопулярніші монстри реальних ігор (запит групи 7)."""
        ...

    async def bump_play_counts(self, counts: dict[str, int]) -> None:
        """Оновлення лічильника появ у зустрічах."""
        ...


@runtime_checkable
class CharacterRepository(CrudRepository[PlayerCharacter], Protocol):
    async def typical_spells(self, char_class: str, level: int, limit: int = 10) -> list[tuple[str, int]]:
        """Типові закляття класу — агрегація по вкладеному масиву."""
        ...

    async def class_race_distribution(self) -> dict[tuple[str, str], int]:
        ...


@runtime_checkable
class EncounterRepository(CrudRepository[Encounter], Protocol):
    async def median_duration(self, exclude_abandoned: bool = True) -> float:
        """Медіана, а не середнє: розподіл містить викиди ."""
        ...

    async def median_rounds(self, exclude_abandoned: bool = True) -> float:
        ...


# --------------------------------------------------------------------------
# Пошук, граф, кеш
# --------------------------------------------------------------------------


@runtime_checkable
class SearchIndex(Protocol):
    """Порт повнотекстового пошуку."""

    async def search(
        self,
        text: str,
        filters: Filters,
        page: Page,
    ) -> tuple[list[str], dict[str, list[Snippet]], int]:
        """Повертає ідентифікатори, фрагменти з підсвічуванням і загальну кількість.

        Повні документи читаються окремо з документного сховища:
        індекс зберігає лише поля, потрібні для пошуку й видачі.
        """
        ...

    async def index_many(self, documents: Sequence[dict]) -> int:
        ...

    async def delete(self, document_id: str) -> bool:
        ...


@runtime_checkable
class GraphRepository(Protocol):
    """Порт обходу зв'язків."""

    async def similar_adventures(self, adventure_id: str, limit: int = 10) -> list[str]:
        """Рекомендації за спільними тегами й монстрами (запит групи 8)."""
        ...

    async def adventures_with_monster(self, monster_id: str) -> list[str]:
        """Зворотний пошук: у яких пригодах зустрічається монстр."""
        ...

    async def co_authors(self, author_id: str) -> list[str]:
        ...

    async def campaign_chain(self, setting_id: str, level_from: int, level_to: int) -> list[str]:
        """Послідовність пригод без повторення ключових монстрів."""
        ...

    async def merge_nodes_and_edges(self, batch: Sequence[dict]) -> int:
        """Ідемпотентне оновлення вузлів і ребер пакетом."""
        ...


@runtime_checkable
class CacheStore(Protocol):
    """Порт кешу та спільного стану між процесами."""

    async def get(self, key: str) -> bytes | None:
        ...

    async def set(self, key: str, value: bytes, ttl_seconds: int | None = None) -> None:
        ...

    async def delete(self, key: str) -> bool:
        ...

    async def incr(self, key: str, amount: int = 1) -> int:
        ...

    async def push_capped(self, key: str, value: bytes, max_length: int) -> None:
        """Черга останніх N елементів — стрічка запитів на панелі."""
        ...


# --------------------------------------------------------------------------
# Джерела, метрики, події
# --------------------------------------------------------------------------


@runtime_checkable
class DocumentSource(Protocol):
    """Спільний контракт усіх джерел документів.

    Його реалізують читання CSV, генератор і краулер. Конвеєр обробки
    не розрізняє, звідки прийшов документ.
    """

    @property
    def name(self) -> str:
        ...

    async def stream(self, batch_size: int = 500) -> AsyncIterator[list[dict]]:
        """Асинхронний потік пакетів сирих документів."""
        ...

    async def set_rate(self, documents_per_second: int) -> None:
        """Цільова швидкість. Для CSV і краулера — верхня межа."""
        ...


@runtime_checkable
class MetricsSink(Protocol):
    """Порт лічильників і журналу запитів."""

    async def record_query(self, entry: SearchLogEntry) -> None:
        ...

    async def inc(self, counter: str, amount: int = 1) -> None:
        ...

    async def observe_latency(self, operation: str, milliseconds: int) -> None:
        ...

    async def snapshot(self) -> dict:
        """Зріз для панелі моніторингу."""
        ...


@runtime_checkable
class EventPublisher(Protocol):
    """Порт публікації пакетів у чергу (п. 4.6.1)."""

    async def publish(self, topic: str, batch: Sequence[dict]) -> None:
        ...

    async def queue_depth(self, topic: str) -> int:
        """Глибина черги робить вузьке місце видимим."""
        ...


@runtime_checkable
class ShardKeyProvider(Protocol):
    """Порт обчислення ключа розподілу."""

    def shard_key(self, entity_id: str) -> ShardKey:
        ...
