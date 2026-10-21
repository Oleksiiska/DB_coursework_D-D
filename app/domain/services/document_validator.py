"""Валідація документів на шляху запису (п. 2.11)."""

from __future__ import annotations

from typing import Any

from app.domain.errors import ValidationError


class DocumentValidator:
    """Перевірка обов'язкових полів і базових інваріантів.

    Документ без обов'язкових полів вважається браком і потрапляє
    в журнал, решта конвеєра його не бачить. Відсутнє значення — це
    None або порожній рядок: інакше фільтри дали б хибні результати.
    """

    REQUIRED: dict[str, tuple[str, ...]] = {
        "adventure": ("title", "origin"),
        "monster": ("name", "type", "challenge_rating"),
        "encounter": ("encounter_id", "started_at"),
        "character": ("name", "char_class"),
        # Поля, без яких документ не має ключа чи назви (покриття 100%, додаток А)
        "participation": ("encounter_id", "instance_label"),
        "author": ("name",),
        "tag": ("name",),
        "setting": ("name",),
    }

    #: Додаткові обов'язкові поля за походженням документа: синопсис
    #: генератор формує завжди, а в каталозі він є лише в 96% карток,
    #: і картку без нього не відкидають (п. 2.11)
    REQUIRED_BY_ORIGIN: dict[tuple[str, str], tuple[str, ...]] = {
        ("adventure", "generated"): ("synopsis",),
    }

    def validate(self, kind: str, document: dict[str, Any]) -> None:
        """Кидає ValidationError, якщо документ непридатний."""
        reasons = self.check(kind, document)
        if reasons:
            raise ValidationError(kind, reasons)

    def check(self, kind: str, document: dict[str, Any]) -> list[str]:
        """Нейтральна перевірка без винятку — для пакетної обробки."""
        if kind not in self.REQUIRED:
            return [f"kind: невідомий вид документа {kind!r}"]
        required = self.REQUIRED[kind] + self.REQUIRED_BY_ORIGIN.get((kind, str(document.get("origin"))), ())
        return [f"{name}: обов'язкове поле" for name in required if self._missing(document.get(name))]

    @staticmethod
    def _missing(value: Any) -> bool:
        return value is None or (isinstance(value, str) and not value.strip())
