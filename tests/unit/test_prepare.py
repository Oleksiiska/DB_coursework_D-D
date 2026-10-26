"""Офлайн-підготовка даних (tools/prepare): розбір форматів джерел, два
проходи архіву FIREBALL, відтворення обходу каталогу, докачування файлу й
головне — підготовлені CSV проходять справжній шлях запису без жодного
відхиленого документа."""

from __future__ import annotations

import http.server
import json
import threading
from collections import Counter
from pathlib import Path

import pytest

from app.application.commands import IngestDocumentHandler
from app.domain.services import DocumentValidator, EnvironmentClassifier, ShardKeyService, VocabularyNormalizer
from app.domain.values import DocumentKind
from app.infrastructure.config.container import Settings
from app.infrastructure.sources import CsvSource
from tests.unit import prepare_fixtures as fx
from tests.unit.fakes import (
    FakeDataQualityLog,
    FakeGraphRepository,
    FakeMetricsSink,
    FakeSearchIndex,
    repository_registry,
)
from tools.prepare import catalog, check, srd
from tools.prepare.__main__ import EXIT_OK, main
from tools.prepare.csvio import CsvTable, cell, read_rows
from tools.prepare.fireball import FireballPreparation, character_id, main_class, parse_classes, parse_hp, split_list
from tools.prepare.report import Measurements
from tools.prepare.sources import SRD_COMMIT, NotEnoughSpace, download, file_name, fireball_url

CSV_FILES = {
    DocumentKind.MONSTER: "monsters.csv", DocumentKind.CHARACTER: "characters.csv",
    DocumentKind.ENCOUNTER: "encounters.csv", DocumentKind.PARTICIPATION: "participations.csv",
    DocumentKind.ADVENTURE: "adventures.csv", DocumentKind.AUTHOR: "authors.csv", DocumentKind.TAG: "tags.csv",
}


def rows(path: Path) -> list[dict]:
    return list(read_rows(path))


def by(rows_: list[dict], key: str) -> dict[str, dict]:
    return {row[key]: row for row in rows_}


# --------------------------------------------------------------------------
# Розбір полів (таблиці 2.7, 2.9)
# --------------------------------------------------------------------------


def test_actor_fields_are_parsed_as_the_dataset_authors_wrote_them() -> None:
    assert parse_hp("<18/18 HP; Healthy>")[:3] == (18, 18, "Healthy")
    assert parse_hp("<-2/52 HP; Dead>")[:3] == (-2, 52, "Dead")
    assert parse_hp("<11/11 HP; Healthy> (+5 temp)")[:3] == (11, 11, "Healthy")
    assert parse_hp("<12/34; Bloodied>")[:3] == (12, 34, "Bloodied")  # приклад із README датасету
    assert parse_hp("<7 HP>") == (7, None, None, "numbers")
    assert parse_hp("<0/0 HP>") == (0, 0, None, "numbers")
    assert parse_hp("") == (None, None, None, "empty") and parse_hp(None).form == "empty"
    assert parse_hp("<Unknown>").form == "unparsed"
    assert parse_classes("Fighter 2/Warlock 3") == [("Fighter", 2), ("Warlock", 3)]
    assert parse_classes("Blood Hunter 5") == [("Blood Hunter", 5)]
    assert parse_classes("Rogue") == [("Rogue", None)] and parse_classes(None) == []
    assert main_class(parse_classes("Fighter 2/Warlock 3")) == ("Warlock", 5)
    assert main_class(parse_classes("Wizard 3/Cleric 3")) == ("Wizard", 6)
    assert main_class(parse_classes("Rogue")) == ("Rogue", None)
    assert split_list("Hex, Eldritch Blast,  Shield, Eldritch Blast, ") == ["Hex", "Eldritch Blast", "Shield"]
    assert split_list(None) == []


