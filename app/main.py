"""Точка входу процесу API.

Запуск: ``python -m app.main`` — так стартує контейнер api
у docker-compose.cluster.yml (таблиця 3.7). Той самий процес віддає
статичну сторінку інтерфейсу й потік статистики SSE (п. 4.9).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from app.infrastructure.config import Container, Settings
from app.presentation.rest import RouteRegistry

log = logging.getLogger("api")


class Application:
    """Складання та життєвий цикл процесу API."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or Settings.from_env()
        self._container: Container | None = None
        self._routes = RouteRegistry()

    async def startup(self) -> None:
        """Складання об'єктного графа, підключення до сховищ, створення
        індексів і обмежень, підстановка вхідних портів у контролери."""
        self._container = Container(self._settings)
        adapters = await self._container.build_adapters()
        await self._container.ensure_storage(adapters)
        self._routes.bind(self._container.build_use_cases(adapters))
        log.info("API готовий: порт %d, сховище %s", self._settings.api_port, self._settings.redacted_mongo_uri())

    async def shutdown(self) -> None:
        if self._container is not None:
            await self._container.shutdown()
            self._container = None

    def build(self):
        """Створення об'єкта веб-застосунку з зареєстрованими маршрутами."""
        from fastapi import FastAPI

        @asynccontextmanager
        async def lifespan(_: FastAPI) -> AsyncIterator[None]:
            await self.startup()
            try:
                yield
            finally:
                await self.shutdown()

        app = FastAPI(
            title="D&D Adventures",
            description="Пошук, картки, рекомендації, статистика гри й моніторинг (таблиця 4.15)",
            lifespan=lifespan,
        )
        self._routes.register(app, data_dir=Path(self._settings.data_dir))
        return app


def create_app(settings: Settings | None = None):
    settings = settings or Settings.from_env()
    Container(settings).configure_logging("api")
    return Application(settings).build()


def main() -> None:
    """Запуск веб-сервера з об'єктом create_app()."""
    import uvicorn

    settings = Settings.from_env()
    # Журнали налаштовує композиційний корінь (config/logging.yaml), тож
    # власна конфігурація журналів Uvicorn вимкнена
    uvicorn.run(create_app(settings), host=settings.api_host, port=settings.api_port, log_config=None)


if __name__ == "__main__":
    main()
