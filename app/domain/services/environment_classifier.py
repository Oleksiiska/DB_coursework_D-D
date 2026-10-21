"""Класифікатор середовища проживання монстра.

Поле відсутнє в джерелі (0 із 334 записів), тому визначається
правилами на етапі завантаження (п. 2.8.1).
"""

from __future__ import annotations

import re

from app.domain.entities import Monster


class EnvironmentClassifier:
    """Доменна служба: правила виведення середовища."""

    #: Замкнений перелік середовищ
    VOCABULARY: tuple[str, ...] = (
        "arctic", "coastal", "desert", "forest", "graveyard", "grassland", "hill",
        "mountain", "swamp", "underdark", "underwater", "urban",
    )

    #: Правило 1: середовища, які однозначно випливають із типу істоти
    #: (нежить → підземелля, цвинтар)
    BY_TYPE: dict[str, tuple[str, ...]] = {
        "undead": ("underdark", "graveyard"),
        "ooze": ("underdark",),
        "aberration": ("underdark",),
    }

    #: Правило 2: ключі поля speed (swim → водні, fly → гірські, burrow → підземні)
    BY_MOVEMENT: dict[str, tuple[str, ...]] = {
        "swim": ("underwater", "coastal"),
        "fly": ("mountain",),
        "burrow": ("underdark",),
    }

    #: Правило 3: ключові слова в описі, особливих здібностях і діях
    BY_KEYWORD: dict[str, tuple[str, ...]] = {
        "arctic": ("arctic", "snow", "glacier", "tundra", "frozen", "ice"),
        "coastal": ("coast", "shore", "beach", "reef", "sea cave"),
        "desert": ("desert", "dune", "sand", "oasis"),
        "forest": ("forest", "woodland", "jungle", "tree"),
        "graveyard": ("graveyard", "cemetery", "crypt", "tomb", "grave"),
        "grassland": ("grassland", "plain", "prairie", "savanna", "meadow"),
        "hill": ("hill", "foothill", "downs"),
        "mountain": ("mountain", "peak", "cliff", "crag"),
        "swamp": ("swamp", "marsh", "bog", "fen", "mire"),
        "underdark": ("underdark", "cavern", "cave", "subterranean", "sunlight sensitivity"),
        "underwater": ("underwater", "ocean", "water breathing", "amphibious", "depths"),
        "urban": ("city", "town", "sewer", "village", "urban"),
    }

    #: Правило 4: значення за замовчуванням для типу істоти
    DEFAULT_BY_TYPE: dict[str, tuple[str, ...]] = {
        "aberration": ("underdark",),
        "beast": ("forest", "grassland"),
        "celestial": ("mountain",),
        "construct": ("urban",),
        "dragon": ("mountain",),
        "elemental": ("mountain",),
        "fey": ("forest",),
        "fiend": ("underdark",),
        "giant": ("hill", "mountain"),
        "humanoid": ("urban",),
        "monstrosity": ("hill",),
        "ooze": ("underdark",),
        "plant": ("forest",),
        "undead": ("graveyard",),
    }

    _KEYWORD_PATTERNS = {
        environment: re.compile(r"\b(?:" + "|".join(re.escape(w) for w in words) + r")s?\b", re.IGNORECASE)
        for environment, words in BY_KEYWORD.items()
    }

    def classify(self, monster: Monster) -> list[str]:
        """Послідовне застосування чотирьох правил.

        1. За типом істоти (нежить → підземелля, цвинтар).
        2. За ключами швидкості (swim → водні, fly → гірські, burrow → підземні).
        3. За ключовими словами в описах і діях.
        4. Інакше — значення за замовчуванням для типу.

        Перші три правила доповнюють одне одного; четверте застосовується,
        лише якщо вони нічого не дали, тож покриття — 100%.
        """
        found = self._by_creature_type(monster) | self._by_movement(monster) | self._by_keywords(monster)
        if not found:
            found = set(self.DEFAULT_BY_TYPE.get(self._type(monster), ("urban",)))
        return sorted(found)

    @staticmethod
    def _type(monster: Monster) -> str:
        return (monster.type or "").strip().lower().split(" ")[0]

    def _by_creature_type(self, monster: Monster) -> set[str]:
        return set(self.BY_TYPE.get(self._type(monster), ()))

    def _by_movement(self, monster: Monster) -> set[str]:
        found: set[str] = set()
        for mode, value in monster.statblock.speed.items():
            if value:
                found.update(self.BY_MOVEMENT.get(mode.lower(), ()))
        return found

    def _by_keywords(self, monster: Monster) -> set[str]:
        block = monster.statblock
        texts = [monster.lore or ""]
        for group in (block.special_abilities, block.actions):
            texts.extend(f"{ability.name} {ability.description}" for ability in group)
        text = "\n".join(texts)
        return {environment for environment, pattern in self._KEYWORD_PATTERNS.items() if pattern.search(text)}
