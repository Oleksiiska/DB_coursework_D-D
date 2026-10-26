"""Перевірка підготовлених CSV моделлю застосунку — без сховищ.

Кожен рядок проходить той самий етап підготовки документа, що й на шляху
запису (IngestDocumentHandler: валідація, нормалізація за словниками,
детермінований ключ, приведення типів, інваріанти сутності, обчислювані
поля). Рядок, який застосунок відхилив би, тут видно одразу, ще до запуску
системи. Додатково перевіряється посилальна цілісність між файлами.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path
from typing import Any

from app.application.commands.ingest_document import IngestDocumentHandler
from app.domain.services import DocumentValidator, EnvironmentClassifier, ShardKeyService, VocabularyNormalizer
from app.domain.values import DocumentKind
from tools.prepare.csvio import read_rows
from tools.prepare.report import Measurements, number, share, top

log = logging.getLogger("prepare.check")

FILES: dict[DocumentKind, str] = {
    DocumentKind.MONSTER: "monsters.csv",
    DocumentKind.CHARACTER: "characters.csv",
    DocumentKind.ENCOUNTER: "encounters.csv",
    DocumentKind.PARTICIPATION: "participations.csv",
    DocumentKind.ADVENTURE: "adventures.csv",
    DocumentKind.AUTHOR: "authors.csv",
    DocumentKind.TAG: "tags.csv",
}


def write_path_handler(vocabulary: VocabularyNormalizer) -> IngestDocumentHandler:
    """Обробник шляху запису зі справжніми доменними службами; сховища не
    потрібні — підготовка документа до них не звертається."""
    return IngestDocumentHandler(
        repositories={kind: None for kind in DocumentKind},  # type: ignore[misc]
        indexes={kind: None for kind in DocumentKind if kind.is_indexed},  # type: ignore[misc]
        graph=None, metrics=None, quality_log=None,  # type: ignore[arg-type]
        validator=DocumentValidator(), shard_keys=ShardKeyService(3), classifier=EnvironmentClassifier(),
        vocabulary=vocabulary,
    )


#: Що потрібно від сутності для перевірки посилань і вимірів
_EXTRACT: dict[DocumentKind, Any] = {
    DocumentKind.MONSTER: lambda x: (x.monster_id, tuple(x.environment), bool(x.abilities_text)),
    DocumentKind.CHARACTER: lambda x: (x.character_id, tuple(x.encounter_ids)),
    DocumentKind.ENCOUNTER: lambda x: (x.encounter_id, tuple(x.player_character_ids)),
    DocumentKind.PARTICIPATION: lambda x: (x.encounter_id, x.monster_id),
    DocumentKind.ADVENTURE: lambda x: (tuple(x.author_ids), tuple(x.tags)),
    DocumentKind.AUTHOR: lambda x: x.author_id,
    DocumentKind.TAG: lambda x: x.name,
}


def check(csv_dir: Path, m: Measurements, tag_aliases: dict[str, str]) -> int:
    """Повертає кількість рядків, які застосунок відхилив би."""
    key = "check"
    m.section(key, "Перевірка CSV моделлю застосунку (таблиця 2.5, п. 2.11)")
    vocabulary = VocabularyNormalizer(tag_aliases)
    handler = write_path_handler(vocabulary)
    found: dict[DocumentKind, list[Any]] = {}
    rejected_total = rows_total = 0
    for kind, name in FILES.items():
        path = csv_dir / name
        if not path.is_file():
            m.add(key, "табл. 2.3", name, "файлу немає")
            continue
        total = rejected = 0
        reasons: Counter = Counter()
        kept: list[Any] = []
        for row in read_rows(path):
            total += 1
            # так само, як CsvSource: вид і походження додає джерело
            entity, problems = handler._prepare(kind, {**row, "kind": kind.value, "origin": "csv"})
            if problems:
                rejected += 1
                reasons.update(problem.split(":")[0] for problem in problems)
            else:
                kept.append(_EXTRACT[kind](entity))
        found[kind] = kept
        rejected_total += rejected
        rows_total += total
        m.add(key, "табл. 2.3", name, f"рядків {number(total)}, відхилено б {number(rejected)}"
              + (f" ({top(reasons, 5)})" if rejected else ""))
        log.info("%s: перевірено %d рядків, відхилено б %d", name, total, rejected)
    m.add(key, "табл. 2.3, А.1, А.13", "разом рядків у CSV", number(rows_total), "~66 700")
    unknown = sorted({f"{vocabulary_name}: {term}" for vocabulary_name, term in vocabulary.drain_unknown()})
    if unknown:
        m.add(key, "п. 2.11", "значення поза словниками (журнал нерозпізнаних)",
              f"{number(len(unknown))}: " + ", ".join(unknown[:15]) + (" …" if len(unknown) > 15 else ""))
    _references(found, m, key)
    monsters = found.get(DocumentKind.MONSTER) or []
    if monsters:
        environments = Counter(env for _, envs, _ in monsters for env in envs)
        m.add(key, "п. 2.8.1, табл. А.7", "середовище монстра (класифікатор застосунку)",
              f"{share(sum(1 for _, envs, _ in monsters if envs), len(monsters))}; {top(environments, 8)}", "100%")
        m.add(key, "табл. А.7", "abilities_text", share(sum(1 for *_, text in monsters if text), len(monsters)), "99%")
    return rejected_total


def _references(found: dict[DocumentKind, list[Any]], m: Measurements, key: str) -> None:
    monsters = {x[0] for x in found.get(DocumentKind.MONSTER, [])}
    characters = {x[0] for x in found.get(DocumentKind.CHARACTER, [])}
    encounters = {x[0] for x in found.get(DocumentKind.ENCOUNTER, [])}
    authors = set(found.get(DocumentKind.AUTHOR, []))
    tags = set(found.get(DocumentKind.TAG, []))
    participations = found.get(DocumentKind.PARTICIPATION, [])
    adventures = found.get(DocumentKind.ADVENTURE, [])
    checks = [
        ("участь → зустріч", [e for e, _ in participations], encounters),
        ("участь → монстр", [mid for _, mid in participations if mid], monsters),
        ("персонаж → зустріч", [i for _, ids in found.get(DocumentKind.CHARACTER, []) for i in ids], encounters),
        ("зустріч → персонаж", [i for _, ids in found.get(DocumentKind.ENCOUNTER, []) for i in ids], characters),
        ("пригода → автор", [i for ids, _ in adventures for i in ids], authors),
        ("пригода → тег", [t for _, names in adventures for t in names], tags),
    ]
    for name, links, targets in checks:
        if links and targets:
            m.add(key, "п. 2.2", f"посилання {name}", share(sum(1 for link in links if link in targets), len(links)))


def tag_aliases(data_dir: Path) -> dict[str, str]:
    """Варіант написання → канонічний тег (app/data/tag_aliases.json), як у Container."""
    aliases: dict[str, str] = {}
    path = data_dir / "tag_aliases.json"
    if path.is_file():
        for entry in json.loads(path.read_text(encoding="utf-8")):
            for alias in [entry["name"], *entry["aliases"]]:
                aliases[alias.lower()] = entry["name"]
    return aliases
