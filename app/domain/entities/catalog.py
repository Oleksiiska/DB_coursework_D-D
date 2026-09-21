"""Сутності каталогу: автор, тег, ігровий світ (додаток А.2–А.4)."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.values import Provenance, TagCategory


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
        raise NotImplementedError


@dataclass(slots=True)
class Tag:
    """Тема. Контрольований словник із нормалізацією за псевдонімами."""

    tag_id: str
    name: str
    category: TagCategory | None = None
    description: str | None = None
    aliases: list[str] = field(default_factory=list)
    adventures_count: int = 0

    def matches_alias(self, raw: str) -> bool:
        """Зіставлення сирого значення з довідником."""
        raise NotImplementedError


@dataclass(slots=True)
class Setting:
    """Ігровий світ. Єдина сутність, що формується вручну."""

    setting_id: str
    name: str
    description: str | None = None
    genre: str | None = None
    tone: str | None = None
    related_setting_ids: list[str] = field(default_factory=list)
    adventures_count: int = 0
