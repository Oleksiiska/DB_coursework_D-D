"""Нормалізація значень за контрольованими словниками.

Словники рас і класів будуються з самих даних, а не з довідника правил:
у реальній грі трапляються значення з видань поза ним (п. 2.5.3).
"""

from __future__ import annotations

import re


class VocabularyNormalizer:
    """Зведення варіантів написання до канонічного значення.

    Приклад із даних: `Blood Hunter` і `Bloodhunter` — один клас.
    Без зведення в графі виникло б два вузли.

    Словники:
      * tag — теги каталогу; псевдоніми — зі списку вручну
        (app/data/tag_aliases.json), назва зводиться до нижнього регістру
        з дефісами, як у мітках itch.io;
      * class — класи персонажів: псевдоніми з даних і перелік відомих
        класів (дванадцять класів SRD і класи, виміряні в FIREBALL);
        невідомий клас зберігається, але потрапляє в журнал нерозпізнаних;
      * race — раси персонажів: словник будується з даних, тож значення
        лише приводиться до єдиного написання.
    """

    #: Класи, виміряні в даних (п. 2.5.3): дванадцять класів SRD, Artificer і Blood Hunter
    KNOWN_CLASSES: frozenset[str] = frozenset(
        {
            "Barbarian", "Bard", "Cleric", "Druid", "Fighter", "Monk", "Paladin",
            "Ranger", "Rogue", "Sorcerer", "Warlock", "Wizard", "Artificer", "Blood Hunter",
        }
    )
    #: Варіанти написання класів (Bloodhunter → Blood Hunter)
    CLASS_ALIASES: dict[str, str] = {"bloodhunter": "Blood Hunter", "blood-hunter": "Blood Hunter"}

    def __init__(self, aliases: dict[str, str] | None = None) -> None:
        #: Псевдонім тегу (нижній регістр) → канонічний тег
        self._aliases = {self._tag_form(k): v for k, v in (aliases or {}).items()}
        self._unknown: list[tuple[str, str]] = []

    def normalize(self, vocabulary: str, raw: str) -> str | None:
        """Канонічне значення або None, якщо значення порожнє."""
        if raw is None or not str(raw).strip():
            return None
        value = str(raw).strip()
        if vocabulary == "tag":
            form = self._tag_form(value)
            return self._aliases.get(form, form)
        if vocabulary == "class":
            canonical = self.CLASS_ALIASES.get(value.lower()) or self._title(value)
            if canonical not in self.KNOWN_CLASSES:
                self.register_unknown(vocabulary, canonical)
            return canonical
        return self._title(value)

    def is_canonical_tag(self, tag: str) -> bool:
        """Чи є тег канонічною назвою зі списку псевдонімів."""
        return tag in set(self._aliases.values())

    def register_unknown(self, vocabulary: str, raw: str) -> None:
        """Журнал нерозпізнаних значень для доповнення словника."""
        entry = (vocabulary, raw)
        if entry not in self._unknown:
            self._unknown.append(entry)

    def drain_unknown(self) -> list[tuple[str, str]]:
        """Нерозпізнані значення, накопичені з останнього виклику (для журналу якості)."""
        entries, self._unknown = self._unknown, []
        return entries

    @property
    def unknown_terms(self) -> list[str]:
        return [f"{vocabulary}:{term}" for vocabulary, term in self._unknown]

    @staticmethod
    def _tag_form(value: str) -> str:
        return re.sub(r"[\s_]+", "-", value.strip().lower())

    @staticmethod
    def _title(value: str) -> str:
        """Єдине написання: пробіли згорнуто, слова з великої літери
        (half-elf → Half-Elf); слова змішаного регістру не змінюються."""

        def word(w: str) -> str:
            return w[:1].upper() + w[1:].lower() if w.islower() or w.isupper() else w

        parts = re.split(r"(\s+|-)", re.sub(r"\s+", " ", value.strip()))
        return "".join(part if part in (" ", "-") else word(part) for part in parts)
