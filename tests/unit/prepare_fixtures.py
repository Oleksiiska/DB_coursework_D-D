"""Мініатюрні датасети для тестів офлайн-підготовки (tools/prepare).

Формати відтворюють справжні: запис 5e-SRD-Monsters.json, стан актора
FIREBALL (нормалізація distill4_normalize.py: ``<cur/max HP; Status>``,
``<cur HP>``, `` (+N temp)``), сирі події raw.jsonl і структура архіву
``anonymized/filtered`` + ``anonymized/data``.
"""

from __future__ import annotations

import gzip
import io
import json
import tarfile
from pathlib import Path

from tools.prepare.srd import Srd

SRD_CLASSES = ["Barbarian", "Bard", "Cleric", "Druid", "Fighter", "Monk", "Paladin", "Ranger", "Rogue",
               "Sorcerer", "Warlock", "Wizard"]
SRD_RACES = ["Dragonborn", "Dwarf", "Elf", "Gnome", "Half-Elf", "Half-Orc", "Halfling", "Human", "Tiefling"]
SRD_SUBRACES = ["Hill Dwarf", "High Elf", "Lightfoot Halfling", "Rock Gnome"]
ALIGNMENTS = ["Chaotic Evil", "Chaotic Good", "Chaotic Neutral", "Lawful Evil", "Lawful Good", "Lawful Neutral",
              "Neutral", "Neutral Evil", "Neutral Good"]


def monster_record(index: str, name: str, type_: str, cr: float, **extra) -> dict:
    record = {
        "index": index, "name": name, "size": "Medium", "type": type_, "alignment": "neutral evil",
        "armor_class": [{"type": "natural", "value": 13}], "hit_points": 13, "hit_dice": "2d8",
        "hit_points_roll": "2d8+4", "speed": {"walk": "30 ft."},
        "strength": 10, "dexterity": 14, "constitution": 15, "intelligence": 6, "wisdom": 8, "charisma": 5,
        "proficiencies": [], "damage_vulnerabilities": [], "damage_resistances": [], "damage_immunities": [],
        "condition_immunities": [], "senses": {"darkvision": "60 ft.", "passive_perception": 9},
        "languages": "", "challenge_rating": cr, "proficiency_bonus": 2, "xp": 50,
        "image": f"/api/images/monsters/{index}.png", "url": f"/api/2014/monsters/{index}",
        "actions": [{"name": "Shortsword", "desc": "Melee Weapon Attack: +4 to hit, reach 5 ft."}],
    }
    record.update(extra)
    return record


def srd_subset() -> Srd:
    monsters = [
        monster_record("skeleton", "Skeleton", "undead", 0.25, damage_vulnerabilities=["bludgeoning"],
                       damage_immunities=["poison"], condition_immunities=[
                           {"index": "poisoned", "name": "Poisoned", "url": "/api/2014/conditions/poisoned"}],
                       languages="understands all languages it knew in life but can't speak", alignment="lawful evil"),
        monster_record("zombie", "Zombie", "undead", 0.25, special_abilities=[
            {"name": "Undead Fortitude", "desc": "If damage reduces the zombie to 0 hit points..."}]),
        monster_record("goblin", "Goblin", "humanoid", 0.25, subtype="goblinoid", languages="Common, Goblin",
                       proficiencies=[{"value": 6, "proficiency": {"index": "skill-stealth", "name": "Skill: Stealth"}}]),
        monster_record("wolf", "Wolf", "beast", 0.25, alignment="unaligned", speed={"walk": "40 ft."}),
        monster_record("aboleth", "Aboleth", "aberration", 10, size="Large", alignment="lawful evil",
                       speed={"walk": "10 ft.", "swim": "40 ft."}, desc="Aboleths lurk in subterranean lakes.",
                       languages="Deep Speech, telepathy 120 ft.",
                       proficiencies=[
                           {"value": 6, "proficiency": {"index": "saving-throw-con", "name": "Saving Throw: CON"}},
                           {"value": 12, "proficiency": {"index": "skill-history", "name": "Skill: History"}}],
                       legendary_actions=[{"name": "Detect", "desc": "The aboleth makes a Wisdom check."}]),
        monster_record("giant-eagle", "Giant Eagle", "beast", 1, size="Large", alignment="neutral good",
                       speed={"walk": "10 ft.", "fly": "80 ft."},
                       languages="Giant Eagle, understands Common and Auran but can't speak them"),
    ]
    spells = [{"index": "shield", "name": "Shield"}, {"index": "eldritch-blast", "name": "Eldritch Blast"},
              {"index": "cure-wounds", "name": "Cure Wounds"}, {"index": "sleep", "name": "Sleep"}]
    named = lambda names: [{"index": n.lower().replace(" ", "-"), "name": n} for n in names]  # noqa: E731
    return Srd(
        commit="test", monsters=monsters, spells=spells, magic_items=[{"index": "bag-of-holding"}],
        dictionaries={"Alignments": named(ALIGNMENTS), "Races": named(SRD_RACES), "Subraces": named(SRD_SUBRACES),
                      "Classes": named(SRD_CLASSES), "Languages": named(["Common", "Goblin"]),
                      "Damage-Types": named(["Poison"]), "Conditions": named(["Poisoned"]),
                      "Skills": named(["Stealth"])},
        monsters_2024=[monster_record("skeleton", "Skeleton", "undead", 0.25),
                       monster_record("goblin-warrior", "Goblin Warrior", "fey", 0.25)],
    )


