"""Кроки 2–3: практика гри (FIREBALL) і популярність монстрів.

Підрозділ 2.5, таблиці 2.8–2.11, А.9–А.11. Архів не розпаковується: tar.gz
читається потоково двома окремими проходами (п. 2.5.1):

1. каталог ``filtered`` — трійки «до / команда / після» зі станом бою; з них
   беруться учасники: персонажі гравців (``characters.csv``) і екземпляри
   монстрів (``participations.csv``);
2. каталог ``data`` — сирі події бою; з них беруться метадані зустрічі й
   наратив ведучого (``encounters.csv``).

Другий прохід потребує результатів першого: зустріч посилається на персонажів
гравців, а сирі події читаються лише для боїв, що мають трійки.

Формат стану актора — нормалізація авторів датасету (FIREBALL-data-processing,
distill4_normalize.py): ``hp`` має вигляд ``<cur/max HP; Status>``, де статус
обчислено з частки здоров'я; без максимуму — ``<cur HP>``; тимчасові очки
дописуються після дужки як `` (+N temp)``; ``class`` — рядок рівнів Avrae
``Fighter 2/Warlock 3``, порожній у монстрів.
"""

from __future__ import annotations

import gzip
import io
import json
import logging
import re
import tarfile
import time
import zlib
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, NamedTuple

from app.domain.entities.play import Encounter
from app.domain.values import DocumentKind, HpStatus
from tools.prepare.csvio import CsvTable
from tools.prepare.report import Distribution, Measurements, counts_line, number, percent, share, top
from tools.prepare.srd import Srd, name_key

log = logging.getLogger("prepare.fireball")

FILTERED_MEMBER = re.compile(r"(?:^|/)filtered/(?:[^/]+/)*([^/]+?)\.jsonl(?:\.gz)?$")
RAW_MEMBER = re.compile(r"(?:^|/)data/(?:[^/]+/)*([^/]+?)\.jsonl(?:\.gz)?$")
#: Пошкодження одного вкладеного файлу: його пропускають, решта архіву читається далі
BROKEN_FILE_ERRORS = (EOFError, OSError, zlib.error, tarfile.TarError)
#: Тип події видно з початку рядка — сирі події непотрібних типів не розбираються
EVENT_TYPE = re.compile(r'"event_type"\s*:\s*"([a-z_]+)"')
NEEDED_EVENTS = frozenset({"message", "command", "combat_state_update", "combat_start", "combat_end"})

HP = re.compile(r"^\s*<\s*(-?\d+)\s*(?:/\s*(-?\d+)\s*)?(?:HP)?\s*(?:;\s*([A-Za-z]+)\s*)?>")
CLASS_LEVEL = re.compile(r"^(.*?)\s*(\d+)\s*$")
HP_STATUSES = {status.value for status in HpStatus}

CHARACTER_COLUMNS = (
    "character_id", "name", "race", "char_class", "level", "classes_all", "hp_current", "hp_max", "hp_status",
    "spells", "spells_srd", "combat_payload", "encounter_ids", "controller_hash", "derived_fields",
)
PARTICIPATION_COLUMNS = (
    "encounter_id", "instance_label", "monster_id", "monster_name_raw", "hp_current", "hp_max", "hp_status",
    "derived_fields",
)
ENCOUNTER_COLUMNS = (
    "encounter_id", "started_at", "ended_at", "duration_seconds", "dm_hash", "channel_hash", "rounds_count",
    "participants_count", "player_character_ids", "narrative_text", "commands", "messages_count", "derived_fields",
)

#: Класи SRD і поширені класи поза ним — для вимірів п. 2.5.3
REPORTED_CLASSES = ("Artificer", "Blood Hunter", "Bloodhunter")


# --------------------------------------------------------------------------
# Розбір полів актора (таблиця 2.9)
# --------------------------------------------------------------------------


class ParsedHp(NamedTuple):
    current: int | None = None
    maximum: int | None = None
    status: str | None = None
    #: status — зі статусом; numbers — лише числа; empty — порожньо; unparsed — інший формат
    form: str = "empty"


def parse_hp(raw: Any) -> ParsedHp:
    """«<18/18 HP; Healthy>» → 18, 18, Healthy; статус поза переліком HpStatus — null."""
    text = str(raw or "").strip()
    if not text:
        return ParsedHp()
    match = HP.match(text)
    if not match:
        return ParsedHp(form="unparsed")
    current = int(match.group(1))
    maximum = int(match.group(2)) if match.group(2) is not None else None
    status = match.group(3)
    if status is not None and status not in HP_STATUSES:
        status = None
    return ParsedHp(current, maximum, status, "status" if status else "numbers")


