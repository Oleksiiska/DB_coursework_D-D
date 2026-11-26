"""Сценарій аналітики ніш каталогу (група запитів 11) — розподілений запит
у формі map-reduce (п. 4.12)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone

from app.application.dto import (
    MergedRunDTO,
    NicheQueryDTO,
    NicheReportDTO,
    NicheRowDTO,
    NicheRunDTO,
    ZoneRunDTO,
    dto_from_dict,
    dto_to_dict,
)
from app.domain.ports import AdventureRepository, CacheStore
from app.domain.services import NicheAggregator, ShardKeyService
from app.domain.values import CatalogDimension, CatalogScope, NicheOrder, NicheStats, ReadSnapshot, ZoneBounds

log = logging.getLogger("analytics")


class GetNicheAnalyticsHandler:
    """Скільки пригод має кожна ніша каталогу, як їх оцінюють, скільки вони
    коштують і тривають — по всій шардованій колекції пригод.

    Порядок виконання:

    1. Знімок: один момент у часі кластера, на якому читаються всі зони, тож
       часткові результати узгоджені, хоча генератор пише безперервно.
    2. Map: для кожної зони shard_key — окремий запит, який маршрутизатор
       надсилає на один шард; шард групує свої документи й повертає лише
       часткові агрегати груп. Зони опитуються паралельно.
    3. Reduce: часткові агрегати зводяться в застосунку в міру надходження
       відповідей (NicheAggregator); верхні ніші вибираються лише після зведення.
    4. Звірка: той самий запит однією агрегацією через маршрутизатор на тому
       самому знімку — результати мають збігтися.

    Зона, шард якої недоступний, позначається у звіті, а решта зон дає
    неповний результат: ключ розподілу — хеш, тож кожна зона є рівномірною
    вибіркою каталогу, і частки й середні лишаються оцінками за вибіркою.
    Неповний результат не кешується.
    """

    #: Запит переглядає весь каталог, тож результат кешується довше за видачу
    #: пошуку: частки ніш під безперервним записом змінюються повільно (табл. 4.9)
    CACHE_TTL_SECONDS = 300
    CACHE_PREFIX = "analytics:niches:v1"

    def __init__(
        self,
        adventures: AdventureRepository,
        shard_keys: ShardKeyService,
        aggregator: NicheAggregator,
        cache: CacheStore,
    ) -> None:
        self._adventures = adventures
        self._shard_keys = shard_keys
        self._aggregator = aggregator
        self._cache = cache
        #: Обчислення, що вже виконуються: однакові одночасні запити чекають
        #: на одне обчислення, а не запускають кожен своє
        self._inflight: dict[str, asyncio.Future] = {}

    async def handle(self, query: NicheQueryDTO) -> NicheReportDTO:
        query.validate()
        dimension, scope = CatalogDimension(query.dimension), CatalogScope(query.scope)
        key = f"{self.CACHE_PREFIX}:{dimension.value}:{scope.value}"
        run = None if query.refresh else await self._from_cache(key)
        cache_hit = run is not None
        if run is None:
            run = await self._single_flight(key, dimension, scope)
        return self._report(run, NicheOrder(query.order), query.limit, cache_hit)

    # --- обчислення ---

    async def _single_flight(self, key: str, dimension: CatalogDimension, scope: CatalogScope) -> NicheRunDTO:
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.ensure_future(self._compute_and_store(key, dimension, scope))
            self._inflight[key] = task
            task.add_done_callback(lambda _: self._inflight.pop(key, None))
        return await asyncio.shield(task)

    async def _compute_and_store(self, key: str, dimension: CatalogDimension, scope: CatalogScope) -> NicheRunDTO:
        run = await self._compute(dimension, scope)
        if run.complete:
            await self._to_cache(key, run)
        return run

    async def _compute(self, dimension: CatalogDimension, scope: CatalogScope) -> NicheRunDTO:
        zones = self._shard_keys.zones()
        snapshot = await self._snapshot(zones)
        runs = [ZoneRunDTO(index=zone.index, label=zone.label) for zone in zones]
        groups: dict[str, NicheStats] = {}
        by_zone: dict[str, list[int]] = {}
        documents = 0
        reduce_seconds = 0.0
        started = time.perf_counter()

        async def map_zone(zone: ZoneBounds):
            try:
                return zone, await self._adventures.niche_partial(zone, dimension, scope, snapshot)
            except Exception as error:  # noqa: BLE001 — недоступна зона позначається у звіті
                return zone, error

        for finished in asyncio.as_completed([map_zone(zone) for zone in zones]):
            zone, result = await finished
            entry = runs[zone.index]
            if isinstance(result, Exception):
                entry.error = str(result) or type(result).__name__
                log.warning("ніші %s/%s: зона %d (%s) не відповіла: %s",
                            dimension.value, scope.value, zone.index + 1, zone.label, entry.error)
                continue
            merging = time.perf_counter()
            self._aggregator.merge_into(groups, result.groups)
            for group, stats in result.groups.items():
                by_zone.setdefault(group, [0] * len(zones))[zone.index] = stats.adventures
            reduce_seconds += time.perf_counter() - merging
            documents += result.documents
            entry.shard, entry.documents, entry.groups, entry.elapsed_ms = (
                result.shard, result.documents, len(result.groups), result.elapsed_ms)
        gather_ms = round((time.perf_counter() - started) * 1000)

        run = NicheRunDTO(
            dimension=dimension.value,
            scope=scope.value,
            generated_at=datetime.now(timezone.utc),
            snapshot=snapshot.token if snapshot else None,
            snapshot_at=snapshot.at if snapshot else None,
            zones=runs,
            documents=documents,
            groups=groups,
            by_zone=by_zone,
            map_max_ms=max((z.elapsed_ms for z in runs if z.error is None), default=0),
            map_sum_ms=sum(z.elapsed_ms for z in runs if z.error is None),
            reduce_ms=round(reduce_seconds * 1000, 2),
            gather_ms=gather_ms,
        )
        run.merged = await self._compare(run, dimension, scope, snapshot)
        self._log(run, dimension, scope)
        return run

    async def _snapshot(self, zones: list[ZoneBounds]) -> ReadSnapshot | None:
        """Знімок береться пробою першої зони, шард якої відповідає."""
        for zone in zones:
            snapshot = await self._adventures.read_snapshot(zone)
            if snapshot is not None:
                return snapshot
        return None

    async def _compare(
        self, run: NicheRunDTO, dimension: CatalogDimension, scope: CatalogScope, snapshot: ReadSnapshot | None
    ) -> MergedRunDTO:
        """Звірка з вбудованою агрегацією: на тому самому знімку результати мають
        збігтися; без знімка розбіжність можлива через запис між запитами."""
        try:
            merged = await self._adventures.niche_merged(dimension, scope, snapshot)
        except Exception as error:  # noqa: BLE001 — звірка не скасовує результат map-reduce
            log.warning("ніші %s/%s: вбудована агрегація не виконалась: %s", dimension.value, scope.value, error)
            return MergedRunDTO(error=str(error) or type(error).__name__)
        matches = None
        if run.complete:
            matches = merged.documents == run.documents and self._aggregator.equal(run.groups, merged.groups)
            if not matches and snapshot is not None:
                log.warning("ніші %s/%s: результат map-reduce не збігся з вбудованою агрегацією на тому самому знімку",
                            dimension.value, scope.value)
        return MergedRunDTO(
            elapsed_ms=merged.elapsed_ms, documents=merged.documents, groups=len(merged.groups), matches=matches,
            shards=list(merged.shards), shards_part=list(merged.shards_part), merger_part=list(merged.merger_part),
            merge_type=merged.merge_type,
        )

    @staticmethod
    def _log(run: NicheRunDTO, dimension: CatalogDimension, scope: CatalogScope) -> None:
        answered = sum(zone.error is None for zone in run.zones)
        merged = run.merged
        comparison = ("—" if merged.elapsed_ms is None else
                      f"{merged.elapsed_ms} мс, збіг: {'—' if merged.matches is None else 'так' if merged.matches else 'ні'}")
        log.info("ніші %s/%s — зон %d з %d, %d док., %d груп; map %d мс (послідовно %d), reduce %.1f мс, "
                 "усього %d мс; вбудована агрегація %s; знімок %s",
                 dimension.value, scope.value, answered, len(run.zones), run.documents, len(run.groups),
                 run.map_max_ms, run.map_sum_ms, run.reduce_ms, run.gather_ms, comparison, run.snapshot or "немає")

    # --- звіт і кеш ---

    def _report(self, run: NicheRunDTO, order: NicheOrder, limit: int, cache_hit: bool) -> NicheReportDTO:
        ranked = self._aggregator.rank(run.groups, order, limit)
        zones = len(run.zones)
        answered = sum(zone.error is None for zone in run.zones)
        return NicheReportDTO(
            dimension=run.dimension,
            scope=run.scope,
            order=order.value,
            limit=limit,
            generated_at=run.generated_at,
            cache_hit=cache_hit,
            complete=run.complete,
            coverage=answered / zones if zones else 1.0,
            snapshot=run.snapshot,
            snapshot_at=run.snapshot_at,
            documents=run.documents,
            groups_total=len(run.groups),
            zones=run.zones,
            map_max_ms=run.map_max_ms,
            map_sum_ms=run.map_sum_ms,
            reduce_ms=run.reduce_ms,
            gather_ms=run.gather_ms,
            merged=run.merged,
            rows=[NicheRowDTO.from_stats(key, stats, run.documents, run.by_zone.get(key, [0] * zones))
                  for key, stats in ranked],
        )

    async def _from_cache(self, key: str) -> NicheRunDTO | None:
        try:
            payload = await self._cache.get(key)
        except Exception as error:  # noqa: BLE001
            log.warning("кеш недоступний, аналітика рахується у сховищі: %s", error)
            return None
        return None if payload is None else dto_from_dict(NicheRunDTO, json.loads(payload))

    async def _to_cache(self, key: str, run: NicheRunDTO) -> None:
        try:
            await self._cache.set(key, json.dumps(dto_to_dict(run), ensure_ascii=False).encode("utf-8"),
                                  self.CACHE_TTL_SECONDS)
        except Exception as error:  # noqa: BLE001
            log.warning("кеш недоступний, аналітику не збережено: %s", error)