# --------------------------------------------------------------------------
# FIREBALL
# --------------------------------------------------------------------------


def actor(name: str, hp: str, cls: str | None = None, race: str | None = None, controller: str = "500",
          spells: str = "", attacks: str = "", actions: str | None = None, effects: str = "",
          description: str | None = None) -> dict:
    return {"name": name, "hp": hp, "class": cls, "race": race, "attacks": attacks, "spells": spells,
            "actions": actions, "effects": effects, "description": description, "controller_id": controller}


def triple(before: list[dict], after: list[dict]) -> dict:
    return {"speaker_id": "1", "before_utterances": [], "combat_state_before": before, "current_actor": None,
            "commands_norm": ["!init next"], "automation_results": [], "caster_after": None, "targets_after": [],
            "combat_state_after": after, "after_utterances": [], "utterance_history": []}


def event(combat_id: str, kind: str, ts: float, **body) -> dict:
    return {"combat_id": combat_id, "event_type": kind, "timestamp": ts, **body}


def message(combat_id: str, ts: float, author: str, content: str, bot: bool = False) -> dict:
    return event(combat_id, "message", ts, message_id=int(ts * 1000), author_id=author,
                 author_name="Avrae" if bot else f"Player {author}", author_bot=bot, content=content)


def state(combat_id: str, ts: float, dm: int, round_: int, combatants: int, channel: str = "42") -> dict:
    return event(combat_id, "combat_state_update", ts, data={
        "channel": channel, "summary": 1, "dm": dm, "options": {}, "round": round_, "turn": 0,
        "combatants": [{"name": f"c{i}", "effects": [{"name": "x", "end_round": 99}]} for i in range(combatants)],
        "metadata": {}})


VERITY_A = actor("Verity", "<18/18 HP; Healthy>", "Rogue 3", "Lightfoot Halfling", "111",
                 spells="Sleep, Mage Armor", attacks="Dagger, Shortbow", actions="Sneak Attack", effects="",
                 description="Halfling, female")
VERITY_A_AFTER = {**VERITY_A, "hp": "<9/18 HP; Bloodied>"}
VERITY_B = actor("Verity", "<24/24 HP; Healthy>", "Rogue 4", "Lightfoot Halfling", "111",
                 spells="Sleep, Mage Armor, Shield", attacks="Dagger, Shortbow, Rapier")
KEYA = actor("Keya", "<24/24 HP; Healthy>", "Fighter 2/Warlock 3", "Custom Lineage", "222",
             spells="Hex, Eldritch Blast, Shield, Eldritch Blast", attacks="Longsword", effects="Hex")
GROM = actor("Grom", "<40/40 HP; Healthy>", "Blood Hunter 5", "Goblin", "333", attacks="Greatsword")
LONELY = actor("Lonely", "<10/12 HP; Injured>", "Bloodhunter 2", None, "444")
SK1 = actor("SK1", "<13/13 HP; Healthy>", race="Skeleton", controller="100", attacks="Shortsword")
SK1_DEAD = {**SK1, "hp": "<0/13 HP; Dead>"}
BOSS = actor("Strahd", "<144/144 HP; Healthy>", race="Strahd von Zarovich", controller="100")
CUSTOM = actor("Guard", "", race=None, controller="100")
GO1 = actor("GO1", "<7 HP>", race="Goblin", controller="200")
WO1 = actor("WO1", "<11/11 HP; Healthy> (+5 temp)", race="wolf", controller="200")
ZO1 = actor("ZO1", "<22/22 HP; Healthy>", race="Zombie", controller="300")

