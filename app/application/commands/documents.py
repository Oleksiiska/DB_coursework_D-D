"""Перетворення документів на шляху запису (таблиця 2.5).

Джерела подають плоскі документи: колонки CSV мають назви полів моделі
(додаток А), значення-переліки й вкладені об'єкти — у форматі JSON.
Тут виконуються трансформації формату — приведення типів і розгортання
вкладених об'єктів у сутності домену, — а також зворотні проєкції сутності
на документ пошукового індексу й на вузли та ребра графа.
"""

from __future__ import annotations

import dataclasses
import enum
import json
import re
import types
import typing
import unicodedata
from datetime import datetime, timezone
from typing import Any

from app.domain.entities import (
    Adventure,
    Author,
    Encounter,
    HitPoints,
    Monster,
    MonsterParticipation,
    PlayerCharacter,
    Setting,
    Tag,
)
from app.domain.values import DocumentKind, LevelRange, Money, PartySize, Provenance, SourceOrigin

#: Сутність кожного виду документа
ENTITY_TYPES: dict[DocumentKind, type] = {
    DocumentKind.ADVENTURE: Adventure,
    DocumentKind.AUTHOR: Author,
    DocumentKind.TAG: Tag,
    DocumentKind.SETTING: Setting,
    DocumentKind.MONSTER: Monster,
    DocumentKind.CHARACTER: PlayerCharacter,
    DocumentKind.ENCOUNTER: Encounter,
    DocumentKind.PARTICIPATION: MonsterParticipation,
}

#: Об'єкти-значення, складові яких у плоскому документі — окремі поля (таблиця А.2)
_NESTED: dict[type, dict[str, tuple[type, dict[str, str]]]] = {
    Adventure: {
        "levels": (LevelRange, {"minimum": "level_min", "maximum": "level_max"}),
        "party": (PartySize, {"minimum": "players_min", "maximum": "players_max"}),
        "price": (Money, {"amount": "price", "currency": "currency"}),
    },
    PlayerCharacter: {"hp": (HitPoints, {"current": "hp_current", "maximum": "hp_max", "status": "hp_status"})},
    MonsterParticipation: {"hp": (HitPoints, {"current": "hp_current", "maximum": "hp_max", "status": "hp_status"})},
}

_TRANSLIT = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "h", "ґ": "g", "д": "d", "е": "e", "є": "ie", "ж": "zh",
    "з": "z", "и": "y", "і": "i", "ї": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n",
    "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "shch", "ь": "", "ю": "iu", "я": "ia", "'": "", "’": "",
    "ы": "y", "э": "e", "ъ": "", "ё": "e",
})


def slugify(text: str) -> str:
    """Транслітерація назви в латиницю (кирилиця — за спрощеною таблицею
    КМУ 2010), нижній регістр, дефіси замість решти символів."""
    text = unicodedata.normalize("NFKD", (text or "").lower().translate(_TRANSLIT))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def parse_datetime(value: Any) -> datetime | None:
    """Час у UTC: ISO-рядок, Unix-час числом або рядком із цифр."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)) or (isinstance(value, str) and re.fullmatch(r"\d+(\.\d+)?", value.strip())):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def coerce(value: Any, hint: Any, name: str = "") -> Any:
    """Значення плоского документа → тип поля сутності.

    Відсутнє значення — None (порожній рядок не зберігається, інакше
    фільтри дали б хибні результати, п. 2.11). Помилка формату — ValueError
    з назвою поля: документ потрапляє в журнал браку.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return _empty(hint)
    origin = typing.get_origin(hint)
    if origin in (typing.Union, types.UnionType):
        options = [a for a in typing.get_args(hint) if a is not type(None)]
        return coerce(value, options[0], name) if len(options) == 1 else value
    try:
        if isinstance(hint, type) and dataclasses.is_dataclass(hint):
            data = json.loads(value) if isinstance(value, str) else value
            return _dataclass(hint, data)
        if isinstance(hint, type) and issubclass(hint, enum.Enum):
            return hint(value)
        if origin in (list, tuple, set, frozenset):
            items = as_list(value)
            args = typing.get_args(hint)
            item_hint = args[0] if args else Any
            converted = [coerce(item, item_hint, name) for item in items]
            return converted if origin is list else origin(converted)
        if origin is dict:
            data = json.loads(value) if isinstance(value, str) else value
            _, item_hint = typing.get_args(hint) or (Any, Any)
            return {str(k): coerce(v, item_hint, name) for k, v in dict(data).items()}
        if hint is bool:
            return _boolean(value)
        if hint is int:
            return int(float(value)) if isinstance(value, str) else int(value)
        if hint is float:
            return float(value)
        if hint is datetime:
            return parse_datetime(value)
        if hint is str:
            return str(value).strip()
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError(f"{name or 'поле'}: невірний формат {value!r}") from error
    return value