def test_srd_monster_row_handles_the_format_traps() -> None:
    data = fx.srd_subset()
    records = {m["index"]: m for m in data.monsters}
    row = srd.monster_row(records["aboleth"], play_count=7)
    block = row["statblock"]
    assert row["monster_id"] == DocumentKind.MONSTER.natural_id("aboleth") and row["slug"] == "aboleth"
    assert row["lore"] == "Aboleths lurk in subterranean lakes." and row["play_count"] == 7
    assert row["image_url"] == "https://www.dnd5eapi.co/api/images/monsters/aboleth.png"
    assert (block["armor_class"], block["ac_type"]) == (13, "natural")
    assert block["speed"] == {"walk": 10, "swim": 40}
    assert block["abilities_scores"]["str"] == 10 and set(block["abilities_scores"]) == {"str", "dex", "con", "int",
                                                                                          "wis", "cha"}
    assert block["saving_throws"] == {"con": 6} and block["skills"] == {"history": 12}
    assert block["languages"] == ["Deep Speech", "telepathy 120 ft."]
    assert block["senses"] == {"darkvision": "60 ft.", "passive_perception": "9"}
    assert block["legendary_actions"] == [{"name": "Detect", "description": "The aboleth makes a Wisdom check."}]
    skeleton = srd.statblock(records["skeleton"])
    assert skeleton["condition_immunities"] == ["Poisoned"] and skeleton["damage_vulnerabilities"] == ["bludgeoning"]
    assert srd.statblock({**records["wolf"], "speed": {"walk": "30 ft.", "fly": "60 ft.", "hover": True}})["speed"] == {
        "walk": 30, "fly": 60}
    assert srd.split_languages("Giant Eagle, understands Common and Auran but can't speak them") == [
        "Giant Eagle", "understands Common and Auran but can't speak them"]
    assert srd.split_languages("understands Abyssal, Common, and Infernal but can't speak, telepathy 120 ft.") == [
        "understands Abyssal, Common, and Infernal but can't speak", "telepathy 120 ft."]
    assert srd.split_languages("") == [] and srd.monster_row(records["wolf"])["lore"] is None


def test_csv_cells_follow_the_readme_format(tmp_path: Path) -> None:
    assert (cell(None), cell(True), cell(0.25), cell("x")) == ("", "true", "0.25", "x")
    assert cell(["a", "б"]) == '["a","б"]' and cell({"k": 1}) == '{"k":1}'
    with CsvTable(tmp_path / "t.csv", ["a", "b"]) as table:
        table.write({"a": "line\nbreak", "b": [1, 2]})
    assert rows(tmp_path / "t.csv") == [{"a": "line\nbreak", "b": "[1,2]"}]
    with pytest.raises(KeyError):
        with CsvTable(tmp_path / "u.csv", ["a"]) as table:
            table.write({"a": 1, "typo": 2})
    # перерваний запис не лишає ні готового файлу, ні частини
    assert not (tmp_path / "u.csv").exists() and not (tmp_path / "u.csv.part").exists()


# --------------------------------------------------------------------------
# FIREBALL: два проходи архіву (п. 2.5)
# --------------------------------------------------------------------------


@pytest.fixture(params=[True, False], ids=["data-first", "filtered-first"])
def prepared(request, tmp_path: Path) -> tuple[Path, Measurements]:
    archive = fx.build_archive(tmp_path / "fireball.tar.gz", data_first=request.param)
    out = tmp_path / "csv"
    m = Measurements()
    result = FireballPreparation(archive, fx.srd_subset(), out, m).run()
    srd.write_monsters(fx.srd_subset(), out / "monsters.csv", result.play_counts)
    return out, m