def parse_classes(raw: Any) -> list[tuple[str, int | None]]:
    """«Fighter 2/Warlock 3» → [(Fighter, 2), (Warlock, 3)]; клас — усе до останнього числа."""
    result: list[tuple[str, int | None]] = []
    for part in str(raw or "").split("/"):
        part = " ".join(part.split())
        if not part:
            continue
        match = CLASS_LEVEL.match(part)
        if match and match.group(1).strip():
            result.append((match.group(1).strip(), int(match.group(2))))
        else:
            result.append((part, None))
    return result


def main_class(classes: list[tuple[str, int | None]]) -> tuple[str | None, int | None]:
    """Основний клас — з найбільшим рівнем (за рівності — перший); рівень — сума рівнів класів."""
    if not classes:
        return None, None
    name = max(classes, key=lambda pair: pair[1] or 0)[0]
    levels = [level for _, level in classes if level is not None]
    return name, (sum(levels) if levels else None)


def split_list(raw: Any) -> list[str]:
    """Рядок через кому (attacks, spells, actions, effects) → масив без повторів."""
    items: dict[str, None] = {}
    for part in str(raw or "").split(","):
        part = " ".join(part.split())
        if part:
            items.setdefault(part, None)
    return list(items)


def is_player_character(actor: dict) -> bool:
    """Персонаж гравця — учасник із непорожнім class (таблиця 2.8)."""
    return bool(str(actor.get("class") or "").strip())


def started_at(combat_id: str) -> datetime | None:
    """Час початку — перша половина імені файлу до дефіса (Unix-час)."""
    head = combat_id.split("-", 1)[0]
    return datetime.fromtimestamp(int(head), tz=timezone.utc) if head.isdigit() else None


def character_id(name: str, controller: str) -> str:
    return DocumentKind.CHARACTER.natural_id(f"{name}:{controller}")


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# Потокове читання архіву
# --------------------------------------------------------------------------


def iter_members(archive: Path, pattern: re.Pattern, wanted: set[str] | None = None,
                 progress: "Progress | None" = None) -> Iterator[tuple[str, Iterator[str]]]:
    """Файли каталогу архіву, що відповідають шаблону: (ім'я без розширення, рядки).

    Архів читається послідовно (режим r|gz), тож файли іншого каталогу лише
    пропускаються без розпакування вкладеного gzip. Якщо задано ``wanted``,
    решта файлів того самого каталогу теж пропускається.
    """
    with archive.open("rb") as raw, tarfile.open(fileobj=raw, mode="r|gz") as tar:
        for member in tar:
            if progress is not None:
                progress.position = raw.tell()
            if not member.isfile():
                continue
            match = pattern.search(member.name)
            if not match:
                if progress is not None and not (FILTERED_MEMBER.search(member.name) or RAW_MEMBER.search(member.name)):
                    progress.other += 1
                    if len(progress.examples) < 3:
                        progress.examples.append(member.name)
                continue
            stem = match.group(1)
            if progress is not None:
                progress.seen += 1
            if wanted is not None and stem not in wanted:
                continue
            stream = tar.extractfile(member)
            if stream is None:
                continue
            yield stem, _lines(stream, member.name)


def _lines(stream: Any, name: str) -> Iterator[str]:
    binary = gzip.GzipFile(fileobj=stream) if name.endswith(".gz") else stream
    with io.TextIOWrapper(binary, encoding="utf-8", errors="replace") as text:
        for line in text:
            if line.strip():
                yield line


