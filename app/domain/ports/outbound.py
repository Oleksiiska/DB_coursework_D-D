"""Вихідні порти — залежності ядра від зовнішнього світу.

Оголошені як `Protocol`: структурна типізація дозволяє адаптеру
задовольняти контракт без успадкування, тобто infrastructure
не імпортує domain навіть заради базового класу.
"""

from __future__ import annotations

from typing import AsyncIterator, Protocol, Sequence, TypeVar, runtime_checkable

from app.domain.entities import Adventure, Encounter, Monster, PlayerCharacter, SearchLogEntry
from app.domain.values import (
    AdventureFilters,
    MonsterFilters,
    Page,
    QueuedBatch,
    SearchHits,
    SearchSpecification,
    SortOrder,
    SourceBatch,
)

T = TypeVar("T")


# --------------------------------------------------------------------------
# Репозиторії
# --------------------------------------------------------------------------


@runtime_checkable
class CrudRepository(Protocol[T]):
    """Узагальнений контракт сховища агрегату.

    Спільні операції оголошено один раз. Автори, теги, ігрові світи й
    записи участі монстрів задовольняються цим контрактом без
    спеціалізації; решта сутностей розширює його власними запитами.
    """

    async def get(self, entity_id: str) -> T | None:
        """Читання за ключем. Основний шлях картки об'єкта."""
        ...

    async def get_many(self, entity_ids: Sequence[str]) -> list[T]:
        """Пакетне читання зі збереженням порядку ключів після пошуку."""
        ...

    async def add(self, entity: T) -> None:
        ...

    async def add_many(self, entities: Sequence[T]) -> int:
        """Пакетний запис — основний чинник пропускної здатності."""
        ...

    async def update(self, entity: T) -> bool:
        ...

    async def delete(self, entity_id: str) -> bool:
        ...

    async def count(self, spec: SearchSpecification | None = None) -> int:
        ...


@runtime_checkable
class AdventureRepository(CrudRepository[Adventure], Protocol):
    """Репозиторій пригод — шардована колекція."""

    async def find_by_filters(
        self, filters: AdventureFilters, page: Page, sort: SortOrder = SortOrder.RELEVANCE
    ) -> list[Adventure]:
        ...

    async def find_by_author(self, author_id: str, limit: int = 50) -> list[Adventure]:
        """Інші роботи автора (група запитів 5), від новіших."""
        ...

    async def find_chain(self, setting_id: str, level_from: int, level_to: int) -> list[Adventure]:
        """Пригоди світу, чий діапазон рівнів перетинає заданий, за зростанням рівня."""
        ...

    async def count_by_shard(self) -> dict[str, int]:
        """Кількість документів пригод на кожному шарді з метаданих колекції
        (агрегація $collStats з параметром count, без сканування документів).

        Для шардованого кластера підрахунок інколи неточний: панель показує
        динаміку й співвідношення шардів, а точні кількості для розділу 6 дає
        countDocuments за діапазоном shard_key кожної зони (п. 4.9).
        """
        ...

    async def author_stats(self, author_ids: Sequence[str]) -> dict[str, dict]:
        """Похідні поля авторів (таблиця 2.14), перераховані за їхніми роботами:
        adventures_count, avg_rating, first_published_year, co_author_ids.

        Значення перераховуються, а не накопичуються, тож повторна доставка
        пакета їх не спотворює.
        """
        ...


@runtime_checkable
class MonsterRepository(CrudRepository[Monster], Protocol):
    async def find_by_filters(self, filters: MonsterFilters, page: Page) -> list[Monster]:
        """Підбір за рівнем небезпеки та середовищем за індексами документного сховища.

        Видачу групи запитів 3 будує пошуковий індекс (SearchMonstersHandler),
        бо там фільтр поєднується з текстом в одному запиті.
        """
        ...

    async def most_played(self, limit: int = 20) -> list[Monster]:
        """Найпопулярніші монстри реальних ігор (група запитів 7)."""
        ...

    async def set_play_counts(self, counts: dict[str, int]) -> None:
        """Записує кількість появ, перераховану за записами участі.

        Під час завантаження play_count надходить у monsters.csv (крок 3
        таблиці 2.4); метод перезаписує значення після повторної підготовки
        записів участі. Значення встановлюється, а не додається: повторний
        виклик не подвоює лічильників.
        """
        ...


@runtime_checkable
class CharacterRepository(CrudRepository[PlayerCharacter], Protocol):
    async def typical_spells(self, char_class: str, level: int, limit: int = 10) -> list[tuple[str, int]]:
        """Типові закляття класу — агрегація по вкладеному масиву."""
        ...

    async def class_race_distribution(self) -> list[tuple[str, str, int]]:
        ...


