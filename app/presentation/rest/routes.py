"""Реєстрація маршрутів у веб-застосунку (таблиця 4.15).

Виокремлено від контролерів, щоб зміна фреймворку не торкалася
логіки перекладу DTO: FastAPI перевіряє типи параметрів на межі шару
подання, а контролери перетворюють їх на об'єкти передачі даних.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.presentation.rest.controllers import (
    AdminController,
    CardController,
    ErrorTranslator,
    SearchController,
    StatisticsController,
)
from app.presentation.stream import StatsStream

#: Статична сторінка без етапу збирання: index.html, app.js, styles.css, Chart.js
STATIC_DIR = Path(__file__).resolve().parents[1] / "static"


class ErrorResponseMiddleware:
    """Переклад винятків сценаріїв у відповіді JSON (проміжний шар ASGI).

    Обробник винятків загального класу Exception фреймворк передає серверу
    повторно, і той записав би трасування рівня ERROR навіть для очікуваних
    відповідей 404 і 422. Тут виняток перехоплюється до сервера: в журнал
    рівня ERROR потрапляють лише справжні збої (таблиця 4.17).
    """

    def __init__(self, app: Any, translator: ErrorTranslator) -> None:
        self._app = app
        self._translator = translator

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        started = False

        async def tracked_send(message: dict) -> None:
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self._app(scope, receive, tracked_send)
        except Exception as error:  # noqa: BLE001 — переклад у відповідь, а не обрив з'єднання
            if started:
                raise
            status, body = self._translator.to_response(error)
            await JSONResponse(body, status_code=status)(scope, receive, send)


class RateRequest(BaseModel):
    """Тіло POST /api/rate; діапазон 10–5000 перевіряє сценарій."""

    documents_per_second: int


class RouteRegistry:
    """Маршрути API, їхні контролери й вхідні порти (таблиця 4.15)."""

    ROUTES: tuple[tuple[str, str, str], ...] = (
        ("GET", "/api/search/{kind}", "SearchController → SearchUseCase"),
        ("GET", "/api/adventures/{adventure_id}", "CardController → GetAdventureCardUseCase"),
        ("GET", "/api/monsters/{monster_id}", "CardController → GetMonsterCardUseCase"),
        ("GET", "/api/characters/{character_id}", "CardController → GetCharacterCardUseCase"),
        ("GET", "/api/authors/{author_id}", "CardController → GetAuthorCardUseCase"),
        ("GET", "/api/adventures/{adventure_id}/similar", "CardController → GetRecommendationsUseCase"),
        ("GET", "/api/adventures/{adventure_id}/chain", "CardController → GetRecommendationsUseCase"),
        ("GET", "/api/statistics/{report}", "StatisticsController → GetPlayStatisticsUseCase"),
        ("GET", "/api/stats/stream", "StatsStream → GetLiveStatsUseCase"),
        ("GET", "/api/rate", "AdminController → ConfigureRateUseCase"),
        ("POST", "/api/rate", "AdminController → ConfigureRateUseCase"),
        ("GET", "/", "статичні файли"),
        ("GET", "/static/…", "статичні файли"),
    )

    def __init__(self) -> None:
        self._search: SearchController | None = None
        self._cards: CardController | None = None
        self._statistics: StatisticsController | None = None
        self._admin: AdminController | None = None
        self._stream: StatsStream | None = None
        self._errors = ErrorTranslator()

    def bind(self, use_cases: Any) -> None:
        """Контролери над вхідними портами, складеними композиційним коренем."""
        self._search = SearchController(use_cases.search_adventures, use_cases.search_monsters,
                                        use_cases.search_encounters)
        self._cards = CardController(use_cases.adventure_card, use_cases.monster_card, use_cases.character_card,
                                     use_cases.author_card, use_cases.recommendations)
        self._statistics = StatisticsController(use_cases.play_statistics)
        self._admin = AdminController(use_cases.configure_rate)
        self._stream = StatsStream(use_cases.live_stats)

    def register(self, app: FastAPI, data_dir: Path | None = None) -> None:
        """Маршрути таблиці 4.15, статичні файли та переклад помилок.

        Списки вручну (світи, системи, псевдоніми тегів) віддаються як
        статичні файли /static/data/… — з них інтерфейс будує фільтри.
        """
        registry = self
        app.add_middleware(ErrorResponseMiddleware, translator=self._errors)

        @app.get("/api/search/{kind}")
        async def search(
            kind: Literal["adventures", "monsters", "encounters"],
            q: str = "",
            sort: Literal["relevance", "rating", "reviews", "year", "duration"] = "relevance",
            page: int = Query(1, ge=1),
            level: int | None = Query(None, ge=1, le=20),
            players: int | None = Query(None, ge=1, le=12),
            system: str | None = None,
            difficulty: int | None = Query(None, ge=1, le=5),
            tags: list[str] = Query([]),
            setting_id: str | None = None,
            is_free: bool | None = None,
            max_duration_hours: float | None = Query(None, gt=0),
            publication_language: str | None = None,
            year_from: int | None = None,
            year_to: int | None = None,
            challenge_min: float | None = Query(None, ge=0),
            challenge_max: float | None = Query(None, ge=0),
            party_level: int | None = Query(None, ge=1, le=20),
            creature_type: str | None = None,
            environment: str | None = None,
            command: str | None = None,
            min_rounds: int | None = Query(None, ge=0),
            max_rounds: int | None = Query(None, ge=0),
            exclude_abandoned: bool = True,
        ) -> dict:
            params = {
                "q": q, "sort": sort, "page": page, "level": level, "players": players, "system": system,
                "difficulty": difficulty, "tags": tags, "setting_id": setting_id, "is_free": is_free,
                "max_duration_hours": max_duration_hours, "publication_language": publication_language,
                "year_from": year_from, "year_to": year_to, "challenge_min": challenge_min,
                "challenge_max": challenge_max, "party_level": party_level, "creature_type": creature_type,
                "environment": environment, "command": command, "min_rounds": min_rounds,
                "max_rounds": max_rounds, "exclude_abandoned": exclude_abandoned,
            }
            return await registry._search.search(kind, params)

        @app.get("/api/adventures/{adventure_id}")
        async def adventure_card(adventure_id: str) -> dict:
            return await registry._cards.adventure(adventure_id)

        @app.get("/api/adventures/{adventure_id}/similar")
        async def similar(adventure_id: str, limit: int = Query(10, ge=1, le=50)) -> list:
            return await registry._cards.similar(adventure_id, limit)

        @app.get("/api/adventures/{adventure_id}/chain")
        async def chain(
            adventure_id: str,
            level_from: int = Query(1, ge=1, le=20),
            level_to: int = Query(10, ge=1, le=20),
        ) -> list:
            return await registry._cards.campaign(
                {"adventure_id": adventure_id, "level_from": level_from, "level_to": level_to})

        @app.get("/api/monsters/{monster_id}")
        async def monster_card(monster_id: str) -> dict:
            return await registry._cards.monster(monster_id)

        @app.get("/api/characters/{character_id}")
        async def character_card(character_id: str) -> dict:
            return await registry._cards.character(character_id)

        @app.get("/api/authors/{author_id}")
        async def author_card(author_id: str) -> dict:
            return await registry._cards.author(author_id)

        @app.get("/api/statistics/{report}")
        async def statistics(
            report: Literal["monsters", "spells", "party", "combat"],
            char_class: str | None = None,
            level: int | None = Query(None, ge=1, le=20),
            limit: int = Query(10, ge=1, le=100),
        ) -> dict:
            return await registry._statistics.play_stats(
                {"report": report, "char_class": char_class, "level": level, "limit": limit})

        @app.get("/api/stats/stream")
        async def stats_stream() -> StreamingResponse:
            return StreamingResponse(
                registry._stream.events(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        @app.get("/api/rate")
        async def get_rate() -> dict:
            return await registry._admin.get_rate()

        @app.post("/api/rate")
        async def set_rate(body: RateRequest) -> dict:
            return await registry._admin.set_rate(body.model_dump())

        @app.get("/", include_in_schema=False)
        async def index() -> FileResponse:
            return FileResponse(STATIC_DIR / "index.html")

        if data_dir is not None:
            app.mount("/static/data", StaticFiles(directory=data_dir), name="data")
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