def test_participants_are_split_into_characters_and_monster_instances(prepared) -> None:
    out, _ = prepared
    characters = by(rows(out / "characters.csv"), "name")
    assert set(characters) == {"Verity", "Keya", "Grom", "Lonely"}
    verity = characters["Verity"]
    # дедуплікація за name + controller_id: стан із найпізнішого бою
    assert verity["character_id"] == character_id("Verity", "111") == DocumentKind.CHARACTER.natural_id("Verity:111")
    assert (verity["char_class"], verity["level"], verity["hp_current"], verity["hp_status"]) == ("Rogue", "4", "24",
                                                                                                   "Healthy")
    assert json.loads(verity["encounter_ids"]) == [fx.COMBAT_A, fx.COMBAT_B]
    keya = characters["Keya"]
    assert json.loads(keya["classes_all"]) == [{"char_class": "Fighter", "level": 2}, {"char_class": "Warlock", "level": 3}]
    assert (keya["char_class"], keya["level"]) == ("Warlock", "5")
    assert json.loads(keya["spells"]) == ["Hex", "Eldritch Blast", "Shield"]
    assert json.loads(keya["spells_srd"]) == ["Eldritch Blast", "Shield"]
    assert json.loads(keya["combat_payload"]) == {"attacks": ["Longsword"], "actions": [], "effects": ["Hex"]}
    assert characters["Lonely"]["race"] == "" and characters["Grom"]["race"] == "Goblin"

    participations = {(r["encounter_id"], r["instance_label"]): r for r in rows(out / "participations.csv")}
    assert set(participations) == {(fx.COMBAT_A, "SK1"), (fx.COMBAT_A, "Strahd"), (fx.COMBAT_A, "Guard"),
                                   (fx.COMBAT_B, "GO1"), (fx.COMBAT_B, "WO1"), (fx.COMBAT_C, "ZO1")}
    skeleton = participations[(fx.COMBAT_A, "SK1")]
    assert skeleton["monster_id"] == DocumentKind.MONSTER.natural_id("skeleton")
    assert (skeleton["hp_current"], skeleton["hp_status"]) == ("0", "Dead")  # останній стан у бою
    assert participations[(fx.COMBAT_A, "Strahd")]["monster_id"] == ""  # істота поза SRD — лише назва
    assert participations[(fx.COMBAT_A, "Strahd")]["monster_name_raw"] == "Strahd von Zarovich"
    assert participations[(fx.COMBAT_A, "Guard")]["monster_name_raw"] == "Guard"
    assert participations[(fx.COMBAT_B, "WO1")]["monster_id"] == DocumentKind.MONSTER.natural_id("wolf")
    assert (participations[(fx.COMBAT_B, "GO1")]["hp_current"], participations[(fx.COMBAT_B, "GO1")]["hp_max"]) == (
        "7", "")

    # персонаж із расою, однойменною з монстром, до популярності не додається (таблиця 2.8)
    monsters = by(rows(out / "monsters.csv"), "slug")
    assert {slug: monsters[slug]["play_count"] for slug in ("skeleton", "goblin", "wolf", "zombie", "aboleth")} == {
        "skeleton": "1", "goblin": "1", "wolf": "1", "zombie": "1", "aboleth": "0"}


def test_encounters_take_metadata_and_narrative_from_raw_events(prepared) -> None:
    out, m = prepared
    encounters = by(rows(out / "encounters.csv"), "encounter_id")
    assert set(encounters) == {fx.COMBAT_A, fx.COMBAT_B, fx.COMBAT_C}  # бій лише з сирими подіями — не зустріч
    a = encounters[fx.COMBAT_A]
    assert a["started_at"] == "2022-04-15T05:20:00+00:00"  # перша половина імені файлу
    assert (a["duration_seconds"], a["dm_hash"], a["channel_hash"]) == ("1800", "100", "42")
    assert (a["rounds_count"], a["participants_count"], a["messages_count"]) == ("2", "5", "5")
    # наратив — лише повідомлення ведучого без ознаки бота
    assert a["narrative_text"] == "The crypt door groans open.\nThe skeleton crumbles to dust."
    assert json.loads(a["commands"]) == ["init attack", "cast", "init attack", "check"]
    assert sorted(json.loads(a["player_character_ids"])) == sorted([character_id("Verity", "111"),
                                                                    character_id("Keya", "222")])
    assert json.loads(a["derived_fields"]) == ["duration_seconds", "messages_count"]
    assert encounters[fx.COMBAT_B]["duration_seconds"] == str(9 * 3600 - 0)
    c = encounters[fx.COMBAT_C]
    assert c["started_at"] == "2022-04-17T12:53:20+00:00" and c["narrative_text"] == "" and c["ended_at"] == ""
    measures = {(x.where, x.what): x.value for s in m.sections.values() for x in s.measures}
    assert measures[("п. 2.5.2", "боїв у каталозі data (сирі події)")] == "3"
    assert measures[("п. 2.5.5", "повідомлень з автором data.dm / з ознакою бота")] == "4 / 25% (1 з 4)"
    assert measures[("табл. 2.8", "race = назва монстра SRD, class заповнений (хибні спрацювання)")].startswith("1 ")


