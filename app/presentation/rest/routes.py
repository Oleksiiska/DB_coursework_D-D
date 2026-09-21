"""Реєстрація маршрутів у веб-застосунку.

Виокремлено від контролерів, щоб зміна фреймворку не торкалася
логіки перекладу DTO.
"""

from __future__ import annotations

from typing import Any



class RouteRegistry:
    ROUTES: tuple[tuple[str, str, str], ...] = (
        ("GET", "/api/search", "search.search"),
        ("GET", "/api/adventures/{adventure_id}", "card.adventure"),
        ("GET", "/api/monsters/{monster_id}", "card.monster"),
        ("GET", "/api/similar/{adventure_id}", "card.similar"),
        ("GET", "/api/rate", "admin.get_rate"),
        ("POST", "/api/rate", "admin.set_rate"),
        ("GET", "/api/stats/stream", "stats.stream"),
        ("GET", "/health", "health.check"),
    )

    def register(self, app: Any, use_cases: Any) -> None:
        raise NotImplementedError
