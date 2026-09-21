"""Обчислення ключа розподілу документів між вузлами."""

from __future__ import annotations

from app.domain.values import ShardKey


class ShardKeyService:
    """Хешований ключ розподілу.

    Природний ключ (наприклад, ігрова система) дає перекіс: більшість
    модулів належить одній системі. Тому розподіл виконується за
    хешем ідентифікатора.
    """

    def __init__(self, shard_count: int = 3) -> None:
        self.shard_count = shard_count

    def shard_key(self, entity_id: str) -> ShardKey:
        raise NotImplementedError

    def shard_for(self, entity_id: str) -> int:
        raise NotImplementedError

    def distribution(self, entity_ids: list[str]) -> dict[int, int]:
        """Фактичний розподіл — для перевірки рівномірності в тестах."""
        raise NotImplementedError
