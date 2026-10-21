"""Сценарій зміни швидкості генерації зі слайдера інтерфейсу."""

from __future__ import annotations

import logging

from app.application.dto import RateSettingDTO
from app.domain.ports import CacheStore

log = logging.getLogger("api")


class ConfigureRateHandler:
    """Запис цільової сумарної швидкості у спільне сховище.

    Генератор працює окремим процесом і зчитує значення перед кожним
    пакетом. Взаємодія між процесами — через спільний ключ, а не
    прямими викликами (п. 3.6.3). Ключ не має часу життя й не
    витісняється з пам'яті (п. 4.7).
    """

    CONFIG_KEY = "config:generator_rate"

    def __init__(self, cache: CacheStore, default_rate: int = 100) -> None:
        self._cache = cache
        self._default_rate = default_rate

    async def handle(self, setting: RateSettingDTO) -> RateSettingDTO:
        """Валідує діапазон 10–5000 і зберігає значення."""
        setting.validate()
        previous = await self.current(setting.source_name)
        await self._cache.set(self.CONFIG_KEY, str(setting.documents_per_second).encode("ascii"))
        if previous.documents_per_second != setting.documents_per_second:
            log.info("зміна цільової швидкості: %d → %d док/с",
                     previous.documents_per_second, setting.documents_per_second)
        return setting

    async def current(self, source_name: str = "generator") -> RateSettingDTO:
        payload = await self._cache.get(self.CONFIG_KEY)
        rate = self._default_rate if payload is None else int(payload)
        return RateSettingDTO(documents_per_second=rate, source_name=source_name)
