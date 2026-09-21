"""Сутності пласту «практика гри»: персонаж, зустріч, участь монстра.

Джерело — записи реальних ігор.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.domain.values import HpStatus, Provenance


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
    """Корисне навантаження: бойові переліки.

    У джерелі — рядки через кому, попри назви полів.
    """

    attacks: list[str] = field(default_factory=list)
    abilities: list[str] = field(default_factory=list)
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

    # Закляття — вкладений масив назв, не окрема сутність (п. 2.4.1)
    spells: list[str] = field(default_factory=list)
    spells_srd: list[str] = field(default_factory=list)

    combat: CombatPayload = field(default_factory=CombatPayload)
    encounter_ids: list[str] = field(default_factory=list)
    controller_hash: str | None = None

    @property
    def is_multiclass(self) -> bool:
        raise NotImplementedError

    def validate(self) -> list[str]:
        raise NotImplementedError

    def knows_spell(self, spell_name: str) -> bool:
        raise NotImplementedError


@dataclass(slots=True)
class MonsterParticipation:
    """Участь монстра у зустрічі — асоціативний клас.

    Формується з учасників із порожнім полем класу (87,5%).
    """

    encounter_id: str
    instance_label: str
    monster_id: str | None = None
    monster_name_raw: str | None = None
    hp: HitPoints = field(default_factory=HitPoints)

    @property
    def is_resolved(self) -> bool:
        """Чи зіставлено з довідником (49,7% учасників без класу)."""
        raise NotImplementedError


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
    participations: list[MonsterParticipation] = field(default_factory=list)
    narrative_text: str | None = None
    commands: list[str] = field(default_factory=list)
    messages_count: int | None = None
    is_abandoned: bool = False

    #: Поріг відсікання викидів (п. 3.5.6): бої понад 8 годин — покинуті сесії
    ABANDONED_THRESHOLD_SECONDS: int = 8 * 3600

    def validate(self) -> list[str]:
        """Обов'язкові: encounter_id, started_at."""
        raise NotImplementedError

    def detect_abandoned(self) -> bool:
        """Ознака покинутої сесії.

        Медіана тривалості 12,7 хв, максимум — 50 031 хв (34 доби).
        Такі записи зберігаються, але виключаються зі статистики.
        """
        raise NotImplementedError

    def is_combat_heavy(self) -> bool:
        """Класифікація за складом команд, без аналізу тексту (п. 3.5.7)."""
        raise NotImplementedError
