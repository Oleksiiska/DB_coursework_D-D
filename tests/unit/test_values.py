"""Об'єкти-значення й правила домену."""

from __future__ import annotations

import uuid

from app.domain.entities import Adventure, Encounter
from app.domain.values import (
    AdventureFilters,
    DocumentKind,
    EncounterFilters,
    LevelRange,
    Money,
    MonsterFilters,
    Page,
    PartySize,
    Provenance,
    SourceOrigin,
    challenge_window,
)


def test_unknown_parameters_do_not_match_filters() -> None:
    """Таблиця 1.5: запис без рівня доступний через пошук, але не потрапляє
    у видачу з фільтром за рівнем."""
    assert not LevelRange().covers(3) and LevelRange(2, 4).covers(3)
    assert not PartySize(None, 5).fits(4)
    adventure = Adventure("a", "Title", None, Provenance(SourceOrigin.CRAWLER))
    assert adventure.matches(AdventureFilters())
    assert not adventure.matches(AdventureFilters(level=3))


def test_is_free_when_price_is_zero_or_missing() -> None:
    assert Money().is_free and Money(0.0).is_free and not Money(4.99).is_free


def test_filters_emptiness() -> None:
    assert AdventureFilters().is_empty() and MonsterFilters().is_empty()
    assert EncounterFilters().is_empty()  # виключення покинутих сесій — політика, а не фільтр
    assert not EncounterFilters(exclude_abandoned=False).is_empty()
    assert not AdventureFilters(tags=("horror",)).is_empty()


def test_party_level_narrows_challenge_rating() -> None:
    low, high = challenge_window(4)
    assert (low, high) == (1.0, 6.0)
    assert MonsterFilters(party_level=4, challenge_max=3).challenge_bounds() == (1.0, 3)


def test_page_offset() -> None:
    assert Page().size == 20 and Page(number=3).offset == 40


def test_natural_ids_are_deterministic_per_kind() -> None:
    """П. 2.8.3: UUIDv5 від природного ключа в просторі імен виду."""
    namespace = uuid.uuid5(uuid.NAMESPACE_URL, "https://github.com/Oleksiiska/DB_coursework_D-D/settings")
    assert DocumentKind.SETTING.natural_id("eberron") == str(uuid.uuid5(namespace, "eberron"))
    assert DocumentKind.TAG.natural_id("horror") == DocumentKind.TAG.natural_id("horror")
    assert DocumentKind.TAG.natural_id("horror") != DocumentKind.AUTHOR.natural_id("horror")


def test_abandoned_encounter_threshold() -> None:
    from datetime import datetime, timezone

    encounter = Encounter("e", datetime.now(timezone.utc), Provenance(SourceOrigin.CSV), duration_seconds=8 * 3600 + 1)
    assert encounter.detect_abandoned()
    encounter.duration_seconds = 760
    assert not encounter.detect_abandoned()


def test_adventure_references_must_not_be_empty() -> None:
    """Посилання без ідентифікатора відхиляється, а не зберігається як null:
    картка не веде на неіснуючий документ."""
    adventure = Adventure("a", "Title", None, Provenance(SourceOrigin.CRAWLER), monster_ids=[None], tags=["horror", ""])
    assert adventure.validate() == ["monster_ids: порожнє значення в переліку", "tags: порожнє значення в переліку"]
    assert Adventure("a", "Title", None, Provenance(SourceOrigin.CRAWLER), monster_ids=["m1"]).validate() == []