async def test_prepared_csv_passes_the_real_write_path(prepared) -> None:
    """Те, що відкидає застосунок, тут дало б rejected > 0."""
    out, m = prepared
    parts = {"repositories": repository_registry(),
             "indexes": {k: FakeSearchIndex() for k in DocumentKind if k.is_indexed},
             "graph": FakeGraphRepository(), "metrics": FakeMetricsSink(), "quality_log": FakeDataQualityLog()}
    ingest = IngestDocumentHandler(**parts, validator=DocumentValidator(), shard_keys=ShardKeyService(3),
                                   classifier=EnvironmentClassifier(), vocabulary=VocabularyNormalizer({}))
    saved: Counter = Counter()
    for kind in (DocumentKind.MONSTER, DocumentKind.CHARACTER, DocumentKind.ENCOUNTER, DocumentKind.PARTICIPATION):
        async for batch in CsvSource(out / CSV_FILES[kind], kind.value, rate=100000).stream():
            report = await ingest.handle(batch.documents, f"csv:{kind.value}")
            assert report.rejected == 0, parts["quality_log"].rejected_documents
            saved[kind] += report.saved
    assert saved == {DocumentKind.MONSTER: 6, DocumentKind.CHARACTER: 4, DocumentKind.ENCOUNTER: 3,
                     DocumentKind.PARTICIPATION: 6}
    repositories = parts["repositories"]
    lonely = await repositories[DocumentKind.CHARACTER].get(character_id("Lonely", "444"))
    assert lonely.char_class == "Blood Hunter"  # псевдонім класу зводить застосунок (п. 2.5.3)
    b = await repositories[DocumentKind.ENCOUNTER].get(fx.COMBAT_B)
    assert b.is_abandoned and b.duration_seconds == 9 * 3600
    skeleton = await repositories[DocumentKind.MONSTER].get(DocumentKind.MONSTER.natural_id("skeleton"))
    assert skeleton.play_count == 1 and skeleton.environment and skeleton.abilities_text
    # та сама перевірка без сховищ — у звіті вимірювань
    assert check.check(out, m, {}) == 0


# --------------------------------------------------------------------------
# Каталог: відтворення обходу зі знімка (п. 2.6)
# --------------------------------------------------------------------------


async def test_catalog_is_rebuilt_from_a_crawl_snapshot(tmp_path: Path) -> None:
    snapshot = fx.build_snapshot(tmp_path / "raw" / "itch" / "crawl-20261022-1000-full.jsonl.gz")
    result = await catalog.crawl(Settings(), [], tmp_path / "raw", replay=snapshot)
    assert sorted(a["itch_id"] for a in result.adventures) == ["games/777001", "games/945604"]
    assert {a["name"]: a["bio"] for a in result.authors} == {"Mira": "Mira writes gothic horror.", "Duo": "Two friends."}
    assert {t["name"]: (t["description"], t["category"]) for t in result.tags} == {
        "horror": ("Scary tabletop games", "Theme"), "one-shot": (None, "Format"), "dnd": (None, None)}
    counts = catalog.write_csv(result, tmp_path / "csv")
    assert counts == {"adventures.csv": 2, "authors.csv": 2, "tags.csv": 3}
    adventure = by(rows(tmp_path / "csv" / "adventures.csv"), "itch_id")["games/945604"]
    assert "kind" not in adventure and "origin" not in adventure  # їх додає CsvSource
    m = Measurements()
    catalog.measure(result, m, None)
    measures = {x.what: x.value for x in m.sections["catalog"].measures}
    assert measures["модулів за тегом dnd"] == "2" and measures["валюти цін"] == "£ (1)"
    assert measures["множинне авторство"] == "50% (1 з 2)"
    assert measures["значення Average session"] == "A few hours (1), A few seconds (1)"
    assert measures["robots.txt: заборонені маршрути"] == "/embed/, /search, /game/download/"
    assert check.check(tmp_path / "csv", m, {}) == 0


