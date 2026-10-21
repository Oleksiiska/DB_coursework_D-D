"""Ключ розподілу: хеш ідентифікатора за модулем 1024 і зони шардів (п. 4.3)."""

from __future__ import annotations

import hashlib
import re
import uuid
from pathlib import Path

from app.domain.services import ShardKeyService

PROJECT = Path(__file__).resolve().parents[2]


#: Фіксовані пари «ідентифікатор — shard_key»: перші вісім байтів SHA-1 від
#: канонічного рядка adventure_id, беззнакове ціле за модулем 1024 (п. 4.3)
FIXED_PAIRS = [
    ("0c2306f4-cb41-539a-8087-4e7984429f6f", "0815"),
    ("99dac3b7-1995-55df-8aac-be9befe0bdc9", "0850"),
    ("f47ac10b-58cc-4372-a567-0e02b2c3d479", "0955"),
]


def test_hash_is_sha1_prefix_modulo_1024() -> None:
    """Незмінність функції: ключ однаковий у будь-якому процесі, на відміну
    від вбудованого hash(), що рандомізується для рядків."""
    for entity_id, shard_key in FIXED_PAIRS:
        assert ShardKeyService().key_value(entity_id) == shard_key
    digest = hashlib.sha1(b"abc").digest()
    assert ShardKeyService.stable_hash("abc") == int.from_bytes(digest[:8], "big")


def test_uuid_is_hashed_in_canonical_form() -> None:
    """Канонічне подання — 36 символів у нижньому регістрі."""
    entity_id, shard_key = FIXED_PAIRS[2]
    keys = ShardKeyService()
    assert keys.key_value(entity_id.upper()) == shard_key
    assert keys.key_value(entity_id.replace("-", "")) == shard_key


def test_key_value_is_four_digit_bucket() -> None:
    keys = ShardKeyService()
    for entity_id in ("adv-1", "check-0f3a9c21", str(uuid.uuid4())):
        value = keys.key_value(entity_id)
        assert re.fullmatch(r"\d{4}", value)
        assert 0 <= int(value) < ShardKeyService.BUCKETS
        assert keys.key_value(entity_id) == value


def test_three_zones_split_buckets_evenly() -> None:
    assert ShardKeyService(3).zone_bounds() == [(None, "0342"), ("0342", "0683"), ("0683", None)]


def test_shard_matches_its_zone() -> None:
    """Шард, обчислений застосунком, — той, чию зону бачить сервер конфігурації."""
    keys = ShardKeyService(3)
    bounds = keys.zone_bounds()
    for n in range(2000):
        entity_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"adventure-{n}"))
        lower, upper = bounds[keys.shard_for(entity_id)]
        value = keys.key_value(entity_id)
        assert (lower is None or lower <= value) and (upper is None or value < upper)


def test_distribution_is_even() -> None:
    keys = ShardKeyService(3)
    ids = [str(uuid.uuid5(uuid.NAMESPACE_URL, f"adventure-{n}")) for n in range(30_000)]
    counts = keys.distribution(ids)
    assert sum(counts.values()) == len(ids)
    for count in counts.values():
        assert abs(count / len(ids) - 1 / 3) < 0.02


def test_cluster_init_uses_the_same_zone_bounds() -> None:
    """Межі зон у сценарії ініціалізації кластера збігаються з тими, що
    обчислює доменна служба: інакше читання за ключем ішло б не на той шард."""
    script = (PROJECT / "docker" / "mongo-init.js").read_text(encoding="utf-8")
    match = re.search(r"const ZONE_BOUNDS = \[(.*?)\];", script, re.S)
    assert match, "у docker/mongo-init.js немає ZONE_BOUNDS"
    pairs = re.findall(r"\[\s*(null|\"\d{4}\")\s*,\s*(null|\"\d{4}\")\s*\]", match.group(1))
    parsed = [tuple(None if v == "null" else v.strip('"') for v in pair) for pair in pairs]
    assert parsed == ShardKeyService(3).zone_bounds()
