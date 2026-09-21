"""Нормалізація значень за контрольованими словниками.

Словники рас і класів будуються з самих даних, а не з довідника правил:
у реальній грі трапляються значення з видань поза ним.
"""

from __future__ import annotations


class VocabularyNormalizer:
    """Зведення варіантів написання до канонічного значення.

    Приклад із даних: `Blood Hunter` і `Bloodhunter` — один клас.
    Без зведення в графі виникло б два вузли.
    """

    def __init__(self, aliases: dict[str, str] | None = None) -> None:
        self._aliases = aliases or {}
        self._unknown: list[str] = []

    def normalize(self, vocabulary: str, raw: str) -> str | None:
        """Канонічне значення або None, якщо не розпізнано."""
        raise NotImplementedError

    def register_unknown(self, vocabulary: str, raw: str) -> None:
        """Журнал нерозпізнаних значень для доповнення словника."""
        raise NotImplementedError

    @property
    def unknown_terms(self) -> list[str]:
        raise NotImplementedError