async def test_sampled_crawl_takes_a_reproducible_subset(tmp_path: Path) -> None:
    snapshot = fx.build_snapshot(tmp_path / "s.jsonl.gz")
    first = await catalog.crawl(Settings(), [], tmp_path, sample=1, seed=7, replay=snapshot)
    second = await catalog.crawl(Settings(), [], tmp_path, sample=1, seed=7, replay=snapshot)
    assert len(first.adventures) == 1 and first.adventures[0]["itch_id"] == second.adventures[0]["itch_id"]


# --------------------------------------------------------------------------
# Завантаження з докачуванням
# --------------------------------------------------------------------------


class _FlakyHandler(http.server.BaseHTTPRequestHandler):
    """Перша відповідь обривається на третині файлу; далі — Range."""

    payload = bytes(range(256)) * 4000
    requests: list[str | None] = []

    def do_GET(self) -> None:  # noqa: N802
        wanted = self.headers.get("Range")
        self.requests.append(wanted)
        if wanted is None:
            self.send_response(200)
            self.send_header("Content-Length", str(len(self.payload)))
            self.end_headers()
            self.wfile.write(self.payload[: len(self.payload) // 3])
            self.wfile.flush()
            self.close_connection = True
            return
        start = int(wanted.split("=")[1].rstrip("-"))
        self.send_response(206)
        self.send_header("Content-Range", f"bytes {start}-{len(self.payload) - 1}/{len(self.payload)}")
        self.send_header("Content-Length", str(len(self.payload) - start))
        self.end_headers()
        self.wfile.write(self.payload[start:])

    def log_message(self, *args) -> None:
        pass


@pytest.fixture()
def flaky_server():
    _FlakyHandler.requests = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FlakyHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/fireball.tar.gz"
    server.shutdown()


def test_download_resumes_after_a_dropped_connection(tmp_path: Path, flaky_server: str) -> None:
    target = download(flaky_server, tmp_path / "fireball.tar.gz", backoff=0)
    assert target.read_bytes() == _FlakyHandler.payload
    assert _FlakyHandler.requests == [None, f"bytes={len(_FlakyHandler.payload) // 3}-"]
    assert not (tmp_path / "fireball.tar.gz.part").exists()
    assert download(flaky_server, target) == target and len(_FlakyHandler.requests) == 2  # уже є — не завантажує


def test_download_stops_when_the_disk_is_too_small(tmp_path: Path, flaky_server: str, monkeypatch) -> None:
    from tools.prepare import sources

    monkeypatch.setattr(sources.shutil, "disk_usage", lambda _: type("U", (), {"free": 10})())
    with pytest.raises(NotEnoughSpace):
        download(flaky_server, tmp_path / "x.tar.gz", backoff=0)


def test_fireball_source_is_the_full_archive_of_the_dataset_authors() -> None:
    assert fireball_url().startswith("https://datasets.mechanus.zhu.codes/")
    assert file_name(fireball_url()) == "fireball-anonymized-nov-28-2022-kfdjl.tar.gz"


# --------------------------------------------------------------------------
# Увесь запуск: python -m tools.prepare --offline
# --------------------------------------------------------------------------


def test_offline_run_writes_all_csv_and_the_measurement_report(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    fx.write_srd_files(raw / "srd" / SRD_COMMIT)
    fx.build_archive(raw / "fireball" / file_name(fireball_url()))
    fx.build_snapshot(raw / "itch" / "crawl-20261022-1000-full.jsonl.gz")
    code = main(["--raw", str(raw), "--out", str(tmp_path / "csv"), "--logs", str(tmp_path / "logs"), "--offline"])
    assert code == EXIT_OK
    produced = {path.name: len(rows(path)) for path in (tmp_path / "csv").glob("*.csv")}
    assert produced == {"monsters.csv": 6, "characters.csv": 4, "encounters.csv": 3, "participations.csv": 6,
                        "adventures.csv": 2, "authors.csv": 2, "tags.csv": 3}
    report = (tmp_path / "csv" / "measurements.md").read_text(encoding="utf-8")
    assert "відхилено б 0 рядків" in report and "## Крок 4. Каталог itch.io" in report
    data = json.loads((tmp_path / "csv" / "measurements.json").read_text(encoding="utf-8"))
    assert list(data["steps"]) == ["1. SRD", "2–3. FIREBALL", "4. Каталог itch.io", "Перевірка моделлю застосунку"]
    assert data["snapshots"]["5e SRD"].endswith("src/2014/en")
    assert (tmp_path / "logs" / "prepare.log").exists()


def test_damaged_member_is_skipped_and_the_rest_of_the_archive_is_read(tmp_path: Path) -> None:
    import io
    import tarfile

    archive = fx.build_archive(tmp_path / "ok.tar.gz")
    damaged = tmp_path / "damaged.tar.gz"
    with tarfile.open(archive, "r:gz") as source, tarfile.open(damaged, "w:gz") as target:
        for member in source.getmembers():
            payload = source.extractfile(member).read() if member.isfile() else None
            if member.name.endswith(f"filtered/{fx.COMBAT_C}.jsonl.gz"):
                payload = payload[:20] + b"\x00" * 40  # обрізаний gzip
                member.size = len(payload)
            target.addfile(member, io.BytesIO(payload) if payload is not None else None)
    m = Measurements()
    result = FireballPreparation(damaged, fx.srd_subset(), tmp_path / "csv", m).run()
    assert (result.characters, result.encounters) == (3, 2)  # бій C пропущено, A і B — на місці
    assert any("Пошкоджених файлів: 1" in note for note in m.sections["fireball"].notes)


def test_archive_of_another_dataset_stops_only_the_fireball_step(tmp_path: Path) -> None:
    import io
    import tarfile

    raw = tmp_path / "raw"
    fx.write_srd_files(raw / "srd" / SRD_COMMIT)
    fx.build_snapshot(raw / "itch" / "crawl-20261022-1000-full.jsonl.gz")
    (raw / "fireball").mkdir(parents=True)
    with tarfile.open(raw / "fireball" / file_name(fireball_url()), "w:gz") as tar:
        info = tarfile.TarInfo("readme.txt")
        info.size = 5
        tar.addfile(info, io.BytesIO(b"hello"))
    code = main(["--raw", str(raw), "--out", str(tmp_path / "csv"), "--logs", str(tmp_path / "logs"), "--offline"])
    assert code == 3
    steps = json.loads((tmp_path / "csv" / "measurements.json").read_text(encoding="utf-8"))["steps"]
    assert "немає файлів filtered" in steps["2–3. FIREBALL"] and steps["4. Каталог itch.io"].startswith("відтворено")
    assert {p.name for p in (tmp_path / "csv").glob("*.csv")} == {"monsters.csv", "adventures.csv", "authors.csv",
                                                                  "tags.csv"}


def test_interrupted_crawl_is_not_replayed_as_complete(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from tools.prepare.__main__ import CatalogStep

    recorder = catalog.Recorder(tmp_path / "itch" / "crawl-20261023-0900-full.jsonl.gz")
    recorder.close(completed=False)
    args = SimpleNamespace(catalog_replay=None, catalog_sample=None, raw=tmp_path, offline=False, catalog_fresh=False)
    assert CatalogStep(args, Settings(), []).choose_replay() is None  # лише .part — новий обхід
    complete = fx.build_snapshot(tmp_path / "itch" / "crawl-20261022-1000-full.jsonl.gz")
    sample = fx.build_snapshot(tmp_path / "itch" / "crawl-20261023-1000-sample100.jsonl.gz")
    assert CatalogStep(args, Settings(), []).choose_replay() == complete
    args.catalog_sample = 100
    assert CatalogStep(args, Settings(), []).choose_replay() == sample
    args.catalog_fresh = True
    assert CatalogStep(args, Settings(), []).choose_replay() is None
