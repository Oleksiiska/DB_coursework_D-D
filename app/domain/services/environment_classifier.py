"""Класифікатор середовища проживання монстра.

Поле відсутнє в джерелі (0 із 334 записів), тому визначається
правилами на етапі завантаження (п. 2.8.1).
"""

from __future__ import annotations

from app.domain.entities import Monster


class EnvironmentClassifier:
    """Доменна служба: правила виведення середовища."""

    #: Замкнений перелік середовищ
    VOCABULARY: tuple[str, ...] = (
        "arctic", "coastal", "desert", "forest", "grassland", "hill",
        "mountain", "swamp", "underdark", "underwater", "urban",
    )

    def classify(self, monster: Monster) -> list[str]:
        """Послідовне застосування чотирьох правил.

        1. За типом істоти (нежить → підземелля, цвинтар).
        2. За ключами швидкості (swim → водні, fly → гірські).
        3. За ключовими словами в описах і діях.
        4. Інакше — значення за замовчуванням для типу.
        """
        raise NotImplementedError

    def _by_creature_type(self, monster: Monster) -> set[str]:
        raise NotImplementedError

    def _by_movement(self, monster: Monster) -> set[str]:
        raise NotImplementedError

    def _by_keywords(self, monster: Monster) -> set[str]:
        raise NotImplementedError