def build_entity(kind: DocumentKind, document: dict[str, Any]) -> Any:
    """Плоский документ → сутність виду: приведення типів за анотаціями
    полів, складові об'єктів-значень із окремих полів, походження з origin."""
    cls = ENTITY_TYPES[kind]
    hints = typing.get_type_hints(cls)
    nested = _NESTED.get(cls, {})
    values: dict[str, Any] = {}
    for f in dataclasses.fields(cls):
        if f.name == "provenance":
            try:
                origin = SourceOrigin(document.get("origin"))
            except ValueError:
                raise ValueError(f"origin: невідоме походження {document.get('origin')!r}") from None
            values[f.name] = Provenance(
                origin=origin,
                derived_fields=coerce(document.get("derived_fields"), list[str], "derived_fields") or [],
            )
        elif f.name in nested:
            part, mapping = nested[f.name]
            part_hints = typing.get_type_hints(part)
            given = {sub: document.get(flat) for sub, flat in mapping.items() if document.get(flat) not in (None, "")}
            values[f.name] = part(**{sub: coerce(v, part_hints[sub], mapping[sub]) for sub, v in given.items()})
        else:
            has_default = f.default is not dataclasses.MISSING or f.default_factory is not dataclasses.MISSING
            value = coerce(document.get(f.name), hints[f.name], f.name)
            if value is None and has_default:
                continue  # відсутнє значення — значення поля за замовчуванням
            values[f.name] = value
    return cls(**values)


def assign_natural_id(kind: DocumentKind, document: dict[str, Any]) -> None:
    """Детермінований ключ документа з природного ключа, якщо джерело ключа
    не задало (п. 2.8.3): itch_id — пригода, slug — монстр і світ, profile_url
    або ім'я — автор, нормалізована назва — тег, «ім'я:controller_hash» —
    персонаж. Зустрічі й записи участі ключ мають уже в джерелі."""
    field = f"{kind.value}_id"
    if kind is DocumentKind.PARTICIPATION or document.get(field):
        return
    natural: Any = None
    if kind is DocumentKind.ADVENTURE:
        natural = document.get("itch_id")
    elif kind is DocumentKind.AUTHOR:
        natural = document.get("profile_url") or document.get("name")
    elif kind is DocumentKind.TAG:
        natural = document.get("name")
    elif kind in (DocumentKind.SETTING, DocumentKind.MONSTER):
        natural = document.get("slug")
    elif kind is DocumentKind.CHARACTER and document.get("name"):
        natural = f"{document['name']}:{document.get('controller_hash') or ''}"
    if natural:
        document[field] = kind.natural_id(str(natural))


def entity_id(kind: DocumentKind, entity: Any) -> str:
    """Ключ документа: власний ідентифікатор або пара «зустріч:мітка» для участі."""
    if kind is DocumentKind.PARTICIPATION:
        return f"{entity.encounter_id}:{entity.instance_label}"
    return getattr(entity, f"{kind.value}_id")


