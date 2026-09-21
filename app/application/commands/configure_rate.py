"""Сценарій зміни швидкості генерації зі слайдера інтерфейсу."""

from __future__ import annotations

from app.application.dto import RateSettingDTO
from app.domain.ports import CacheStore


class ConfigureRateHandler:
    """Запис цільової швидкості у спільне сховище.

    Генератор працює окремим процесом і зчитує значення перед кожним
    пакетом. Взаємодія між процесами — через спільний ключ, а не
    прямими викликами.
    """

    CONFIG_KEY = "config:generator_rate"

    def __init__(self, cache: CacheStore) -> None:
        self._cache = cache

    async def handle(self, setting: RateSettingDTO) -> RateSettingDTO:
        """Валідує діапазон 10–5000 і зберігає значення."""
        raise NotImplementedError

    async def current(self, source_name: str = "generator") -> RateSettingDTO:
        raise NotImplementedError
