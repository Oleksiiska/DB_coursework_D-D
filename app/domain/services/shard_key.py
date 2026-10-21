"""Обчислення ключа розподілу документів між вузлами (п. 4.3, таблиця 4.7)."""

from __future__ import annotations

import hashlib
import uuid
from typing import Iterable

from app.domain.values import ShardKey


class ShardKeyService:
    """Ключ розподілу — хеш ідентифікатора за модулем 1024.

    Природний ключ (наприклад, ігровий світ чи система) дає перекіс:
    більшість модулів належить кільком популярним значенням. Хеш
    ідентифікатора розподіляє документи рівномірно за будь-якого формату
    ключів.

    Діапазон кошиків 0–1023 наперед поділено на зони, по одній на шард:
    шард i отримує кошики, для яких ``кошик * shard_count // 1024 == i``.
    Тому розміщення документа відоме застосунку й не залежить від
    балансувальника. Ті самі межі задає сервер конфігурації
    (docker/mongo-init.js); їх збіг перевіряє модульний тест.

    Значення зберігається рядком із чотирьох цифр («0517»): для рядків
    однакової довжини лексикографічний порядок збігається з числовим,
    тож межі зон однакові для застосунку й сховища.
    """

    BUCKETS = 1024

    def __init__(self, shard_count: int = 3) -> None:
        if not 1 <= shard_count <= self.BUCKETS:
            raise ValueError(f"кількість шардів має бути від 1 до {self.BUCKETS}")
        self.shard_count = shard_count

    @staticmethod
    def canonical_id(entity_id: str) -> str:
        """Канонічне рядкове подання ідентифікатора: UUID — 36 символів у
        нижньому регістрі; ідентифікатор іншого вигляду — без змін."""
        try:
            return str(uuid.UUID(entity_id))
        except ValueError:
            return entity_id

    @staticmethod
    def stable_hash(text: str) -> int:
        """Перші вісім байтів дайджесту SHA-1, прочитані як беззнакове ціле
        (старший байт першим) — п. 4.3, таблиця А.2.

        Вбудований hash() для рядків рандомізується в кожному процесі Python,
        тож процеси запису й читання отримали б різні ключі для одного
        документа. SHA-1 детермінований і є в стандартній бібліотеці.
        """
        return int.from_bytes(hashlib.sha1(text.encode("utf-8")).digest()[:8], "big")

    def bucket(self, entity_id: str) -> int:
        """Номер кошика 0–1023: хеш канонічного ідентифікатора за модулем 1024."""
        return self.stable_hash(self.canonical_id(entity_id)) % self.BUCKETS

    def key_value(self, entity_id: str) -> str:
        """Значення поля shard_key: номер кошика з провідними нулями."""
        return f"{self.bucket(entity_id):04d}"

    def shard_key(self, entity_id: str) -> ShardKey:
        bucket = self.bucket(entity_id)
        return ShardKey(value=f"{bucket:04d}", shard_index=bucket * self.shard_count // self.BUCKETS)

    def shard_for(self, entity_id: str) -> int:
        return self.shard_key(entity_id).shard_index

    def zone_bounds(self) -> list[tuple[str | None, str | None]]:
        """Межі зон [нижня, верхня) як значення shard_key.

        None на краях означає MinKey і MaxKey: перша й остання зони
        покривають увесь простір ключів.
        """
        lows = [-(-i * self.BUCKETS // self.shard_count) for i in range(self.shard_count)]
        bounds: list[tuple[str | None, str | None]] = []
        for i, low in enumerate(lows):
            lower = None if i == 0 else f"{low:04d}"
            upper = None if i == self.shard_count - 1 else f"{lows[i + 1]:04d}"
            bounds.append((lower, upper))
        return bounds

    def distribution(self, entity_ids: Iterable[str]) -> dict[int, int]:
        """Фактичний розподіл — для перевірки рівномірності в тестах
        і на панелі моніторингу."""
        counts = {i: 0 for i in range(self.shard_count)}
        for entity_id in entity_ids:
            counts[self.shard_for(entity_id)] += 1
        return counts