def index_document(kind: DocumentKind, entity: Any) -> dict[str, Any]:
    """Документ пошукового індексу: текстові поля й поля фільтрів і
    сортування з таблиці 4.12; `id` — ключ документа в індексі."""
    if kind is DocumentKind.ADVENTURE:
        return {
            "id": entity.adventure_id,
            "title": entity.title,
            "hook": entity.hook,
            "synopsis": entity.synopsis,
            "location_names": list(entity.location_names),
            "full_text": entity.full_text,
            "reviews": [{"text": review.text} for review in entity.reviews],
            "tags": list(entity.tags),
            "system": entity.system,
            "edition": entity.edition,
            "setting_id": entity.setting_id,
            "publication_language": entity.publication_language,
            "status": entity.status,
            "genre": entity.genre,
            "origin": entity.provenance.origin.value,
            "level_min": entity.levels.minimum,
            "level_max": entity.levels.maximum,
            "players_min": entity.party.minimum,
            "players_max": entity.party.maximum,
            "duration_hours": entity.duration_hours,
            "session_count": entity.session_count,
            "difficulty": entity.difficulty,
            "published_year": entity.published_year,
            "price": entity.price.amount,
            "rating_avg": entity.rating_avg,
            "ratings_count": entity.ratings_count,
            "reviews_count": entity.reviews_count(),
            "is_free": entity.is_free,
        }
    if kind is DocumentKind.MONSTER:
        return {
            "id": entity.monster_id,
            "name": entity.name,
            "abilities_text": entity.abilities_text,
            "lore": entity.lore,
            "type": entity.type,
            "subtype": entity.subtype,
            "size": entity.size,
            "alignment": entity.alignment,
            "environment": list(entity.environment),
            "challenge_rating": entity.challenge_rating,
            "play_count": entity.play_count,
        }
    if kind is DocumentKind.ENCOUNTER:
        return {
            "id": entity.encounter_id,
            "narrative_text": entity.narrative_text,
            "commands": sorted(set(entity.commands)),
            "started_at": entity.started_at.isoformat() if entity.started_at else None,
            "duration_seconds": entity.duration_seconds,
            "rounds_count": entity.rounds_count,
            "participants_count": entity.participants_count,
            "is_abandoned": entity.is_abandoned,
        }
    raise ValueError(f"вид {kind.value} не індексується")


def graph_document(kind: DocumentKind, entity: Any) -> dict[str, Any] | None:
    """Вузол і ребра для графового сховища: ідентифікатори, назви й посилання
    (таблиця 4.5). Персонажі гравців у граф не потрапляють, участь — лише
    зіставлена з монстром SRD (п. 1.5)."""
    if kind is DocumentKind.ADVENTURE:
        return {
            "kind": kind.value, "id": entity.adventure_id, "title": entity.title,
            "setting_id": entity.setting_id, "level_min": entity.levels.minimum, "level_max": entity.levels.maximum,
            "author_ids": list(entity.author_ids), "tags": list(entity.tags), "monster_ids": list(entity.monster_ids),
            "prev_adventure_id": entity.prev_adventure_id, "next_adventure_id": entity.next_adventure_id,
        }
    if kind is DocumentKind.AUTHOR:
        return {"kind": kind.value, "id": entity.author_id, "name": entity.name}
    if kind is DocumentKind.TAG:
        return {"kind": kind.value, "id": entity.tag_id, "name": entity.name}
    if kind is DocumentKind.SETTING:
        return {"kind": kind.value, "id": entity.setting_id, "name": entity.name,
                "related_setting_ids": list(entity.related_setting_ids)}
    if kind is DocumentKind.MONSTER:
        return {"kind": kind.value, "id": entity.monster_id, "name": entity.name, "type": entity.type,
                "challenge_rating": entity.challenge_rating}
    if kind is DocumentKind.ENCOUNTER:
        return {"kind": kind.value, "id": entity.encounter_id}
    if kind is DocumentKind.PARTICIPATION and entity.monster_id:
        return {"kind": kind.value, "encounter_id": entity.encounter_id, "monster_id": entity.monster_id,
                "instance_label": entity.instance_label}
    return None


# --- допоміжні перетворення ---


def _empty(hint: Any) -> Any:
    origin = typing.get_origin(hint)
    if origin in (list,):
        return []
    if origin is dict:
        return {}
    return None


def as_list(value: Any) -> list[Any]:
    """Перелік із плоского документа: список, JSON-масив або рядок через «|»."""
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple, set, frozenset)):
        return list(value)
    text = str(value).strip()
    if text.startswith("["):
        return list(json.loads(text))
    return [item.strip() for item in text.split("|") if item.strip()]


def _boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "t", "y"):
        return True
    if text in ("0", "false", "no", "f", "n"):
        return False
    raise ValueError(value)


def _dataclass(cls: type, data: dict[str, Any]) -> Any:
    hints = typing.get_type_hints(cls)
    names = {f.name for f in dataclasses.fields(cls) if f.init}
    return cls(**{k: coerce(v, hints[k], k) for k, v in dict(data).items() if k in names})
