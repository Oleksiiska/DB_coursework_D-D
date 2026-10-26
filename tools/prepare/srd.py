"""Крок 1: довідник правил 5e SRD (підрозділ 2.4, таблиці 2.6–2.7, А.7–А.8).

Результат кроку — monsters.csv (play_count дописує крок 3), назви заклять
і службові словники, за якими крок 2 зіставляє учасників і закляття FIREBALL.
Середовище проживання й текст здібностей монстра обчислює застосунок на
шляху запису (таблиця 2.4, крок 7), тому у файлі ці колонки порожні.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.domain.values import DocumentKind
from tools.prepare.csvio import CsvTable
from tools.prepare.report import Measurements, number, percent, share
from tools.prepare.sources import SRD_BASE_URL, SRD_COMMIT, SRD_FILES, download

log = logging.getLogger("prepare.srd")

#: Базова адреса зображень (поле image містить шлях /api/images/…)
IMAGE_BASE_URL = "https://www.dnd5eapi.co"
#: Каталог правил 2024 року — лише для порівняння зіставлення (п. 2.4)
SRD_2024_MONSTERS = "5e-SRD-Monsters.json"
SRD_2024_BASE_URL = "https://raw.githubusercontent.com/5e-bits/5e-database/{commit}/src/2024/en/{name}"

ABILITIES = {"strength": "str", "dexterity": "dex", "constitution": "con",
             "intelligence": "int", "wisdom": "wis", "charisma": "cha"}
DICTIONARIES = ("Alignments", "Races", "Subraces", "Classes", "Languages", "Damage-Types", "Conditions", "Skills")

MONSTER_COLUMNS = (
    "monster_id", "slug", "name", "type", "subtype", "size", "challenge_rating", "xp", "alignment",
    "lore", "image_url", "play_count", "statblock", "derived_fields",
)

_FEET = re.compile(r"(\d+)\s*ft")


def name_key(name: str | None) -> str:
    """Ключ зіставлення назв: пробіли згорнуто, регістр не враховується."""
    return " ".join(str(name or "").split()).casefold()


@dataclass(slots=True)
class Srd:
    """Завантажений знімок SRD."""

    commit: str
    monsters: list[dict]
    spells: list[dict]
    magic_items: list[dict]
    dictionaries: dict[str, list[dict]]
    monsters_2024: list[dict] = field(default_factory=list)

    def monsters_by_name(self) -> dict[str, dict]:
        return {name_key(m["name"]): m for m in self.monsters}

    def monsters_2024_by_name(self) -> dict[str, dict]:
        return {name_key(m["name"]): m for m in self.monsters_2024}

    def spells_by_name(self) -> dict[str, str]:
        """Ключ назви → канонічна назва закляття SRD."""
        return {name_key(s["name"]): s["name"] for s in self.spells}

    def names(self, dictionary: str) -> set[str]:
        return {name_key(entry["name"]) for entry in self.dictionaries.get(dictionary, [])}


def load(raw_dir: Path, *, offline: bool = False, commit: str = SRD_COMMIT) -> Srd:
    """Файли src/2014/en зафіксованого коміту (і монстри src/2024/en для порівняння)."""
    folder = raw_dir / "srd" / commit
    files: dict[str, Any] = {}
    for name in SRD_FILES:
        path = folder / "2014" / name
        if not offline:
            download(SRD_BASE_URL.format(commit=commit, name=name), path)
        files[name] = json.loads(path.read_text(encoding="utf-8"))
    monsters_2024: list[dict] = []
    path_2024 = folder / "2024" / SRD_2024_MONSTERS
    try:
        if not offline:
            download(SRD_2024_BASE_URL.format(commit=commit, name=SRD_2024_MONSTERS), path_2024)
        monsters_2024 = json.loads(path_2024.read_text(encoding="utf-8"))
    except Exception as error:  # noqa: BLE001 — лише для порівняння, крок не зупиняє
        log.warning("монстри src/2024 недоступні (%s): порівняння зіставлення пропущено", error)
    return Srd(
        commit=commit,
        monsters=files["5e-SRD-Monsters.json"],
        spells=files["5e-SRD-Spells.json"],
        magic_items=files["5e-SRD-Magic-Items.json"],
        dictionaries={d: files[f"5e-SRD-{d}.json"] for d in DICTIONARIES},
        monsters_2024=monsters_2024,
    )


# --------------------------------------------------------------------------
# monsters.csv (таблиці А.7, А.8)
# --------------------------------------------------------------------------


def monster_row(record: dict, play_count: int = 0) -> dict[str, Any]:
    """Запис 5e-SRD-Monsters.json → рядок monsters.csv."""
    lore = record.get("desc")
    if isinstance(lore, list):
        lore = "\n".join(str(part) for part in lore)
    return {
        "monster_id": DocumentKind.MONSTER.natural_id(record["index"]),
        "slug": record["index"],
        "name": record["name"],
        "type": record.get("type"),
        "subtype": record.get("subtype") or None,
        "size": record.get("size"),
        "challenge_rating": record.get("challenge_rating"),
        "xp": record.get("xp"),
        "alignment": record.get("alignment"),
        "lore": lore or None,
        "image_url": IMAGE_BASE_URL + record["image"] if record.get("image") else None,
        "play_count": play_count,
        "statblock": statblock(record),
        "derived_fields": ["play_count"],
    }


def statblock(record: dict) -> dict[str, Any]:
    """Корисне навантаження (таблиця А.8) з урахуванням пасток формату (таблиця 2.7)."""
    armor = (record.get("armor_class") or [{}])[0]
    proficiencies = record.get("proficiencies") or []
    return {
        "armor_class": armor.get("value"),
        "ac_type": armor.get("type"),
        "hit_points": record.get("hit_points"),
        "hit_dice": record.get("hit_dice"),
        "speed": speed(record.get("speed") or {}),
        "abilities_scores": {short: record[full] for full, short in ABILITIES.items() if full in record},
        "saving_throws": _proficiencies(proficiencies, "saving-throw-"),
        "skills": _proficiencies(proficiencies, "skill-"),
        "proficiency_bonus": record.get("proficiency_bonus"),
        "damage_resistances": list(record.get("damage_resistances") or []),
        "damage_immunities": list(record.get("damage_immunities") or []),
        "damage_vulnerabilities": list(record.get("damage_vulnerabilities") or []),
        "condition_immunities": [c["name"] if isinstance(c, dict) else str(c)
                                 for c in record.get("condition_immunities") or []],
        "senses": {key: str(value) for key, value in (record.get("senses") or {}).items()},
        "languages": split_languages(record.get("languages")),
        "special_abilities": _abilities(record.get("special_abilities")),
        "actions": _abilities(record.get("actions")),
        "legendary_actions": _abilities(record.get("legendary_actions")),
        "reactions": _abilities(record.get("reactions")),
    }


def speed(raw: dict[str, Any]) -> dict[str, int]:
    """«40 ft.» → 40. Ознака hover (булева) числом не є й відкидається."""
    result: dict[str, int] = {}
    for mode, value in raw.items():
        if isinstance(value, bool):
            continue
        match = _FEET.search(str(value))
        if match:
            result[mode] = int(match.group(1))
    return result


def split_languages(text: str | None) -> list[str]:
    """Рядок мов → масив: розбиття за комою, але зворот «understands A, B,
    and C but can't speak» лишається одним елементом — інакше він розпався б
    на шматки, жоден з яких не є мовою."""
    items: list[str] = []
    for part in (text or "").split(","):
        part = part.strip()
        if not part or part in ("—", "-"):
            continue
        if items and "understands" in items[-1] and " but " not in f" {items[-1]} ":
            items[-1] = f"{items[-1]}, {part}"
        else:
            items.append(part)
    return items


def _proficiencies(proficiencies: list[dict], prefix: str) -> dict[str, int]:
    """Спільний масив proficiencies розділяється за префіксом ідентифікатора."""
    result: dict[str, int] = {}
    for entry in proficiencies:
        index = (entry.get("proficiency") or {}).get("index", "")
        if index.startswith(prefix):
            result[index[len(prefix):]] = entry.get("value")
    return result


def _abilities(entries: list[dict] | None) -> list[dict[str, str]]:
    return [{"name": e.get("name", ""), "description": e.get("desc", "")} for e in entries or []]


def write_monsters(srd: Srd, path: Path, play_counts: Counter | None = None) -> int:
    play_counts = play_counts or Counter()
    with CsvTable(path, MONSTER_COLUMNS) as table:
        for record in srd.monsters:
            table.write(monster_row(record, play_counts.get(record["index"], 0)))
    log.info("%s: %d рядків", path.name, table.rows)
    return table.rows


# --------------------------------------------------------------------------
# Виміри (таблиці 2.1, 2.6, 2.7; п. 2.4.3)
# --------------------------------------------------------------------------

COVERAGE_FIELDS = (
    "actions", "special_abilities", "subtype", "desc", "legendary_actions", "reactions",
)
REPORTED_COVERAGE = {"actions": "99%", "special_abilities": "83%", "subtype": "22%", "desc": "13% (lore)",
                     "legendary_actions": "9%", "reactions": "3%"}
REPORTED_DICTIONARIES = {"Alignments": "9", "Races": "9", "Subraces": "4", "Classes": "12", "Languages": "16",
                         "Damage-Types": "13", "Conditions": "15", "Skills": "18"}


def measure(srd: Srd, m: Measurements) -> None:
    key = "srd"
    m.section(key, "Крок 1. 5e SRD (підрозділ 2.4)")
    total = len(srd.monsters)
    m.add(key, "табл. 2.1", "монстри / закляття / магічні предмети",
          f"{number(total)} / {number(len(srd.spells))} / {number(len(srd.magic_items))}", "334 / 319 / 362")
    m.add(key, "п. 2.9", "записів у повному проході", number(total + len(srd.spells) + len(srd.magic_items)), "1 015")
    m.add(key, "п. 2.4", "монстрів у src/2024/en того самого коміту",
          number(len(srd.monsters_2024)) if srd.monsters_2024 else "файл недоступний", "3")
    for name in COVERAGE_FIELDS:
        filled = sum(1 for record in srd.monsters if record.get(name))
        m.add(key, "табл. 2.6", f"покриття {name}", share(filled, total), REPORTED_COVERAGE[name])
    # порожній перелік чи рядок (немає стійкостей, мов) — значення, а не пропуск
    others = sorted({k for record in srd.monsters for k in record} - set(COVERAGE_FIELDS) - {"forms"})
    present = {k: sum(1 for r in srd.monsters if r.get(k) is not None) for k in others}
    incomplete = [f"{k} {percent(n, total)}" for k, n in present.items() if n < total]
    m.add(key, "п. 2.4.1", "решта полів, заповнених не в усіх записах",
          ", ".join(incomplete) or "немає — 100%", "немає (100%)")
    m.add(key, "п. 2.8.1", "поле environment у SRD",
          f"{number(sum(1 for r in srd.monsters if 'environment' in r))} з {number(total)}", "0 із 334")

    # пастки формату (таблиця 2.7)
    multi_ac = sum(1 for r in srd.monsters if len(r.get("armor_class") or []) > 1)
    m.add(key, "табл. 2.7", "armor_class — масив об'єктів",
          f"у {number(sum(1 for r in srd.monsters if isinstance(r.get('armor_class'), list)))} записах; "
          f"кілька елементів — {number(multi_ac)}")
    m.add(key, "табл. 2.7", "languages — один рядок",
          f"рядок у {number(sum(1 for r in srd.monsters if isinstance(r.get('languages'), str)))} записах")
    saves = sum(1 for r in srd.monsters for p in r.get("proficiencies") or []
                if p["proficiency"]["index"].startswith("saving-throw-"))
    skills = sum(1 for r in srd.monsters for p in r.get("proficiencies") or []
                 if p["proficiency"]["index"].startswith("skill-"))
    m.add(key, "табл. 2.7", "proficiencies: рятівні кидки / навички", f"{number(saves)} / {number(skills)}")
    with_units = sum(1 for r in srd.monsters for v in (r.get("speed") or {}).values()
                     if isinstance(v, str) and "ft" in v)
    m.add(key, "табл. 2.7", "speed — рядки з одиницями", f"{number(with_units)} значень; hover (булеве) — "
          f"{number(sum(1 for r in srd.monsters if isinstance((r.get('speed') or {}).get('hover'), bool)))}")

    # службові словники (п. 2.4.3)
    sizes = ", ".join(f"{d} ({number(len(srd.dictionaries.get(d, [])))})" for d in DICTIONARIES)
    m.add(key, "п. 2.4.3", "службові словники", sizes,
          ", ".join(f"{d} ({v})" for d, v in REPORTED_DICTIONARIES.items()))
    canonical = srd.names("Alignments")
    aligned = Counter(r.get("alignment") for r in srd.monsters)
    valid = sum(count for value, count in aligned.items() if name_key(value) in canonical)
    m.add(key, "табл. А.7", "alignment у словнику Alignments", share(valid, total))
    m.note(key, "Значення alignment поза словником (зберігаються без змін): "
           + ", ".join(f"{v} ({number(c)})" for v, c in aligned.most_common() if name_key(v) not in canonical) + ".")
    languages = Counter(len(split_languages(r.get("languages"))) for r in srd.monsters)
    m.note(key, "Кількість мов монстра після розбиття → записів: " + ", ".join(
        f"{n} → {number(c)}" for n, c in sorted(languages.items())) + ". Порожній рядок languages означає, що "
        "мов у істоти немає, і вважається значенням, як і порожні переліки стійкостей.")

