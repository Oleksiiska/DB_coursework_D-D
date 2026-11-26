"""Аналітика ніш каталогу: еталон етапу map і етап reduce (п. 4.12)."""

from __future__ import annotations

import math
from dataclasses import fields
from typing import Iterable, Mapping

from app.domain.entities import Adventure
from app.domain.values import (
    RATING_BINS,
    CatalogDimension,
    CatalogScope,
    NicheOrder,
    NicheStats,
    play_tier,
)

#: Ключ групи для пригод, у яких значення виміру не вказано
UNSPECIFIED = ""


class NicheAggregator:
    """Правила аналітики ніш, незалежні від сховища (група запитів 11).

    `group_keys` і `contribution` — еталон етапу map: до яких груп належить
    пригода і що вона додає до агрегату групи. Адаптер документного сховища
    виконує ту саму обробку мовою конвеєра агрегації на шарді кожної зони, а
    інтеграційний тест звіряє його результат з еталоном, як `Adventure.matches`
    є еталоном фільтрів пошуку.

    `merge_into` і `reduce` — етап reduce: зведення часткових агрегатів зон.
    Зведення точне, бо `NicheStats` — моноїд із розкладних показників; верхні
    N ніш при цьому вибираються лише після зведення: ніша, що в кожній зоні
    лише одинадцята, у підсумку може бути першою, тож зони обрізати не можна.
    """

    #: Оцінка ніші з меншою кількістю оцінених пригод ненадійна: у порядку
    #: за оцінкою такі ніші йдуть після решти
    MIN_RATED = 5
    #: Відносна похибка сум з плаваючою комою під час звірки результатів:
    #: порядок додавання в застосунку й у сховищі різний
    TOLERANCE = 1e-9
    #: Кошик гістограми: оцінка × (кошиків на шкалу 0–5)
    BIN_FACTOR = RATING_BINS / 5

    # --- map: еталон ---

    @staticmethod
    def group_keys(adventure: Adventure, dimension: CatalogDimension) -> list[str]:
        """Групи пригоди: кожен її тег без повторів або одне значення виміру;
        пригода без тегів чи без значення — у групі «не вказано»."""
        if dimension is CatalogDimension.TAG:
            return sorted({tag for tag in adventure.tags if tag}) or [UNSPECIFIED]
        if dimension is CatalogDimension.SYSTEM:
            value: object = adventure.system
        elif dimension is CatalogDimension.SETTING:
            value = adventure.setting_id
        elif dimension is CatalogDimension.TIER:
            value = play_tier(adventure.levels.minimum)
        else:
            value = adventure.published_year
        return [UNSPECIFIED if value in (None, "") else str(value)]

    @classmethod
    def contribution(cls, adventure: Adventure) -> NicheStats:
        """Внесок однієї пригоди в агрегат кожної її групи."""
        rating = adventure.rating_avg
        bins = [0] * RATING_BINS
        if rating is not None:
            bins[min(RATING_BINS - 1, max(0, math.floor(rating * cls.BIN_FACTOR)))] = 1
        amount = adventure.price.amount
        priced = amount is not None and amount > 0
        year = adventure.published_year
        return NicheStats(
            adventures=1,
            collected=int(adventure.provenance.origin.value in CatalogScope.COLLECTED.origins),
            rated=int(rating is not None),
            rating_sum=float(rating) if rating is not None else 0.0,
            rating_bins=tuple(bins),
            ratings_total=adventure.ratings_count or 0,
            reviews_total=adventure.reviews_count(),
            free=int(adventure.is_free),
            priced=int(priced),
            price_sum=float(amount) if priced else 0.0,
            timed=int(adventure.duration_hours is not None),
            duration_sum=float(adventure.duration_hours or 0.0),
            year_min=year,
            year_max=year,
        )

    def map(
        self, adventures: Iterable[Adventure], dimension: CatalogDimension, scope: CatalogScope
    ) -> tuple[dict[str, NicheStats], int]:
        """Еталон етапу map для набору пригод: агрегати груп і кількість
        пригод, що потрапили в запит."""
        origins = scope.origins
        groups: dict[str, NicheStats] = {}
        documents = 0
        for adventure in adventures:
            if origins is not None and adventure.provenance.origin.value not in origins:
                continue
            documents += 1
            value = self.contribution(adventure)
            for key in self.group_keys(adventure, dimension):
                current = groups.get(key)
                groups[key] = value if current is None else current.combine(value)
        return groups, documents

    # --- reduce ---

    @staticmethod
    def merge_into(accumulator: dict[str, NicheStats], partial: Mapping[str, NicheStats]) -> None:
        """Додає частковий агрегат зони до накопиченого: зони зводяться в тому
        порядку, у якому відповідають шарди."""
        for key, stats in partial.items():
            current = accumulator.get(key)
            accumulator[key] = stats if current is None else current.combine(stats)

    def reduce(self, partials: Iterable[Mapping[str, NicheStats]]) -> dict[str, NicheStats]:
        result: dict[str, NicheStats] = {}
        for partial in partials:
            self.merge_into(result, partial)
        return result

    def rank(self, groups: Mapping[str, NicheStats], order: NicheOrder, limit: int) -> list[tuple[str, NicheStats]]:
        """Верхні `limit` ніш: за кількістю пригод або за середньою оцінкою
        (спершу ніші з щонайменше MIN_RATED оціненими пригодами)."""
        if order is NicheOrder.RATING:
            def sort_key(item: tuple[str, NicheStats]) -> tuple:
                name, stats = item
                return (stats.rated < self.MIN_RATED, -(stats.avg_rating or 0.0), -stats.adventures, name)
        else:
            def sort_key(item: tuple[str, NicheStats]) -> tuple:
                return (-item[1].adventures, item[0])
        return sorted(groups.items(), key=sort_key)[:limit]

    def equal(self, a: Mapping[str, NicheStats], b: Mapping[str, NicheStats]) -> bool:
        """Чи збігаються два зведені результати: цілі поля — точно, суми з
        плаваючою комою — з відносною похибкою TOLERANCE."""
        return a.keys() == b.keys() and all(self._same(a[key], b[key]) for key in a)

    def _same(self, x: NicheStats, y: NicheStats) -> bool:
        for f in fields(NicheStats):
            u, v = getattr(x, f.name), getattr(y, f.name)
            if isinstance(u, float) or isinstance(v, float):
                if not math.isclose(u, v, rel_tol=self.TOLERANCE, abs_tol=1e-9):
                    return False
            elif u != v:
                return False
        return True