@dataclass(slots=True)
class Progress:
    """Журнал ходу проходу: позиція в архіві, кількість файлів, оцінка часу."""

    label: str
    total_bytes: int
    position: int = 0
    seen: int = 0
    processed: int = 0
    #: файли, що не належать ні до filtered, ні до data, і кілька їхніх назв
    other: int = 0
    examples: list[str] = field(default_factory=list)
    started: float = field(default_factory=time.monotonic)
    last: float = field(default_factory=time.monotonic)

    def tick(self, extra: str = "", force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self.last < 30:
            return
        self.last = now
        elapsed = now - self.started
        share_done = self.position / self.total_bytes if self.total_bytes else 0
        eta = f", лишилося ≈ {int(elapsed * (1 - share_done) / share_done / 60) + 1} хв" if 0.01 < share_done < 1 else ""
        log.info("%s: архів %d %%, файлів каталогу %d, оброблено %d%s%s", self.label, int(share_done * 100),
                 self.seen, self.processed, f", {extra}" if extra else "", eta)


# --------------------------------------------------------------------------
# Підготовка
# --------------------------------------------------------------------------


@dataclass(slots=True)
class _Character:
    actor: dict
    order: tuple[int, int]
    encounters: set[str] = field(default_factory=set)


@dataclass(slots=True)
class FireballResult:
    characters: int = 0
    participations: int = 0
    encounters: int = 0
    play_counts: Counter = field(default_factory=Counter)


class FireballPreparation:
    """Прохід 1 — учасники, прохід 2 — метадані зустрічей; виміри обох."""

    def __init__(self, archive: Path, srd: Srd, out_dir: Path, measurements: Measurements,
                 limit: int | None = None) -> None:
        self._archive = archive
        self._srd = srd
        self._out = out_dir
        self._m = measurements
        self._limit = limit
        self._monsters = srd.monsters_by_name()
        self._monsters_2024 = srd.monsters_2024_by_name()
        self._spells = srd.spells_by_name()
        self._srd_classes = srd.names("Classes")
        self._srd_races = srd.names("Races") | srd.names("Subraces")
        self._characters: dict[tuple[str, str], _Character] = {}
        self._combat_characters: dict[str, set[str]] = {}
        self._result = FireballResult()
        # виміри першого проходу
        self._triples = 0
        self._broken_lines = 0
        self._broken_files = 0
        self._instance_races: Counter = Counter()
        self._instance_status: Counter = Counter()
        self._instance_hp_forms: Counter = Counter()
        self._instance_controller = 0
        self._instances_without_race = 0
        self._groups = 0
        self._matched_2024 = 0
        self._name_conflicts = 0
        self._other_members: tuple[int, list[str]] = (0, [])

    # --- прохід 1: учасники ---

    def run(self) -> FireballResult:
        size = self._archive.stat().st_size
        gigabytes = f"{size / 1e9:.2f}".replace(".", ",")
        self._m.snapshots["FIREBALL"] = f"{self._archive.name}, {number(size)} байтів ({gigabytes} ГБ)"
        # файли попереднього запуску не повинні змішатися з результатом перерваного
        for name in ("characters.csv", "participations.csv", "encounters.csv"):
            (self._out / name).unlink(missing_ok=True)
        self._participants_pass(size)
        self._measure_participants()
        self._encounters_pass(size)
        self._measure_play_counts()
        return self._result

    def _participants_pass(self, size: int) -> None:
        progress = Progress("прохід 1 (filtered)", size)
        with CsvTable(self._out / "participations.csv", PARTICIPATION_COLUMNS) as participations:
            for combat_id, lines in iter_members(self._archive, FILTERED_MEMBER, progress=progress):
                if self._limit is not None and progress.processed >= self._limit:
                    break
                try:
                    self._read_combat(combat_id, lines, participations)
                except BROKEN_FILE_ERRORS as error:
                    self._broken_files += 1
                    log.warning("filtered/%s: файл пошкоджено (%s) — пропущено", combat_id, error)
                    continue
                progress.processed += 1
                progress.tick(f"трійок {self._triples}, персонажів {len(self._characters)}")
            progress.tick(f"трійок {self._triples}", force=True)
            if not progress.seen:
                examples = ", ".join(progress.examples) or "архів порожній"
                raise ValueError(f"в архіві {self._archive.name} немає файлів filtered/*.jsonl.gz ({examples}) — "
                                 "це не архів FIREBALL або його структура інша")
        self._other_members = (progress.other, progress.examples)
        self._result.participations = participations.rows
        self._write_characters()

    def _read_combat(self, combat_id: str, lines: Iterator[str], participations: CsvTable) -> None:
        start = started_at(combat_id)
        start_key = int(start.timestamp()) if start else 0
        players: dict[tuple[str, str], tuple[dict, int]] = {}
        instances: dict[str, dict] = {}
        for position, line in enumerate(lines):
            try:
                triple = json.loads(line)
            except json.JSONDecodeError:
                self._broken_lines += 1
                continue
            self._triples += 1
            for state in ("combat_state_before", "combat_state_after"):
                for actor in triple.get(state) or []:
                    name = " ".join(str(actor.get("name") or "").split())
                    if not name:
                        continue
                    if is_player_character(actor):
                        players[(name, str(actor.get("controller_id") or ""))] = (actor, position)
                    else:
                        instances[name] = actor
        # учасник, що хоч раз мав клас, — персонаж гравця, а не екземпляр монстра
        player_names = {name for name, _ in players}
        for name in [n for n in instances if n in player_names]:
            del instances[name]
            self._name_conflicts += 1

        ids: set[str] = set()
        for (name, controller), (actor, position) in players.items():
            entry = self._characters.get((name, controller))
            order = (start_key, position)
            if entry is None:
                entry = self._characters[(name, controller)] = _Character(actor, order)
            elif order >= entry.order:
                entry.actor, entry.order = actor, order
            entry.encounters.add(combat_id)
            ids.add(character_id(name, controller))
        self._combat_characters[combat_id] = ids

        for name, actor in instances.items():
            participations.write(self._participation(combat_id, name, actor))

    def _participation(self, combat_id: str, name: str, actor: dict) -> dict[str, Any]:
        race = " ".join(str(actor.get("race") or "").split()) or None
        monster = self._monsters.get(name_key(race)) if race else None
        hp = parse_hp(actor.get("hp"))
        self._instance_races[race or "—"] += 1
        self._instance_status[hp.status or "—"] += 1
        self._instance_hp_forms[hp.form] += 1
        if str(actor.get("controller_id") or "").strip():
            self._instance_controller += 1
        if race is None:
            self._instances_without_race += 1
        elif race == "Group":
            self._groups += 1
        if race and name_key(race) in self._monsters_2024:
            self._matched_2024 += 1
        if monster is not None:
            self._result.play_counts[monster["index"]] += 1
        return {
            "encounter_id": combat_id,
            "instance_label": name,
            "monster_id": DocumentKind.MONSTER.natural_id(monster["index"]) if monster else None,
            "monster_name_raw": race or name,
            "hp_current": hp.current,
            "hp_max": hp.maximum,
            "hp_status": hp.status,
            "derived_fields": ["monster_id"] if monster else [],
        }

    def _write_characters(self) -> None:
        with CsvTable(self._out / "characters.csv", CHARACTER_COLUMNS) as table:
            for (name, controller), entry in sorted(self._characters.items()):
                table.write(self._character(name, controller, entry))
        self._result.characters = table.rows
        log.info("characters.csv: %d рядків, participations.csv: %d рядків", table.rows, self._result.participations)

    def _character(self, name: str, controller: str, entry: _Character) -> dict[str, Any]:
        actor = entry.actor
        classes = parse_classes(actor.get("class"))
        char_class, level = main_class(classes)
        hp = parse_hp(actor.get("hp"))
        spells = split_list(actor.get("spells"))
        spells_srd = list(dict.fromkeys(self._spells[name_key(s)] for s in spells if name_key(s) in self._spells))
        return {
            "character_id": character_id(name, controller),
            "name": name,
            "race": " ".join(str(actor.get("race") or "").split()) or None,
            "char_class": char_class,
            "level": level,
            "classes_all": [{"char_class": c, "level": lv} for c, lv in classes],
            "hp_current": hp.current,
            "hp_max": hp.maximum,
            "hp_status": hp.status,
            "spells": spells,
            "spells_srd": spells_srd,
            "combat_payload": {
                "attacks": split_list(actor.get("attacks")),
                "actions": split_list(actor.get("actions")),
                "effects": split_list(actor.get("effects")),
            },
            "encounter_ids": sorted(entry.encounters),
            "controller_hash": controller or None,
            "derived_fields": ["encounter_ids", "spells_srd"],
        }

    # --- прохід 2: метадані зустрічей ---

    def _encounters_pass(self, size: int) -> None:
        wanted = set(self._combat_characters)
        progress = Progress("прохід 2 (data)", size)
        stats = _RawStats()
        with CsvTable(self._out / "encounters.csv", ENCOUNTER_COLUMNS) as table:
            for combat_id, lines in iter_members(self._archive, RAW_MEMBER, wanted, progress):
                try:
                    row = self._encounter(combat_id, lines, stats)
                except BROKEN_FILE_ERRORS as error:
                    self._broken_files += 1
                    log.warning("data/%s: файл пошкоджено (%s) — лише метадані з імені файлу", combat_id, error)
                    continue
                table.write(row)
                wanted.discard(combat_id)
                progress.processed += 1
                progress.tick(f"зустрічей {progress.processed} з {len(self._combat_characters)}")
                if self._limit is not None and not wanted:
                    break
            progress.tick(force=True)
            stats.raw_files = progress.seen
            # бої з трійками, але без сирих подій: лише ім'я файлу й персонажі
            for combat_id in sorted(wanted):
                table.write(self._bare_encounter(combat_id))
                stats.without_raw += 1
        self._result.encounters = table.rows
        log.info("encounters.csv: %d рядків (без сирих подій — %d)", table.rows, stats.without_raw)
        self._measure_encounters(stats)

    def _bare_encounter(self, combat_id: str) -> dict[str, Any]:
        return {"encounter_id": combat_id, "started_at": started_at(combat_id),
                "player_character_ids": sorted(self._combat_characters.get(combat_id, ())), "derived_fields": []}

    def _encounter(self, combat_id: str, lines: Iterator[str], stats: "_RawStats") -> dict[str, Any]:
        messages: list[tuple[str, bool, str]] = []
        commands: list[str] = []
        dms: list[str] = []
        channel = None
        rounds = combatants = 0
        start_ts = end_ts = None
        inner_id = None
        for line in lines:
            kind = EVENT_TYPE.search(line, 0, 300) or EVENT_TYPE.search(line)
            if kind is None or kind.group(1) not in NEEDED_EVENTS:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                self._broken_lines += 1
                continue
            if inner_id is None:
                inner_id = event.get("combat_id")
            event_type = event.get("event_type")
            if event_type == "message":
                messages.append((str(event.get("author_id")), bool(event.get("author_bot")), event.get("content") or ""))
            elif event_type == "command":
                if event.get("command_name"):
                    commands.append(str(event["command_name"]))
            elif event_type == "combat_state_update":
                data = event.get("data") or {}
                if not isinstance(data, dict):
                    continue
                if data.get("dm") is not None:
                    dm = str(data["dm"])
                    if not dms or dms[-1] != dm:
                        dms.append(dm)
                if channel is None and data.get("channel") is not None:
                    channel = str(data["channel"])
                rounds = max(rounds, _int(data.get("round")))
                members = data.get("combatants")
                combatants = max(combatants, len(members) if isinstance(members, (list, dict)) else 0)
            elif event_type == "combat_start":
                start_ts = start_ts if start_ts is not None else _float(event.get("timestamp"))
            elif event_type == "combat_end":
                end_ts = _float(event.get("timestamp")) or end_ts

        dm = dms[0] if dms else None
        dm_messages = [(bot, content) for author, bot, content in messages if dm is not None and author == dm]
        narrative = [content for bot, content in dm_messages if not bot and content.strip()]
        duration = None
        derived = ["messages_count"]
        if start_ts is not None and end_ts is not None:
            duration = int(round(end_ts - start_ts))
            if duration < 0:
                stats.negative_durations += 1
                duration = None
            else:
                derived.insert(0, "duration_seconds")

        stats.with_raw += 1
        stats.inner_id_match += inner_id == combat_id
        stats.with_start += start_ts is not None
        stats.with_end += end_ts is not None
        stats.with_dm += dm is not None
        stats.with_rounds += rounds > 0
        stats.with_combatants += combatants > 0
        stats.several_dms += len(set(dms)) > 1
        stats.dm_messages += len(dm_messages)
        stats.dm_bot_messages += sum(1 for bot, _ in dm_messages if bot)
        stats.dm_replies += len(narrative)
        stats.dm_chars += sum(len(text) for text in narrative)
        stats.commands.update(commands)
        stats.messages.add(len(messages))
        stats.replies.add(len(narrative))
        stats.narrative_chars.add(len("\n".join(narrative)) if narrative else None)
        stats.rounds.add(rounds if rounds > 0 else None)
        stats.participants.add(combatants if combatants > 0 else None)
        if duration is not None:
            stats.durations.add(duration / 60)
            stats.abandoned += duration > Encounter.ABANDONED_THRESHOLD_SECONDS

        begin = started_at(combat_id)
        if begin is None and start_ts is not None:
            begin = datetime.fromtimestamp(start_ts, tz=timezone.utc).replace(microsecond=0)
        return {
            "encounter_id": combat_id,
            "started_at": begin,
            "ended_at": datetime.fromtimestamp(end_ts, tz=timezone.utc).replace(microsecond=0)
            if end_ts is not None else None,
            "duration_seconds": duration,
            "dm_hash": dm,
            "channel_hash": channel,
            "rounds_count": rounds or None,
            "participants_count": combatants or None,
            "player_character_ids": sorted(self._combat_characters.get(combat_id, ())),
            "narrative_text": "\n".join(narrative) or None,
            "commands": commands,
            "messages_count": len(messages),
            "derived_fields": derived,
        }

    # --- виміри ---

    def _measure_participants(self) -> None:
        key = "fireball"
        m = self._m
        m.section(key, "Крок 2. FIREBALL: учасники (підрозділи 2.5.1–2.5.4, таблиці 2.8, 2.9, А.9, А.11)")
        players = list(self._characters.values())
        n_players = len(players)
        n_instances = self._result.participations
        n_all = n_players + n_instances
        combats = len(self._combat_characters)
        m.add(key, "п. 2.5.2", "трійок (повний прохід filtered)", number(self._triples), "153 829")
        m.add(key, "п. 2.5.2", "боїв у каталозі filtered", number(combats), "14 700")
        m.add(key, "п. 2.5.2, табл. А.13", "унікальних учасників", number(n_all), "48 895")
        m.add(key, "табл. 2.3, А.1, А.9", "персонажів гравців (name + controller_id)", number(n_players), "6 092")
        m.add(key, "табл. 2.3, А.1, А.11", "екземплярів монстрів (encounter_id + instance_label)", number(n_instances),
              "42 803")
        controllers = sum(1 for (_, controller) in self._characters if controller) + self._instance_controller
        m.add(key, "табл. 2.8", "controller_id заповнений", share(controllers, n_all), "100%")
        m.add(key, "табл. 2.8, п. 2.5.2", "порожній class (екземпляри монстрів)", share(n_instances, n_all),
              "87,5% (42 803 із 48 895)")
        m.add(key, "п. 2.5.2, табл. А.9", "заповнений class (персонажі гравців)", share(n_players, n_all), "12,5%")
        matched = sum(self._result.play_counts.values())
        false_hits = sum(1 for e in players if name_key(e.actor.get("race")) in self._monsters)
        m.add(key, "табл. 2.8", "race = назва монстра SRD, class порожній (зіставлено)", number(matched), "21 293")
        m.add(key, "табл. 2.8", "race = назва монстра SRD, class заповнений (хибні спрацювання)",
              f"{number(false_hits)} ({percent(false_hits, false_hits + matched)} від усіх збігів race)", "360 (1,7%)")
        m.add(key, "табл. А.11, п. 2.5.2", "записів участі, зіставлених зі SRD", share(matched, n_instances),
              "49,7% (21 293 з 42 803)")
        if self._monsters_2024:
            m.add(key, "п. 2.4", "та сама перевірка за назвами монстрів src/2024", share(self._matched_2024, n_instances))
        races = Counter(self._instance_races)
        for entry in players:
            races[" ".join(str(entry.actor.get("race") or "").split()) or "—"] += 1
        m.add(key, "п. 2.5.2", "поле race, перші 15 значень", top(races, 15),
              "Skeleton (997), Zombie (843), Bandit (819), Goblin (817), Wolf (781), Commoner (594); "
              "Variant Human (759), Half-Elf (322)")
        m.add(key, "табл. А.11", "екземпляри без race (власні учасники ведучого) / групи",
              f"{number(self._instances_without_race)} / {number(self._groups)}")
        m.add(key, "п. 2.5.2", "імен, що в одному бою мали й не мали class", number(self._name_conflicts))

        # придатність словників SRD (п. 2.5.3)
        player_races = Counter(" ".join(str(e.actor.get("race") or "").split()) for e in players)
        outside = Counter({race: n for race, n in player_races.items() if race and name_key(race) not in self._srd_races})
        m.add(key, "п. 2.5.3", "раси персонажів поза Races + Subraces SRD (перші 10)", top(outside, 10),
              "Variant Human, Astral Elf, Shifter, Dhampir, Custom Lineage")
        m.add(key, "п. 2.5.3", "персонажів із расою поза SRD", share(sum(outside.values()), n_players))
        class_counts: Counter = Counter()
        for entry in players:
            class_counts.update({name for name, _ in parse_classes(entry.actor.get("class"))})
        outside_classes = Counter({c: n for c, n in class_counts.items() if name_key(c) not in self._srd_classes})
        m.add(key, "п. 2.5.3", "класи поза 12 класами SRD (персонажів, перші 10)", top(outside_classes, 10),
              "Artificer (351), Blood Hunter (102), Bloodhunter (22)")
        m.add(key, "п. 2.5.3", "Blood Hunter / Bloodhunter",
              f"{number(class_counts.get('Blood Hunter', 0))} / {number(class_counts.get('Bloodhunter', 0))}",
              "102 / 22")

        # закляття
        uses: Counter = Counter()
        for entry in players:
            uses.update(name_key(s) for s in split_list(entry.actor.get("spells")))
        matched_names = [s for s in uses if s in self._spells]
        matched_uses = sum(uses[s] for s in matched_names)
        m.add(key, "п. 2.5.3", "унікальних назв заклять / зіставлено зі SRD",
              f"{number(len(uses))} / {share(len(matched_names), len(uses))}", "1 315 / 313 (23,8%)")
        m.add(key, "п. 2.5.3, табл. А.9", "частка зіставлених за обсягом використання",
              share(matched_uses, sum(uses.values())), "82,8%")

        # перетворення й покриття (п. 2.5.4, таблиця А.9)
        statuses = Counter(self._instance_status)
        hp_forms = Counter(self._instance_hp_forms)
        multiclass = 0
        coverage = Counter()
        for entry in players:
            actor = entry.actor
            hp = parse_hp(actor.get("hp"))
            statuses[hp.status or "—"] += 1
            hp_forms[hp.form] += 1
            classes = parse_classes(actor.get("class"))
            multiclass += len(classes) > 1
            coverage["race"] += bool(str(actor.get("race") or "").strip())
            coverage["level"] += main_class(classes)[1] is not None
            coverage["hp"] += hp.current is not None
            coverage["hp_status"] += hp.status is not None
            for name in ("spells", "attacks", "actions", "effects", "description"):
                coverage[name] += bool(split_list(actor.get(name)) if name != "description"
                                       else str(actor.get(name) or "").strip())
        ordered = [(s.value, statuses.get(s.value, 0)) for s in HpStatus]
        m.add(key, "п. 2.5.4", "статуси здоров'я (усі учасники, останній стан у бою)", counts_line(ordered),
              "Healthy (44 035), Injured (2 184), Bloodied (676), Critical (269), Dead (54)")
        m.add(key, "п. 2.5.4", "учасників без статусу: без максимуму HP / порожнє hp / інший формат",
              f"{number(hp_forms.get('numbers', 0))} / {number(hp_forms.get('empty', 0))} / "
              f"{number(hp_forms.get('unparsed', 0))} — разом {number(statuses.get('—', 0))}",
              "TODO: 47 218 ≠ 48 895")
        m.add(key, "п. 2.5.4, табл. А.9", "мультиклас (кілька пар «клас — рівень»)", share(multiclass, n_players),
              "32,8%")
        reported = {"race": "96,3%", "level": "99,8%", "hp": "97,4%", "hp_status": "97,4%", "spells": "22,5%",
                    "attacks": "96,6%", "actions": "11,5%", "effects": "7,5%", "description": "4,3% (табл. 2.17)"}
        for name, value in reported.items():
            m.add(key, "табл. А.9", f"персонаж: {name}", share(coverage[name], n_players), value)
        instance_hp = sum(n for form, n in self._instance_hp_forms.items() if form in ("status", "numbers"))
        m.add(key, "табл. А.11", "участь: hp_current, hp_max", share(instance_hp, n_instances), "97,4%")
        m.add(key, "табл. А.11", "участь: hp_status", share(self._instance_hp_forms.get("status", 0), n_instances),
              "97,4%")
        if self._broken_files or self._broken_lines:
            m.note(key, f"Пошкоджених файлів: {self._broken_files}, рядків, що не розібрано як JSON: "
                   f"{self._broken_lines} — пропущено.")
        other, examples = self._other_members
        if other:
            m.note(key, f"Файлів архіву поза filtered і data: {number(other)} "
                   f"(наприклад, {', '.join(examples)}) — не використовуються.")
        m.note(key, "Стан учасника — останній зафіксований у бою (combat_state_after останньої трійки); "
               "для персонажа — з найпізнішого бою. Статуси підраховано за цим станом.")

    def _measure_play_counts(self) -> None:
        key = "play_count"
        m = self._m
        m.section(key, "Крок 3. Популярність монстрів (таблиця 2.14)")
        counts = self._result.play_counts
        names = {record["index"]: record["name"] for record in self._srd.monsters}
        m.add(key, "п. 2.5.2, табл. А.11", "найчастіші монстри (play_count)",
              top(Counter({names[i]: n for i, n in counts.items()}), 10), "Skeleton (997), Zombie (843), Bandit (819)")
        m.add(key, "табл. А.7", "монстрів SRD із play_count > 0", share(len(counts), len(self._srd.monsters)))
        single = sum(1 for n in counts.values() if n <= 5)
        m.add(key, "п. 2.5.2", "монстрів, що трапилися не більше 5 разів", share(single, len(counts) or 1))

    def _measure_encounters(self, s: "_RawStats") -> None:
        key = "encounters"
        m = self._m
        m.section(key, "Крок 2. FIREBALL: зустрічі (підрозділи 2.5.5–2.5.7, таблиці 2.10, 2.11, А.10)")
        total = len(self._combat_characters)
        m.add(key, "п. 2.5.2", "боїв у каталозі data (сирі події)", number(s.raw_files), "≈ 25 тис. (за авторами)")
        m.add(key, "табл. 2.3, А.1", "рядків encounters.csv", number(self._result.encounters), "14 700")
        m.add(key, "п. 2.5.5", "боїв filtered із сирими подіями", share(s.with_raw, total))
        m.add(key, "табл. 2.10", "combat_start", share(s.with_start, total), "99,7%")
        m.add(key, "табл. 2.10, А.10", "ідентифікатор ведучого (data.dm)", share(s.with_dm, total), "99,7%")
        m.add(key, "табл. 2.10, п. 2.5.1", "ім'я файлу = combat_id сирих подій", share(s.inner_id_match, total), "99,7%")
        m.add(key, "табл. 2.10, А.10", "combat_end (ended_at, duration_seconds)", share(s.with_end, total), "94,7%")
        m.add(key, "табл. 2.10, А.10", "ненульова кількість раундів", share(s.with_rounds, total), "93,0%")
        m.add(key, "табл. А.10", "participants_count > 0", share(s.with_combatants, total), "93,0%")
        m.add(key, "табл. А.10", "narrative_text", share(s.narrative_chars.count, total), "99,7%")
        m.add(key, "п. 2.5.5", "повідомлень з автором data.dm / з ознакою бота",
              f"{number(s.dm_messages)} / {share(s.dm_bot_messages, s.dm_messages)}", "45 893 / 22 774 (49,6%)")
        m.add(key, "п. 2.5.5", "реплік ведучого після фільтра / символів",
              f"{number(s.dm_replies)} / {number(s.dm_chars)}", "23 119 / 657 492 (вибірка 301 бою)")
        m.add(key, "п. 2.5.5", "символів на репліку", number(s.dm_chars / s.dm_replies if s.dm_replies else None),
              "28")
        m.add(key, "п. 2.5.5", "символів narrative_text на бій",
              s.narrative_chars.describe(0), "≈ 2 200; екстраполяція ≈ 32 млн символів")
        m.add(key, "п. 2.5.5", "символів narrative_text разом", number(sum(s.narrative_chars.values)))
        m.add(key, "п. 2.5.5", "боїв, де data.dm змінювався", number(s.several_dms))
        for name, dist, reported, digits in (
            ("раундів у бою", s.rounds, "3 / 20,5 / 2 554", 1),
            ("тривалість, хв", s.durations, "12,7 / 1 264 / 50 031", 1),
            ("повідомлень", s.messages, "115,5 / 327,2 / 8 120", 1),
            ("реплік ведучого", s.replies, "36,5 / 77,1 / 1 770", 1),
            ("учасників", s.participants, "3 / 4,9 / 65", 1),
        ):
            m.add(key, "табл. 2.11", name, dist.describe(digits), f"медіана / середнє / максимум: {reported}")
        longest = s.durations.maximum()
        m.add(key, "п. 2.5.6", "найдовший бій",
              f"{number(longest, 0)} хв ({number(longest / 1440, 1)} доби)" if longest else "—", "50 031 хв (> 34 доби)")
        m.add(key, "п. 2.5.6, табл. 2.14", "is_abandoned (понад 8 годин)", share(s.abandoned, s.durations.count or 1))
        m.add(key, "п. 2.5.7", "команди (кількість викликів, перші 10)", top(s.commands, 10),
              "init next (4 890), embed (3 206), action (3 016), init attack (1 984), cast (1 233), roll (956), "
              "check (919), character (906)")
        sizes = sorted(len(ids) for ids in self._combat_characters.values() if ids)
        party = max(1, sizes[len(sizes) // 2]) if sizes else None
        m.add(key, "табл. 2.13, п. 2.7.3, А.2", "персонажів гравців у бою (медіана; калібрування players_min/max)",
              f"{number(party)} (боїв із персонажами {share(len(sizes), total)})", "TODO: порахувати")
        if s.negative_durations:
            m.note(key, f"Боїв, де combat_end раніше за combat_start: {s.negative_durations} — тривалість не записано.")
        m.note(key, "Знаменник часток — бої каталогу filtered (рядки encounters.csv); розподіли таблиці 2.11 — "
               "за зустрічами із заповненим полем. Повідомлення — події message усього каналу, репліки ведучого — "
               "повідомлення автора data.dm без ознаки бота.")


@dataclass(slots=True)
class _RawStats:
    raw_files: int = 0
    with_raw: int = 0
    without_raw: int = 0
    inner_id_match: int = 0
    with_start: int = 0
    with_end: int = 0
    with_dm: int = 0
    with_rounds: int = 0
    with_combatants: int = 0
    several_dms: int = 0
    negative_durations: int = 0
    abandoned: int = 0
    dm_messages: int = 0
    dm_bot_messages: int = 0
    dm_replies: int = 0
    dm_chars: int = 0
    commands: Counter = field(default_factory=Counter)
    messages: Distribution = field(default_factory=Distribution)
    replies: Distribution = field(default_factory=Distribution)
    narrative_chars: Distribution = field(default_factory=Distribution)
    rounds: Distribution = field(default_factory=Distribution)
    participants: Distribution = field(default_factory=Distribution)
    durations: Distribution = field(default_factory=Distribution)

