"""Сутності пласту «практика гри»: персонаж, зустріч, участь монстра.

Джерело — записи реальних ігор (п. 2.5, додаток А, таблиці А.9–А.11).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar

from app.domain.values import HpStatus, Provenance, SourceOrigin


@dataclass(slots=True)
class HitPoints:
    """Здоров'я. Розбирається зі складеного рядка `<18/18 HP; Healthy>`."""

    current: int | None = None
    maximum: int | None = None
    status: HpStatus | None = None


@dataclass(slots=True)
class ClassLevel:
    """Пара «клас — рівень». Мультиклас мають 32,8% персонажів."""

    char_class: str
    level: int


@dataclass(slots=True)
class CombatPayload:
    """Корисне навантаження: бойові переліки (додаток А, таблиця А.9).

    У джерелі — рядки через кому, попри назви полів.
    """

    attacks: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    effects: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PlayerCharacter:
    """Персонаж гравця з реально проведеної гри.

    Відбір: учасник із непорожнім полем класу (12,5% від усіх учасників).
    """

    character_id: str
    name: str
    provenance: Provenance

    race: str | None = None
    char_class: str | None = None
    level: int | None = None
    classes_all: list[ClassLevel] = field(default_factory=list)
    hp: HitPoints = field(default_factory=HitPoints)

    # Закляття — вкладений масив назв, не окрема сутність (п. 1.4)
    spells: list[str] = field(default_factory=list)
    spells_srd: list[str] = field(default_factory=list)

    combat_payload: CombatPayload = field(default_factory=CombatPayload)
    encounter_ids: list[str] = field(default_factory=list)
    controller_hash: str | None = None

    @property
    def is_multiclass(self) -> bool:
        """Мультиклас — кілька пар «клас — рівень» (у джерелі роздільник /)."""
        return len(self.classes_all) > 1

    def validate(self) -> list[str]:
        """Обов'язкові: name і char_class — ознака персонажа гравця (п. 2.5.2)."""
        reasons: list[str] = []
        if not self.name:
            reasons.append("name: обов'язкове поле")
        if not self.char_class:
            reasons.append("char_class: обов'язкове поле")
        if self.level is not None and self.level < 1:
            reasons.append("level: менше 1")
        return reasons

    def knows_spell(self, spell_name: str) -> bool:
        wanted = spell_name.strip().lower()
        return any(spell.lower() == wanted for spell in self.spells)


@dataclass(slots=True)
class MonsterParticipation:
    """Участь монстра у зустрічі — асоціативний клас на зв'язку
    «Зустріч — Монстр» (додаток А, таблиця А.11).

    Формується з учасників із порожнім полем класу (87,5%). Власного
    ключа не має: запис ідентифікується парою «зустріч — мітка екземпляра»
    і посилається на обидві сутності. Зберігається окремою колекцією:
    42 803 записи з природним перекосом за монстрами — матеріал для
    перевірки ключа розподілу.
    """

    instance_label: str
    encounter_id: str
    monster_id: str | None = None
    monster_name_raw: str | None = None
    hp: HitPoints = field(default_factory=HitPoints)
    #: Записи участі надходять лише з підготовлених CSV (таблиця А.11)
    provenance: Provenance = field(default_factory=lambda: Provenance(origin=SourceOrigin.CSV))

    @property
    def is_resolved(self) -> bool:
        """Чи зіставлено з довідником (49,7% учасників без класу)."""
        return self.monster_id is not None


@dataclass(slots=True)
class Encounter:
    """Реально проведена бойова сцена."""

    encounter_id: str
    started_at: datetime
    provenance: Provenance

    ended_at: datetime | None = None
    duration_seconds: int | None = None
    dm_hash: str | None = None
    channel_hash: str | None = None
    rounds_count: int | None = None
    participants_count: int | None = None
    player_character_ids: list[str] = field(default_factory=list)
    narrative_text: str | None = None
    commands: list[str] = field(default_factory=list)
    messages_count: int | None = None
    is_abandoned: bool = False

    #: Поріг відсікання викидів (п. 2.5.6): бої понад 8 годин — покинуті сесії
    ABANDONED_THRESHOLD_SECONDS: ClassVar[int] = 8 * 3600

    #: Команди, що свідчать про бойову зустріч (п. 2.5.7)
    COMBAT_COMMANDS: ClassVar[frozenset[str]] = frozenset({"init attack", "cast"})

    def validate(self) -> list[str]:
        """Обов'язкові: encounter_id, started_at."""
        reasons: list[str] = []
        if not self.encounter_id:
            reasons.append("encounter_id: обов'язкове поле")
        if self.started_at is None:
            reasons.append("started_at: обов'язкове поле")
        if self.duration_seconds is not None and self.duration_seconds < 0:
            reasons.append("duration_seconds: від'ємна тривалість")
        return reasons

    def detect_abandoned(self) -> bool:
        """Ознака покинутої сесії.

        Медіана тривалості 12,7 хв, максимум — 50 031 хв (34 доби).
        Такі записи зберігаються, але виключаються зі статистики.
        """
        return self.duration_seconds is not None and self.duration_seconds > self.ABANDONED_THRESHOLD_SECONDS

    def is_combat_heavy(self) -> bool:
        """Класифікація за складом команд, без аналізу тексту (п. 2.5.7).

        Наявність init attack і cast свідчить про бойову зустріч, переважання
        check — про соціальну або дослідницьку.
        """
        combat = sum(1 for command in self.commands if command in self.COMBAT_COMMANDS)
        checks = sum(1 for command in self.commands if command == "check")
        return combat > 0 and combat >= checks
