"""Точка входу процесу API."""

from __future__ import annotations

from app.infrastructure.config import Container, Settings


class Application:
    """Складання та життєвий цикл процесу API."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or Settings()
        self._container: Container | None = None

    async def startup(self) -> None:
        """Складання об'єктного графа, підключення до сховищ."""
        raise NotImplementedError

    async def shutdown(self) -> None:
        raise NotImplementedError

    def build(self):
        """Створення об'єкта веб-застосунку з зареєстрованими маршрутами."""
        raise NotImplementedError


def create_app():
    raise NotImplementedError
