"""Трасування вимог: кожна група запитів із таблиці 1.2 обслуговується
хоча б одним вхідним портом, а кожен порт має реалізацію.

Тест не дає архітектурі мовчки втратити сценарій використання.
"""

from __future__ import annotations

import inspect

from app.application import ports
from app.application.commands import ConfigureRateHandler, IngestDocumentHandler
from app.application.queries import (
    CachedSearchHandler,
    GetAdventureCardHandler,
    GetAuthorCardHandler,
    GetCharacterCardHandler,
    GetLiveStatsHandler,
    GetMonsterCardHandler,
    GetPlayStatisticsHandler,
    GetRecommendationsHandler,
)

QUERY_GROUPS = range(1, 11)

HANDLERS = {
    ports.SearchUseCase: CachedSearchHandler,
    ports.GetAdventureCardUseCase: GetAdventureCardHandler,
    ports.GetMonsterCardUseCase: GetMonsterCardHandler,
    ports.GetCharacterCardUseCase: GetCharacterCardHandler,
    ports.GetAuthorCardUseCase: GetAuthorCardHandler,
    ports.GetRecommendationsUseCase: GetRecommendationsHandler,
    ports.GetPlayStatisticsUseCase: GetPlayStatisticsHandler,
    ports.GetLiveStatsUseCase: GetLiveStatsHandler,
    ports.IngestDocumentUseCase: IngestDocumentHandler,
    ports.ConfigureRateUseCase: ConfigureRateHandler,
}


def _protocol_methods(protocol: type) -> set[str]:
    return {
        name
        for name, member in vars(protocol).items()
        if inspect.isfunction(member) and not name.startswith("_")
    }


def test_every_query_group_is_covered() -> None:
    uncovered = [g for g in QUERY_GROUPS if not ports.QUERY_GROUP_COVERAGE.get(g)]
    assert not uncovered, f"групи без сценарію використання: {uncovered}"


def test_every_inbound_port_has_a_handler() -> None:
    declared = {
        obj
        for obj in vars(ports).values()
        if inspect.isclass(obj)
        and getattr(obj, "_is_protocol", False)
        and obj.__module__ == ports.__name__
    }
    assert declared == set(HANDLERS), declared ^ set(HANDLERS)


def test_handlers_implement_all_port_methods() -> None:
    for port, handler in HANDLERS.items():
        missing = _protocol_methods(port) - set(dir(handler))
        assert not missing, f"{handler.__name__} не реалізує {port.__name__}: {missing}"
