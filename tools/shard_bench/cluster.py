"""Розміщення колекції заміру на N шардах кластера."""

from __future__ import annotations

import logging
from typing import Any

from app.domain.services import ShardKeyService

log = logging.getLogger("shard-bench")


class ZoneLayout:
    """Колекції заміру в окремій базі на підмножинах шардів кластера.

    Зони заміру мають власні назви (``bench-<шард>``), тож налаштування колекції
    пригод системи (зони zone1–zone3, docker/mongo-init.js) не змінюються. Межі
    зон для N шардів обчислює та сама доменна служба ShardKeyService(N), що й
    для системи: значення shard_key документа від N не залежить, змінюється лише
    те, скільки шардів ділять діапазон кошиків.
    """

    ZONE_PREFIX = "bench-"
    SHARD_KEY = {"shard_key": 1, "_id": 1}

    def __init__(self, client: Any, database: str) -> None:
        self._client = client
        self._database = database
        self._admin = client.admin
        self._config = client["config"]

    async def shards(self) -> list[str]:
        """Шарди кластера за назвою; на одному вузлі без mongos — порожньо."""
        from pymongo.errors import OperationFailure

        try:
            reply = await self._admin.command("listShards")
        except OperationFailure:
            return []
        return sorted(shard["_id"] for shard in reply.get("shards", []))

    async def server_version(self) -> str:
        return (await self._admin.command("buildInfo")).get("version", "?")

    def namespace(self, collection: str) -> str:
        return f"{self._database}.{collection}"

    async def prepare(self, collection: str, shard_ids: list[str]) -> list[dict]:
        """Порожня колекція, розподілена між `shard_ids`: зона на шард, межі —
        ShardKeyService(len(shard_ids)).zone_bounds(). Зони задаються до
        shardCollection, тож початкові фрагменти одразу створюються на шардах
        своїх зон і балансувальник під час заміру нічого не переносить."""
        namespace = self.namespace(collection)
        await self.drop(collection)
        ranges = []
        bounds = ShardKeyService(len(shard_ids)).zone_bounds()
        for shard_id, (lower, upper) in zip(shard_ids, bounds):
            zone = self.ZONE_PREFIX + shard_id
            await self._admin.command({"addShardToZone": shard_id, "zone": zone})
            await self._admin.command({
                "updateZoneKeyRange": namespace, "min": self._lower(lower), "max": self._upper(upper), "zone": zone,
            })
            ranges.append({"shard": shard_id, "zone": zone, "lower": lower, "upper": upper})
        await self._admin.command({"shardCollection": namespace, "key": self.SHARD_KEY})
        return ranges

    async def chunks(self, collection: str) -> dict[str, int]:
        """Кількість фрагментів колекції на кожному шарді (метадані сервера
        конфігурації; з MongoDB 5.0 фрагменти прив'язані до uuid колекції)."""
        meta = await self._config.collections.find_one({"_id": self.namespace(collection)})
        if not meta:
            return {}
        cursor = await self._config.chunks.aggregate([
            {"$match": {"uuid": meta["uuid"]}},
            {"$group": {"_id": "$shard", "n": {"$sum": 1}}},
        ])
        return {row["_id"]: row["n"] for row in await cursor.to_list()}

    async def drop(self, collection: str) -> None:
        """Колекція й діапазони її зон (діапазони переживають видалення колекції)."""
        namespace = self.namespace(collection)
        await self._client[self._database].drop_collection(collection)
        tags = await self._config.tags.find({"ns": namespace}).to_list()
        for tag in tags:
            await self._admin.command({"updateZoneKeyRange": namespace, "min": tag["min"], "max": tag["max"],
                                       "zone": None})

    async def release(self, shard_ids: list[str]) -> None:
        """Зони заміру знімаються з шардів, коли жодна колекція їх не використовує."""
        from pymongo.errors import OperationFailure

        for shard_id in shard_ids:
            try:
                await self._admin.command({"removeShardFromZone": shard_id, "zone": self.ZONE_PREFIX + shard_id})
            except OperationFailure as error:
                log.debug("зону %s%s не знято: %s", self.ZONE_PREFIX, shard_id, error)

    @staticmethod
    def _lower(value: str | None) -> dict:
        from bson.min_key import MinKey

        return {"shard_key": MinKey() if value is None else value, "_id": MinKey()}

    @staticmethod
    def _upper(value: str | None) -> dict:
        from bson.max_key import MaxKey
        from bson.min_key import MinKey

        return {"shard_key": MaxKey(), "_id": MaxKey()} if value is None else {"shard_key": value, "_id": MinKey()}