COMBAT_A = "1650000000-aaaaaaaa-0000-0000-0000-000000000001"
COMBAT_B = "1650100000-bbbbbbbb-0000-0000-0000-000000000002"
COMBAT_C = "1650200000-cccccccc-0000-0000-0000-000000000003"
RAW_ONLY = "1650300000-dddddddd-0000-0000-0000-000000000004"


def filtered_files() -> dict[str, list[dict]]:
    return {
        COMBAT_A: [
            triple([VERITY_A, KEYA, SK1], [VERITY_A, KEYA, SK1, BOSS]),
            triple([VERITY_A, KEYA, SK1, BOSS, CUSTOM], [VERITY_A_AFTER, KEYA, SK1_DEAD, BOSS, CUSTOM]),
        ],
        COMBAT_B: [triple([VERITY_B, GROM, GO1], [VERITY_B, GROM, GO1, WO1])],
        COMBAT_C: [triple([LONELY, ZO1], [LONELY, ZO1])],
    }


def raw_files() -> dict[str, list[dict]]:
    a, b = 1650000000.3, 1650100000.5
    return {
        COMBAT_A: [
            message(COMBAT_A, a, "100", "The crypt door groans open."),
            event(COMBAT_A, "combat_start", a + 1),
            state(COMBAT_A, a + 2, 100, 0, 0),
            event(COMBAT_A, "command", a + 3, command_name="init attack", author_id="111"),
            message(COMBAT_A, a + 4, "100", "Embed from the bot under the DM id", bot=True),
            message(COMBAT_A, a + 5, "999", "Verity hits!", bot=True),
            event(COMBAT_A, "automation_run", a + 6, automation=[{"type": "target"}], caster={"name": "Verity"}),
            state(COMBAT_A, a + 7, 100, 1, 5),
            event(COMBAT_A, "command", a + 8, command_name="cast", author_id="222"),
            event(COMBAT_A, "command", a + 9, command_name="init attack", author_id="111"),
            message(COMBAT_A, a + 10, "111", "I stab it."),
            message(COMBAT_A, a + 11, "100", "The skeleton crumbles to dust."),
            event(COMBAT_A, "command", a + 12, command_name="check", author_id="111"),
            state(COMBAT_A, a + 13, 100, 2, 4),
            event(COMBAT_A, "combat_end", a + 1 + 1800),
        ],
        COMBAT_B: [
            event(COMBAT_B, "combat_start", b),
            state(COMBAT_B, b + 1, 200, 1, 3, channel="43"),
            message(COMBAT_B, b + 2, "200", "Wolves howl in the dark."),
            event(COMBAT_B, "command", b + 3, command_name="init next", author_id="200"),
            event(COMBAT_B, "combat_end", b + 9 * 3600),
        ],
        RAW_ONLY: [event(RAW_ONLY, "combat_start", 1650300000.0)],
    }


def _gz_lines(records: list[dict]) -> bytes:
    data = "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records).encode("utf-8")
    return gzip.compress(data)


def build_archive(path: Path, data_first: bool = True) -> Path:
    """tar.gz зі структурою anonymized/{filtered,data}/{combat_id}.jsonl.gz."""
    groups = [("data", raw_files()), ("filtered", filtered_files())]
    if not data_first:
        groups.reverse()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as tar:
        for folder, files in groups:
            info = tarfile.TarInfo(f"anonymized/{folder}")
            info.type = tarfile.DIRTYPE
            tar.addfile(info)
            for combat_id, records in files.items():
                payload = _gz_lines(records)
                info = tarfile.TarInfo(f"anonymized/{folder}/{combat_id}.jsonl.gz")
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))
    return path


# --------------------------------------------------------------------------
# Знімок обходу itch.io
# --------------------------------------------------------------------------

ROBOTS = "User-agent: *\nDisallow: /embed/\nDisallow: /search\nDisallow: /game/download/\n"

