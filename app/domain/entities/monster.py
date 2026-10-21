"""Сутність «Монстр» — довідник і єдиний міст між каталогом
і практикою гри (п. 2.4, додаток А, таблиці А.7–А.8).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.values import Provenance, challenge_window


@dataclass(slots=True)
class Ability:
    """Здібність, дія, легендарна дія або реакція."""

    name: str
    description: str


@dataclass(slots=True)
class StatBlock:
    """Корисне навантаження: бойові характеристики.

    Зберігається єдиним вкладеним об'єктом і в запитах участі не бере
    (додаток А, таблиця А.8). Показується у картці.
    """

    armor_class: int | None = None
    ac_type: str | None = None
    hit_points: int | None = None
    hit_dice: str | None = None
    speed: dict[str, int] = field(default_factory=dict)
    abilities_scores: dict[str, int] = field(default_factory=dict)
    saving_throws: dict[str, int] = field(default_factory=dict)
    skills: dict[str, int] = field(default_factory=dict)
    proficiency_bonus: int | None = None
    damage_resistances: list[str] = field(default_factory=list)
    damage_immunities: list[str] = field(default_factory=list)
    damage_vulnerabilities: list[str] = field(default_factory=list)
    condition_immunities: list[str] = field(default_factory=list)
    senses: dict[str, str] = field(default_factory=dict)
    languages: list[str] = field(default_factory=list)
    special_abilities: list[Ability] = field(default_factory=list)
    actions: list[Ability] = field(default_factory=list)
    legendary_actions: list[Ability] = field(default_factory=list)
    reactions: list[Ability] = field(default_factory=list)


@dataclass(slots=True)
class Monster:
    """Ігрова істота (16 атомарних полів, таблиця А.7)."""

    monster_id: str
    name: str
    type: str
    challenge_rating: float
    provenance: Provenance

    slug: str | None = None
    subtype: str | None = None
    size: str | None = None
    xp: int | None = None
    alignment: str | None = None
    lore: str | None = None
    image_url: str | None = None

    # Обчислювані поля
    environment: list[str] = field(default_factory=list)
    abilities_text: str | None = None
    play_count: int = 0

    statblock: StatBlock = field(default_factory=StatBlock)

    def validate(self) -> list[str]:
        """Обов'язкові: name, type, challenge_rating (п. 2.11, правило 3)."""
        reasons: list[str] = []
        if not self.name:
            reasons.append("name: обов'язкове поле")
        if not self.type:
            reasons.append("type: обов'язкове поле")
        if self.challenge_rating is None:
            reasons.append("challenge_rating: обов'язкове поле")
        elif self.challenge_rating < 0:
            reasons.append("challenge_rating: від'ємне значення")
        return reasons

    def suits_party_level(self, level: int, party_size: int) -> bool:
        """Чи придатний монстр як супротивник партії вказаного рівня."""
        low, high = challenge_window(level, party_size)
        return low <= self.challenge_rating <= high

    def build_abilities_text(self) -> str:
        """Склейка текстів здібностей для пошукового індексу.

        Забезпечує запит «знайти монстра за описом здібності» без
        винесення кожної здібності в окреме поле (додаток А, таблиця А.7).
        """
        block = self.statblock
        parts = [
            f"{ability.name}. {ability.description}".strip()
            for group in (block.special_abilities, block.actions, block.legendary_actions, block.reactions)
            for ability in group
        ]
        return "\n".join(part for part in parts if part)