@runtime_checkable
class EncounterRepository(CrudRepository[Encounter], Protocol):
    async def median_duration(self, exclude_abandoned: bool = True) -> float | None:
        """Медіана тривалості в секундах, а не середнє: розподіл містить викиди."""
        ...

    async def median_rounds(self, exclude_abandoned: bool = True) -> float | None:
        ...

    async def party_composition(self, level: int | None = None) -> list[tuple[str, int]]:
        """Типовий склад партії за класами (група запитів 7)."""
        ...


# --------------------------------------------------------------------------
# Пошук, граф, кеш
# --------------------------------------------------------------------------


@runtime_checkable
class SearchIndex(Protocol):
    """Порт повнотекстового пошуку.

    Один контракт — три екземпляри: індекси пригод, монстрів і зустрічей.
    """

    async def search(
        self,
        text: str,
        spec: SearchSpecification,
        page: Page,
        sort: SortOrder = SortOrder.RELEVANCE,
    ) -> SearchHits:
        ...

    async def index_many(self, documents: Sequence[dict]) -> int:
        ...

    async def delete(self, document_id: str) -> bool:
        ...


@runtime_checkable
class GraphRepository(Protocol):
    """Порт обходу зв'язків."""

    async def similar_adventures(self, adventure_id: str, limit: int = 10) -> list[str]:
        """Рекомендації за спільними тегами й монстрами (група запитів 8)."""
        ...

    async def adventures_with_monster(self, monster_id: str, limit: int = 20) -> list[tuple[str, str]]:
        """Зворотний пошук: у яких пригодах зустрічається монстр.

        Граф зберігає ідентифікатори й назви (таблиця 4.5), тож повертаються
        пари (adventure_id, title).
        """
        ...

    async def co_authors(self, author_id: str) -> list[str]:
        ...

    async def seen_together(self, monster_id: str, limit: int = 10) -> list[tuple[str, int]]:
        """Монстри, що трапляються разом у реальних боях: обхід
        «монстр — зустріч — монстр» із кількістю спільних боїв (група 8)."""
        ...

    async def campaign_chain(
        self, setting_id: str, level_from: int, level_to: int, through: str | None = None
    ) -> list[str]:
        """Послідовність пригод одного світу без повторення монстрів (група 6).

        Шаблон шляху змінної довжини за ребрами продовження з умовами на
        вузли: неперервні рівні в межах [level_from, level_to], жодного
        спільного монстра. `through` — пригода, через яку має пройти ланцюжок.
        """
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

    async def heartbeat(self, group: str, member: str, window_seconds: int) -> int:
        """Позначає учасника групи живим і повертає кількість учасників, що
        подавали сигнал протягом останніх `window_seconds` секунд.

        Застарілі позначки видаляються тією самою атомарною операцією, тож
        аварійно зупинений екземпляр перестає враховуватися через вікно (п. 4.8).
        """
        ...


# --------------------------------------------------------------------------
# Джерела, черга, метрики, якість даних
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

    async def stream(self, batch_size: int = 500) -> AsyncIterator[SourceBatch]:
        """Асинхронний потік пакетів сирих документів зі звітом джерела (SourceBatch)."""
        ...

    async def set_rate(self, documents_per_second: int) -> None:
        """Цільова швидкість. Для CSV і краулера — верхня межа."""
        ...


@runtime_checkable
class EventPublisher(Protocol):
    """Порт публікації пакетів у чергу — сторона виробника."""

    async def publish(self, topic: str, batch: Sequence[dict]) -> None:
        ...

    async def queue_depth(self, topic: str) -> int:
        """Глибина черги робить вузьке місце видимим."""
        ...


@runtime_checkable
class EventConsumer(Protocol):
    """Порт читання з черги — сторона споживача.

    Відокремлений від публікації за принципом розділення інтерфейсів:
    джерелам не потрібне читання, процесу запису — публікація.
    """

    async def consume(self, topic: str, batch_size: int = 500) -> AsyncIterator[QueuedBatch]:
        """Пакети разом з ідентифікаторами для підтвердження."""
        ...

    async def acknowledge(self, topic: str, batch_id: str) -> None:
        """Підтвердження обробки пакета: без нього пакет буде доставлено повторно."""
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
class DataQualityLog(Protocol):
    """Порт журналів якості даних.

    Журнал браку та журнал нерозпізнаних значень словників — вимоги
    предметної області, тому оголошені портом, а не викликами логера.
    """

    async def rejected(self, document: dict, reasons: list[str]) -> None:
        ...

    async def unknown_term(self, vocabulary: str, term: str) -> None:
        ...
