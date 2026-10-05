"""Відображення сутностей на документи сховища й назад — без сховища."""

from __future__ import annotations

from datetime import datetime, timezone

from app.domain.entities import (
    Ability,
    ClassLevel,
    Encounter,
    HitPoints,
    Monster,
    MonsterParticipation,
    PlayerCharacter,
    StatBlock,
)
from app.domain.values import HpStatus, Provenance, SourceOrigin
from app.infrastructure.persistence import DataclassDocumentMapper
from app.infrastructure.config import Container
from app.domain.values import DocumentKind
from workers.storage_check import StorageCheck


def round_trip(entity, key_fields):
    mapper = DataclassDocumentMapper(type(entity), key_fields)
    document = mapper.to_document(entity)
    return document, mapper.to_entity(document)


def test_adventure_with_nested_values_survives_round_trip() -> None:
    adventure = StorageCheck.sample()
    document, restored = round_trip(adventure, ("adventure_id",))
    assert restored == adventure
    assert document["_id"] == adventure.adventure_id
    assert document["provenance"]["origin"] == "generated"  # перелік — значенням
    assert document["levels"] == {"minimum": 1, "maximum": 3}  # вкладений об'єкт
    assert document["reviews"][0]["author_name"] == "Ведучий"


def test_monster_statblock_with_nested_lists() -> None:
    monster = Monster(
        monster_id="goblin",
        name="Goblin",
        type="humanoid",
        challenge_rating=0.25,
        provenance=Provenance(origin=SourceOrigin.CSV),
        statblock=StatBlock(
            armor_class=15,
            speed={"walk": 30},
            actions=[Ability(name="Scimitar", description="Melee Weapon Attack")],
        ),
    )
    _, restored = round_trip(monster, ("monster_id",))
    assert restored == monster
    assert isinstance(restored.statblock.actions[0], Ability)


def test_player_character_and_encounter() -> None:
    character = PlayerCharacter(
        character_id="pc-1",
        name="Aria",
        provenance=Provenance(origin=SourceOrigin.CSV),
        classes_all=[ClassLevel(char_class="Wizard", level=3)],
        hp=HitPoints(current=12, maximum=18, status=HpStatus.INJURED),
        spells=["Magic Missile"],
    )
    _, restored = round_trip(character, ("character_id",))
    assert restored == character
    assert restored.hp.status is HpStatus.INJURED

    encounter = Encounter(
        encounter_id="enc-1",
        started_at=datetime(2021, 5, 1, 18, 30, tzinfo=timezone.utc),
        provenance=Provenance(origin=SourceOrigin.CSV),
        player_character_ids=["pc-1"],
    )
    _, restored = round_trip(encounter, ("encounter_id",))
    assert restored == encounter


def test_association_class_has_composite_key() -> None:
    participation = MonsterParticipation(instance_label="SH1", encounter_id="enc-1", monster_id="skeleton")
    document, restored = round_trip(participation, ("encounter_id", "instance_label"))
    assert document["_id"] == "enc-1:SH1"
    assert restored == participation


def test_every_document_kind_has_a_binding() -> None:
    """Вид документа без прив'язки означав би колекцію, яку нема чим записати."""
    assert set(Container.ENTITY_BINDINGS) == set(DocumentKind)
