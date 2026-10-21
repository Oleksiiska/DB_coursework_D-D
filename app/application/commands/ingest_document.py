"""Сценарій приймання документів — шлях команд CQRS."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Mapping

from app.application.commands.documents import (
    as_list,
    assign_natural_id,
    build_entity,
    entity_id,
    graph_document,
    index_document,
    slugify,
)
from app.application.dto import IngestReport, LiveStatsDTO
from app.domain.ports import (
    AdventureRepository,
    CrudRepository,
    DataQualityLog,
    GraphRepository,
    MetricsSink,
    SearchIndex,
)
from app.domain.services import (
    DocumentValidator,
    EnvironmentClassifier,
    ShardKeyService,
    VocabularyNormalizer,
)
from app.domain.values import AdventureFilters, DocumentKind

log = logging.getLogger("ingest")


class IngestDocumentHandler:
    """Єдиний обробник для всіх джерел і всіх видів документів.

    Оскільки CSV, генератор і краулер реалізують спільний порт, логіка
    валідації, трансформацій, обчислення ключа розподілу та індексації
    існує в одному екземплярі.

    Сховище обирається за видом документа з реєстру репозиторіїв;
    індекс — з реєстру індексів. Перелік видів і ознака індексації
    оголошені в домені (`DocumentKind`), тож новий вид документа не
    змінює код обробника. Реєстр перевіряється при створенні: вид без
    репозиторію означав би, що документи цього виду мовчки губляться
    на шляху запису.
    """

    #: Похідні лічильники авторів, тегів і світів (таблиця 2.14) допускають
    #: тимчасове відставання (таблиця 4.2): їх перераховують не частіше
    #: ніж раз на цей інтервал і не більше ніж для стількох об'єктів за раз
    DERIVED_REFRESH_SECONDS = 30
    DERIVED_REFRESH_LIMIT = 500
    #: Частка відхилених документів у пакеті, понад яку — WARNING (таблиця 4.17)
    REJECTED_SHARE_WARNING = 0.05

    def __init__(
        self,
        repositories: Mapping[DocumentKind, CrudRepository],
        indexes: Mapping[DocumentKind, SearchIndex],
        graph: GraphRepository,
        metrics: MetricsSink,
        quality_log: DataQualityLog,
        validator: DocumentValidator,
        shard_keys: ShardKeyService,
        classifier: EnvironmentClassifier,
        vocabulary: VocabularyNormalizer,
    ) -> None:
        missing_repositories = set(DocumentKind) - set(repositories)
        if missing_repositories:
            raise ValueError(f"немає репозиторію для: {sorted(k.value for k in missing_repositories)}")
        missing_indexes = {kind for kind in DocumentKind if kind.is_indexed} - set(indexes)
        if missing_indexes:
            raise ValueError(f"немає індексу для: {sorted(k.value for k in missing_indexes)}")

        self._repositories = dict(repositories)
        self._indexes = dict(indexes)
        self._graph = graph
        self._metrics = metrics
        self._quality_log = quality_log
        self._validator = validator
        self._shard_keys = shard_keys
        self._classifier = classifier
        self._vocabulary = vocabulary
        #: Об'єкти, чиї похідні лічильники треба перерахувати
        self._pending: dict[DocumentKind, set[str]] = {
            DocumentKind.AUTHOR: set(), DocumentKind.TAG: set(), DocumentKind.SETTING: set(),
        }
        self._last_refresh = 0.0
        #: Відкладений перерахунок на випадок, коли нових пакетів немає
        self._deferred: asyncio.Task | None = None
        #: Теги, наявність яких у довіднику вже перевірено
        self._known_tags: set[str] = set()
        self._reported_tags: set[str] = set()

    async def handle(self, batch: list[dict], source_name: str) -> IngestReport:
        """Обробка пакета: групування за видом, валідація, трансформації,
        паралельний запис у сховище, індекс і граф.

        Запис виконується пакетами, а не поодинці: кількість звернень
        до сховища зменшується на два порядки. Збій сховища піднімається
        вище, тож пакет лишається непідтвердженим і надійде повторно.
        """
        started = time.perf_counter()
        report = IngestReport(source_name=source_name, received=len(batch))
        groups = self._group_by_kind(batch)
        for document in groups.pop(None, []):
            await self._reject(document, [f"kind: невідомий вид документа {document.get('kind')!r}"])
            report.rejected += 1
        if report.rejected:
            await self._metrics.inc("ingest.rejected.unknown", report.rejected)

        for kind, documents in groups.items():
            entities: list[Any] = []
            rejected = 0
            for document in documents:
                entity, reasons = self._prepare(kind, document)
                if reasons:
                    await self._reject(document, reasons)
                    rejected += 1
                else:
                    entities.append(entity)
            report.rejected += rejected
            if rejected:
                await self._metrics.inc(f"ingest.rejected.{kind.value}", rejected)
            if not entities:
                continue
            saved, indexed, edges = await self._persist(kind, entities)
            report.saved += saved
            report.indexed += indexed
            report.graph_edges += edges
            await self._metrics.inc(f"ingest.saved.{kind.value}", saved)
            await self._after_write(kind, entities)

        for vocabulary, term in self._vocabulary.drain_unknown():
            await self._quality_log.unknown_term(vocabulary, term)
            report.unknown_terms.append(f"{vocabulary}:{term}")
        await self._refresh_derived_if_due()

        report.duration_ms = round((time.perf_counter() - started) * 1000)
        await self._metrics.observe_latency("ingest.batch", report.duration_ms)
        log.info("пакет від %s: отримано %d, збережено %d, відхилено %d за %d мс",
                 source_name, report.received, report.saved, report.rejected, report.duration_ms)
        if report.received and report.rejected / report.received > self.REJECTED_SHARE_WARNING:
            log.warning("пакет від %s: відхилено %d із %d (понад %d %%)",
                        source_name, report.rejected, report.received, round(self.REJECTED_SHARE_WARNING * 100))
        return report

    def _group_by_kind(self, batch: list[dict]) -> dict[DocumentKind | None, list[dict]]:
        groups: dict[DocumentKind | None, list[dict]] = {}
        for document in batch:
            try:
                kind: DocumentKind | None = DocumentKind(document.get("kind"))
            except ValueError:
                kind = None
            groups.setdefault(kind, []).append(document)
        return groups

    def _prepare(self, kind: DocumentKind, document: dict) -> tuple[Any, list[str]]:
        """Валідація, трансформації формату, обчислювані поля й інваріанти сутності."""
        reasons = self._validator.check(kind.value, document)
        if reasons:
            return None, reasons
        try:
            entity = self._derive(kind, build_entity(kind, self._transform(kind, document)))
        except (KeyError, ValueError) as error:
            return None, [str(error)]
        validate = getattr(entity, "validate", None)
        reasons = validate() if validate is not None else []
        if not reasons and not entity_id(kind, entity):
            reasons = ["ідентифікатор: відсутній"]
        return entity, reasons

    def _transform(self, kind: DocumentKind, document: dict) -> dict:
        """Приведення типів і похідні значення.

        Розбір складених рядків винесено в офлайн-підготовку CSV,
        тут лишаються детерміновані ключі, ключ розподілу, класифікація
        середовища монстра та нормалізація за словниками.
        """
        doc = {k: v for k, v in document.items() if k != "kind"}
        derived = set(as_list(doc.get("derived_fields")))
        now = datetime.now(timezone.utc).replace(microsecond=0)
        if kind is DocumentKind.TAG:
            doc["name"] = self._vocabulary.normalize("tag", doc.get("name"))
        elif kind is DocumentKind.CHARACTER:
            doc["char_class"] = self._vocabulary.normalize("class", doc.get("char_class"))
            doc["race"] = self._vocabulary.normalize("race", doc.get("race"))
        assign_natural_id(kind, doc)
        if kind is DocumentKind.ADVENTURE:
            tags = [self._vocabulary.normalize("tag", t) for t in as_list(doc.get("tags"))]
            doc["tags"] = list(dict.fromkeys(t for t in tags if t))
            if not doc.get("slug") and doc.get("title") and doc.get("adventure_id"):
                doc["slug"] = f"{slugify(str(doc['title']))}-{str(doc['adventure_id'])[:8]}".lstrip("-")
                derived.add("slug")
            if doc.get("adventure_id"):
                doc["shard_key"] = self._shard_keys.key_value(str(doc["adventure_id"]))
            doc.setdefault("created_at", now)
            doc["indexed_at"] = now
            derived.update({"is_free", "reviews_count"})
        doc["derived_fields"] = sorted(derived)
        return doc

    def _derive(self, kind: DocumentKind, entity: Any) -> Any:
        """Обчислювані поля, що потребують сутності: середовище й текст
        здібностей монстра, тривалість і ознака викиду зустрічі."""
        if kind is DocumentKind.MONSTER:
            if not entity.environment:
                entity.environment = self._classifier.classify(entity)
                entity.provenance.mark_derived("environment")
            if not entity.abilities_text:
                entity.abilities_text = entity.build_abilities_text() or None
                entity.provenance.mark_derived("abilities_text")
        elif kind is DocumentKind.ENCOUNTER:
            if entity.duration_seconds is None and entity.ended_at and entity.started_at:
                entity.duration_seconds = int((entity.ended_at - entity.started_at).total_seconds())
                entity.provenance.mark_derived("duration_seconds")
            entity.is_abandoned = entity.detect_abandoned()
            entity.provenance.mark_derived("is_abandoned")
        return entity

    async def _persist(self, kind: DocumentKind, entities: list[Any]) -> tuple[int, int, int]:
        """Паралельний запис у три сховища. Повертає (збережено, проіндексовано, ребер).

        Помилка будь-якого сховища піднімається після завершення решти
        записів: пакет не підтверджується, а повторна доставка ідемпотентна.
        """
        tasks = [self._repositories[kind].add_many(entities)]
        if kind.is_indexed:
            tasks.append(self._indexes[kind].index_many([index_document(kind, e) for e in entities]))
        graph_batch = [doc for doc in (graph_document(kind, e) for e in entities) if doc is not None]
        if graph_batch:
            tasks.append(self._graph.merge_nodes_and_edges(graph_batch))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result
        saved = results[0]
        indexed = results[1] if kind.is_indexed else 0
        edges = results[-1] if graph_batch else 0
        return saved, indexed, edges

    async def _after_write(self, kind: DocumentKind, entities: list[Any]) -> None:
        """Статистика заповненості полів, перевірка тегів за довідником і
        позначення об'єктів, чиї похідні лічильники змінилися."""
        if kind is DocumentKind.ADVENTURE:
            await self._count_filled(entities)
            await self._check_tags({tag for e in entities for tag in e.tags})
            for adventure in entities:
                self._pending[DocumentKind.AUTHOR].update(adventure.author_ids)
                self._pending[DocumentKind.TAG].update(adventure.tags)
                if adventure.setting_id:
                    self._pending[DocumentKind.SETTING].add(adventure.setting_id)
        elif kind is DocumentKind.AUTHOR:
            self._pending[kind].update(e.author_id for e in entities)
        elif kind is DocumentKind.TAG:
            self._known_tags.update(e.name for e in entities)
            self._pending[kind].update(e.name for e in entities)
        elif kind is DocumentKind.SETTING:
            self._pending[kind].update(e.setting_id for e in entities)

    async def _count_filled(self, adventures: list[Any]) -> None:
        """Лічильники ingest.filled.<поле>.<origin> і знаменник
        ingest.filled.adventures.<origin> (таблиця 4.14)."""
        totals: dict[str, int] = {}
        filled: dict[str, int] = {}
        for adventure in adventures:
            origin = adventure.provenance.origin.value
            totals[origin] = totals.get(origin, 0) + 1
            flat = index_document(DocumentKind.ADVENTURE, adventure)
            for name in LiveStatsDTO.FILL_FIELDS:
                value = flat.get(name)
                if value not in (None, "", []):
                    filled[f"{name}.{origin}"] = filled.get(f"{name}.{origin}", 0) + 1
        for origin, count in totals.items():
            await self._metrics.inc(f"ingest.filled.adventures.{origin}", count)
        for key, count in filled.items():
            await self._metrics.inc(f"ingest.filled.{key}", count)

    async def _check_tags(self, tags: set[str]) -> None:
        """Теги, не знайдені в довіднику за псевдонімами, — у журнал
        нерозпізнаних значень для подальшого доповнення (п. 2.11)."""
        unchecked = [t for t in tags if t not in self._known_tags and t not in self._reported_tags]
        if not unchecked:
            return
        found = await self._repositories[DocumentKind.TAG].get_many([DocumentKind.TAG.natural_id(t) for t in unchecked])
        self._known_tags.update(tag.name for tag in found)
        for tag in unchecked:
            if tag in self._known_tags or self._vocabulary.is_canonical_tag(tag):
                self._known_tags.add(tag)
            else:
                self._reported_tags.add(tag)
                await self._quality_log.unknown_term("tag", tag)

    async def _refresh_derived_if_due(self) -> None:
        """Перерахунок похідних лічильників не частіше ніж раз на інтервал.

        Якщо перерахунок ще не на часі або лишилися об'єкти понад межу,
        наступний відкладається таймером: лічильники наздоганяють записи
        й тоді, коли нових пакетів у черзі більше немає.
        """
        wait = self._last_refresh + self.DERIVED_REFRESH_SECONDS - time.monotonic()
        if wait <= 0:
            self._last_refresh = time.monotonic()
            batch = {kind: set(list(ids)[: self.DERIVED_REFRESH_LIMIT]) for kind, ids in self._pending.items()}
            for kind, ids in batch.items():
                self._pending[kind] -= ids
            try:
                await self._refresh_derived(batch)
            except Exception as error:  # noqa: BLE001 — лічильники не першоджерело (таблиця 4.9)
                log.warning("похідні лічильники не перераховано, повтор за %d с: %s",
                            self.DERIVED_REFRESH_SECONDS, error)
                for kind, ids in batch.items():
                    self._pending[kind] |= ids
            wait = self.DERIVED_REFRESH_SECONDS
        if any(self._pending.values()) and (self._deferred is None or self._deferred.done()):
            self._deferred = asyncio.create_task(self._refresh_later(wait))

    async def _refresh_later(self, delay: float) -> None:
        await asyncio.sleep(delay)
        self._deferred = None
        await self._refresh_derived_if_due()

    async def _refresh_derived(self, batch: dict[DocumentKind, set[str]]) -> None:
        """Похідні лічильники (таблиця 2.14) перераховуються за роботами,
        а не накопичуються, тож повторна доставка пакета їх не спотворює."""
        adventures = self._repositories[DocumentKind.ADVENTURE]
        if not isinstance(adventures, AdventureRepository):
            return
        if batch[DocumentKind.AUTHOR]:
            ids = sorted(batch[DocumentKind.AUTHOR])
            stats = await adventures.author_stats(ids)
            authors = await self._repositories[DocumentKind.AUTHOR].get_many(ids)
            for author in authors:
                values = stats.get(author.author_id, {})
                author.adventures_count = values.get("adventures_count", 0)
                author.avg_rating = values.get("avg_rating")
                author.first_published_year = values.get("first_published_year")
                author.co_author_ids = values.get("co_author_ids", [])
                for name in ("adventures_count", "avg_rating", "first_published_year", "co_author_ids"):
                    author.provenance.mark_derived(name)
            if authors:
                await self._repositories[DocumentKind.AUTHOR].add_many(authors)
        for kind, filter_of, key_of in (
            (DocumentKind.TAG, lambda v: AdventureFilters(tags=(v,)), DocumentKind.TAG.natural_id),
            (DocumentKind.SETTING, lambda v: AdventureFilters(setting_id=v), lambda v: v),
        ):
            values = sorted(batch[kind])
            if not values:
                continue
            counts = await asyncio.gather(*(adventures.count(filter_of(v)) for v in values))
            by_key = {key_of(v): count for v, count in zip(values, counts)}
            entities = await self._repositories[kind].get_many(list(by_key))
            for entity in entities:
                entity.adventures_count = by_key[entity_id(kind, entity)]
                entity.provenance.mark_derived("adventures_count")
            if entities:
                await self._repositories[kind].add_many(entities)

    async def _reject(self, document: dict, reasons: list[str]) -> None:
        """Журнал браку через порт якості даних."""
        await self._quality_log.rejected(document, reasons)

