"""Сценарії повнотекстового пошуку — шлях запитів CQRS.

Три види пошуку (пригоди, монстри, зустрічі) мають однаковий порядок
кроків і відрізняються лише типом фільтрів і перетворенням сутності
на картку. Тому порядок закріплено в базовому класі (шаблонний метод),
а підкласи визначають лише змінні кроки.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Generic, TypeVar

from app.application.dto import (
    AdventureCardDTO,
    EncounterCardDTO,
    MonsterCardDTO,
    SearchQueryDTO,
    SearchResultDTO,
    dto_from_dict,
    dto_to_dict,
)
from app.domain.entities import Adventure, Encounter, Monster, SearchLogEntry
from app.domain.errors import ValidationError
from app.domain.ports import CacheStore, CrudRepository, MetricsSink, SearchIndex
from app.domain.services import QueryNormalizer
from app.domain.values import (
    AdventureFilters,
    DocumentKind,
    EncounterFilters,
    MonsterFilters,
    Snippet,
)

TEntity = TypeVar("TEntity")
TCard = TypeVar("TCard")

log = logging.getLogger("search")


def _results(count: int) -> str:
    """Кількість результатів для журналу: «1 результат», «3 результати», «37 результатів»."""
    if count % 10 == 1 and count % 100 != 11:
        return f"{count} результат"
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return f"{count} результати"
    return f"{count} результатів"


class CachedSearchHandler(ABC, Generic[TEntity, TCard]):
    """Базовий сценарій пошуку з кешуванням. Реалізує порт `SearchUseCase`.

    Залежить лише від інтерфейсів: у модульних тестах вони підмінюються
    заглушками, і жодна база для запуску тесту не потрібна.
    """

    #: Вид документа — входить у ключ кешу
    KIND: DocumentKind
    #: Очікуваний тип фільтрів
    FILTERS_TYPE: type
    #: Тип картки — для відновлення видачі з кешу
    CARD_TYPE: type
    #: Час життя видачі в кеші (п. 4.3): за безперервного запису довший
    #: показував би застарілу видачу; уточнюється за частотою влучань
    CACHE_TTL_SECONDS = 60
    #: Запит, повільніший за цей поріг, — подія рівня WARNING (таблиця 4.17)
    SLOW_QUERY_MS = 100

    def __init__(
        self,
        index: SearchIndex,
        repository: CrudRepository[TEntity],
        cache: CacheStore,
        metrics: MetricsSink,
        normalizer: QueryNormalizer,
    ) -> None:
        self._index = index
        self._repository = repository
        self._cache = cache
        self._metrics = metrics
        self._normalizer = normalizer

    async def handle(self, query: SearchQueryDTO) -> SearchResultDTO[TCard]:
        """Шаблонний метод: перевірка фільтрів → ключ кешу → кеш → індекс →
        пакетне читання документів → кеш → запис метрики.

        При влучанні в кеш звернень до сховищ немає взагалі. Якщо після
        нормалізації не лишилося жодного слова й фільтрів не задано,
        відповідь порожня без звернення до рушія (п. 4.6).
        """
        started = time.perf_counter()
        self._check_filters(query)
        if self._normalizer.is_empty(query.raw_text) and query.filters.is_empty():
            result: SearchResultDTO[TCard] = SearchResultDTO(page=query.page.number, page_size=query.page.size)
        else:
            key = self._normalizer.cache_key(self.KIND, query.raw_text, query.filters, query.page, query.sort)
            cached = await self._from_cache(key)
            if cached is not None:
                result = cached
                result.cache_hit = True
            else:
                result = await self._from_storage(query)
                await self._to_cache(key, result)
        result.latency_ms = round((time.perf_counter() - started) * 1000)
        await self._record(query, result)
        return result

    # --- змінний крок, що визначається підкласами ---

    @abstractmethod
    def _to_card(self, entity: TEntity, snippets: list[Snippet]) -> TCard:
        ...

    # --- незмінні кроки ---

    def _check_filters(self, query: SearchQueryDTO) -> None:
        """Фільтри мають відповідати виду пошуку."""
        if not isinstance(query.filters, self.FILTERS_TYPE):
            raise ValidationError(
                "search",
                [f"filters: для пошуку {self.KIND.plural} потрібні {self.FILTERS_TYPE.__name__}, "
                 f"отримано {type(query.filters).__name__}"],
            )

    async def _from_cache(self, key: str) -> SearchResultDTO[TCard] | None:
        """Відповідь із кешу. Недоступний кеш не зупиняє пошук: запит іде
        напряму до рушія, а подія записується попередженням (таблиця 4.17)."""
        try:
            payload = await self._cache.get(key)
        except Exception as error:  # noqa: BLE001 — будь-який збій кешу
            log.warning("кеш недоступний, пошук іде напряму до рушія: %s", error)
            return None
        if payload is None:
            return None
        data = json.loads(payload)
        result: SearchResultDTO[TCard] = dto_from_dict(SearchResultDTO, {**data, "items": []})
        result.items = [dto_from_dict(self.CARD_TYPE, item) for item in data["items"]]
        return result

    async def _to_cache(self, key: str, result: SearchResultDTO[TCard]) -> None:
        """Кешується вся відповідь: картки сторінки, фрагменти, кількості
        за тегами й загальна кількість; порожні видачі — так само (п. 4.7)."""
        data = dto_to_dict(result)
        data.pop("cache_hit", None)
        data.pop("latency_ms", None)
        try:
            await self._cache.set(key, json.dumps(data, ensure_ascii=False).encode("utf-8"), self.CACHE_TTL_SECONDS)
        except Exception as error:  # noqa: BLE001
            log.warning("кеш недоступний, видачу не збережено: %s", error)

    async def _from_storage(self, query: SearchQueryDTO) -> SearchResultDTO[TCard]:
        """Пошук в індексі, потім пакетне читання сутностей за ключами.

        У рушій передаються вихідні форми слів, по одній на основу (п. 4.7).
        Ідентифікатори з індексу, яких ще немає в документному сховищі,
        пакетне читання пропускає (п. 4.4.2).
        """
        text = self._normalizer.search_text(query.raw_text)
        hits = await self._index.search(text, query.filters, query.page, query.sort)
        entities = await self._repository.get_many(list(hits.ids))
        items = [self._to_card(entity, list(hits.snippets.get(self._id_of(entity), ()))) for entity in entities]
        return SearchResultDTO(
            items=items,
            total=hits.total,
            page=query.page.number,
            page_size=query.page.size,
            normalized_query=self._normalizer.normalize(query.raw_text),
            tag_counts=dict(hits.tag_counts),
        )

    async def _record(self, query: SearchQueryDTO, result: SearchResultDTO[TCard]) -> None:
        """Запис у журнал запитів — живить панель моніторингу."""
        entry = SearchLogEntry.from_result(
            query_id=str(uuid.uuid4()),
            raw_query=query.raw_text,
            normalized_query=result.normalized_query or self._normalizer.normalize(query.raw_text),
            kind=self.KIND,
            filters=json.loads(query.filters.cache_fragment()),
            results_count=result.total,
            latency_ms=result.latency_ms,
            cache_hit=result.cache_hit,
            created_at=datetime.now(timezone.utc),
        )
        log.info(
            "%s «%s» — %s, %s, %d мс, кеш: %s",
            self.KIND.plural, query.raw_text, entry.status.value, _results(result.total),
            result.latency_ms, "так" if result.cache_hit else "ні",
        )
        if result.latency_ms > self.SLOW_QUERY_MS:
            log.warning("%s «%s»: запит повільніший за %d мс — %d мс",
                        self.KIND.plural, query.raw_text, self.SLOW_QUERY_MS, result.latency_ms)
        try:
            await self._metrics.record_query(entry)
        except Exception as error:  # noqa: BLE001 — журнал запитів не зупиняє видачу
            log.warning("журнал запитів недоступний: %s", error)

    def _id_of(self, entity: TEntity) -> str:
        return getattr(entity, f"{self.KIND.value}_id")


class SearchAdventuresHandler(CachedSearchHandler[Adventure, AdventureCardDTO]):
    """Пошук пригод за текстом і фільтрами партії (групи запитів 1, 2, 9).

    Тексти вкладених відгуків індексуються разом із пригодою, тому
    пошук за текстом відгуку теж виконується тут.
    """

    KIND = DocumentKind.ADVENTURE
    FILTERS_TYPE = AdventureFilters
    CARD_TYPE = AdventureCardDTO

    def _to_card(self, entity: Adventure, snippets: list[Snippet]) -> AdventureCardDTO:
        return AdventureCardDTO.from_entity(entity, snippets)


class SearchMonstersHandler(CachedSearchHandler[Monster, MonsterCardDTO]):
    """Пошук монстрів за описом здібності та підбір за рівнем
    небезпеки й середовищем (групи запитів 1, 3)."""

    KIND = DocumentKind.MONSTER
    FILTERS_TYPE = MonsterFilters
    CARD_TYPE = MonsterCardDTO

    def _to_card(self, entity: Monster, snippets: list[Snippet]) -> MonsterCardDTO:
        card = MonsterCardDTO.from_entity(entity, snippets)
        card.statblock = {}  # статблок — лише в повній картці
        return card


class SearchEncountersHandler(CachedSearchHandler[Encounter, EncounterCardDTO]):
    """Пошук реальних зустрічей за текстом наративу ведучого (група 1)."""

    KIND = DocumentKind.ENCOUNTER
    FILTERS_TYPE = EncounterFilters
    CARD_TYPE = EncounterCardDTO

    def _to_card(self, entity: Encounter, snippets: list[Snippet]) -> EncounterCardDTO:
        return EncounterCardDTO.from_entity(entity, snippets)
