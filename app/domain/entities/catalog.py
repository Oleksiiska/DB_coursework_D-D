"""Сутності каталогу: автор, тег, ігровий світ (додаток А, таблиці А.4–А.6)."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.values import Provenance, SourceOrigin, TagCategory


@dataclass(slots=True)
class Author:
    """Автор модуля."""

    author_id: str
    name: str
    provenance: Provenance

    bio: str | None = None
    platform: str | None = None
    profile_url: str | None = None

    # Обчислювані
    adventures_count: int = 0
    avg_rating: float | None = None
    first_published_year: int | None = None
    co_author_ids: list[str] = field(default_factory=list)

    def validate(self) -> list[str]:
        """Обов'язкові: name і походження."""
        reasons: list[str] = []
        if not self.name:
            reasons.append("name: обов'язкове поле")
        if self.provenance is None or self.provenance.origin is None:
            reasons.append("origin: обов'язкове поле")
        return reasons


@dataclass(slots=True)
class Tag:
    """Тема. Контрольований словник із нормалізацією за псевдонімами."""

    tag_id: str
    name: str
    #: csv або crawler — константа адаптера (таблиця А.5)
    provenance: Provenance = field(default_factory=lambda: Provenance(origin=SourceOrigin.CSV))
    category: TagCategory | None = None
    description: str | None = None
    aliases: list[str] = field(default_factory=list)
    adventures_count: int = 0

    def matches_alias(self, raw: str) -> bool:
        """Зіставлення сирого значення з довідником (п. 2.11, правило 4)."""
        value = raw.strip().lower()
        return value == self.name.lower() or value in {alias.lower() for alias in self.aliases}


@dataclass(slots=True)
class Setting:
    """Ігровий світ. Єдина сутність, що формується вручну."""

    setting_id: str
    name: str
    #: Світи складено вручну (app/data/settings.json, таблиця А.6)
    provenance: Provenance = field(default_factory=lambda: Provenance(origin=SourceOrigin.MANUAL))
    description: str | None = None
    genre: str | None = None
    tone: str | None = None
    related_setting_ids: list[str] = field(default_factory=list)
    adventures_count: int = 0