LIST_PAGE = """<html><body>
<div class="filter_group"><span>Theme</span><a href="/physical-games/tag-horror">Horror</a></div>
<div class="filter_group"><span>Format</span><a href="/physical-games/tag-one-shot">One-shot</a></div>
<div class="game_cell" data-game_id="945604"><a class="title game_link" href="https://mira.itch.io/the-drowned-chapel">A</a></div>
<div class="game_cell" data-game_id="777001"><a class="title game_link" href="https://duo.itch.io/goblin-market">B</a></div>
</body></html>"""


def card_page(path: str, title: str, authors: list[tuple[str, str]], tags: list[str], price: str | None,
              session: str | None, comments: int) -> str:
    author_links = ", ".join(f'<a href="{url}">{name}</a>' for name, url in authors)
    tag_links = ", ".join(f'<a href="https://itch.io/physical-games/tag-{t}">{t}</a>' for t in tags)
    rows = ["<tr><td>Status</td><td>Released</td></tr>",
            f"<tr><td>Authors</td><td>{author_links}</td></tr>",
            f"<tr><td>Tags</td><td>{tag_links}</td></tr>",
            "<tr><td>Rating</td><td>Rated 4.5 out of 5 stars (12 total ratings)</td></tr>",
            "<tr><td>Content</td><td>Violence</td></tr>"]
    if session:
        rows.append(f"<tr><td>Average session</td><td>{session}</td></tr>")
    posts = "".join(
        f'<div class="community_post" id="post-{i}"><span class="post_author"><a href="https://itch.io/profile/u{i}">u{i}</a>'
        f'</span><span class="post_date"><a href="https://itch.io/post/{i}">3 days ago</a></span>'
        f'<div class="post_body"><p>Ran it, comment {i}.</p></div></div>' for i in range(1, comments + 1))
    buy = f'<div class="buy_row"><span class="dollars">{price}</span></div>' if price else ""
    return (f'<html><head><meta name="itch:path" content="{path}">'
            f'<meta property="og:description" content="A one-shot for levels 2-4 about {title}.">'
            f'<meta property="og:image" content="https://img.itch.zone/{path}.png"></head><body>'
            f'<h1 class="game_title">{title}</h1>{buy}<div class="game_info_panel_widget"><table>{"".join(rows)}'
            f'</table></div><a href="https://itch.io/games-like/{path}/x">More like this</a>{posts}</body></html>')


def snapshot_pages() -> dict[str, str]:
    return {
        "https://itch.io/robots.txt": ROBOTS,
        "https://itch.io/physical-games/tag-dnd": LIST_PAGE,
        "https://mira.itch.io/the-drowned-chapel": card_page(
            "games/945604", "The Drowned Chapel", [("Mira", "https://mira.itch.io")], ["horror", "one-shot"],
            "£4.00", "A few hours", 2),
        "https://duo.itch.io/goblin-market": card_page(
            "games/777001", "Goblin Market", [("Duo", "https://duo.itch.io"), ("Mira", "https://mira.itch.io")],
            ["horror", "dnd"], None, "A few seconds", 0),
        "https://mira.itch.io": '<html><body><div class="user_profile">Mira writes gothic horror.</div></body></html>',
        "https://duo.itch.io": "<html><head><meta name=\"description\" content=\"Two friends.\"></head></html>",
        "https://itch.io/physical-games/tag-horror": '<div class="tag_description">Scary tabletop games</div>',
        "https://itch.io/physical-games/tag-one-shot": "<html></html>",
    }


def build_snapshot(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        for url, text in snapshot_pages().items():
            stream.write(json.dumps({"url": url, "status": 200, "fetched_at": "2026-10-22T10:00:00+00:00",
                                     "retry_after": None, "text": text}) + "\n")
    return path


def write_srd_files(folder: Path) -> None:
    """Файли src/2014/en (і src/2024/en) у розкладці кешу tools.prepare.srd."""
    data = srd_subset()
    files = {"5e-SRD-Monsters.json": data.monsters, "5e-SRD-Spells.json": data.spells,
             "5e-SRD-Magic-Items.json": data.magic_items}
    files.update({f"5e-SRD-{name}.json": entries for name, entries in data.dictionaries.items()})
    (folder / "2014").mkdir(parents=True, exist_ok=True)
    (folder / "2024").mkdir(parents=True, exist_ok=True)
    for name, entries in files.items():
        (folder / "2014" / name).write_text(json.dumps(entries), encoding="utf-8")
    (folder / "2024" / "5e-SRD-Monsters.json").write_text(json.dumps(data.monsters_2024), encoding="utf-8")
