"""Валідація документів на шляху запису (п. 2.11)."""

from __future__ import annotations

from typing import Any

from app.domain.errors import ValidationError


class DocumentValidator:
    """Перевірка обов'язкових полів і базових інваріантів.

    Документ без обов'язкових полів вважається браком і потрапляє
    в журнал, решта конвеєра його не бачить.
    """

    REQUIRED: dict[str, tuple[str, ...]] = {
        "adventure": ("title", "synopsis", "origin"),
        "monster": ("name", "type", "challenge_rating"),
        "encounter": ("encounter_id", "started_at"),
        "character": ("name", "char_class"),
    }

    def validate(self, kind: str, document: dict[str, Any]) -> None:
        """Кидає ValidationError, якщо документ непридатний."""
        raise NotImplementedError

    def check(self, kind: str, document: dict[str, Any]) -> list[str]:
        """Нейтральна перевірка без винятку — для пакетної обробки."""
        raise NotImplementedError
