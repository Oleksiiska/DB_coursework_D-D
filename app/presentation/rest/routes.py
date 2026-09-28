"""Реєстрація маршрутів у веб-застосунку.

Виокремлено від контролерів, щоб зміна фреймворку не торкалася
логіки перекладу DTO.
"""

from __future__ import annotations

from typing import Any


class RouteRegistry:
    ROUTES: tuple[tuple[str, str, str], ...] = (
        ("GET", "/api/search/adventures", "search.search_adventures"),
        ("GET", "/api/search/monsters", "search.search_monsters"),
        ("GET", "/api/search/encounters", "search.search_encounters"),
        ("GET", "/api/adventures/{adventure_id}", "card.adventure"),
        ("GET", "/api/monsters/{monster_id}", "card.monster"),
        ("GET", "/api/characters/{character_id}", "card.character"),
        ("GET", "/api/authors/{author_id}", "card.author"),
        ("GET", "/api/similar/{adventure_id}", "card.similar"),
        ("GET", "/api/campaign", "card.campaign"),
        ("GET", "/api/play-stats", "stats.play_stats"),
        ("GET", "/api/rate", "admin.get_rate"),
        ("POST", "/api/rate", "admin.set_rate"),
        ("GET", "/api/stats/stream", "stream.stream"),
        ("GET", "/health", "health.check"),
    )

    def register(self, app: Any, use_cases: Any) -> None:
        raise NotImplementedError
