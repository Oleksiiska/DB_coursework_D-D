"""Процес перевірки сховища: запис документа через порт і читання назад.

Одноразовий процес для перевірки зв'язку контейнера застосунку з
контейнером документного сховища (п. 3.6.4, 4.2). Отримує порт
`CrudRepository`, тож про конкретне сховище не знає: адаптер підставляє
композиційний корінь. Ключ розподілу обчислює доменна служба, тож той
самий процес без змін працює і з одним вузлом, і з шардованим
кластером — змінюється лише адреса сховища.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from app.domain.entities import Adventure, Review
from app.domain.ports import CrudRepository
from app.domain.services import ShardKeyService
from app.domain.values import LevelRange, Money, PartySize, Provenance, SourceOrigin

log = logging.getLogger("storage-check")


class StorageCheck:
    """Записує пригоду з вкладеними об'єктами, читає її за ключем і
    порівнює з записаною. Код завершення процесу — результат порівняння."""

    def __init__(self, repository: CrudRepository[Adventure], shard_keys: ShardKeyService) -> None:
        self._repository = repository
        self._shard_keys = shard_keys

    @staticmethod
    def sample(shard_keys: ShardKeyService) -> Adventure:
        """Документ зі складною структурою: діапазони, ціна, відгуки.

        Англомовний, як і весь корпус (п. 4.6). Ключ — новий UUID, тож
        кожен запуск додає нову пригоду. Час — з точністю до секунди:
        сховище зберігає мілісекунди, а порівняння має бути точним. Ключ
        розподілу обчислюється так само, як на шляху запису.
        """
        key = str(uuid.uuid4())
        return Adventure(
            adventure_id=key,
            title="The Cursed Village of Mirefen",
            synopsis="The party investigates the disappearance of villagers near the marshes.",
            provenance=Provenance(origin=SourceOrigin.GENERATED, derived_fields=["slug"]),
            slug=f"the-cursed-village-of-mirefen-{key[:8]}",
            system="Dungeons & Dragons",
            edition="5e (2014)",
            publication_language="English",
            levels=LevelRange(minimum=1, maximum=3),
            party=PartySize(minimum=3, maximum=5),
            duration_hours=4.0,
            price=Money(amount=0.0, currency="USD"),
            tags=["horror", "investigation"],
            reviews=[Review(review_id=f"{key[:8]}-r1", author_name="Game Master", text="Great for a first session.")],
            shard_key=shard_keys.key_value(key),
            created_at=datetime.now(timezone.utc).replace(microsecond=0),
        )

    async def run(self) -> bool:
        adventure = self.sample(self._shard_keys)
        # Зона — діапазон shard_key, закріплений за шардом (п. 4.3): у кластері
        # документ лягає на шард цієї зони, на одному вузлі — на єдиний вузол
        zone = self._shard_keys.shard_for(adventure.adventure_id)
        log.info(
            "запис пригоди %s «%s», shard_key %s (зона %d з %d)",
            adventure.adventure_id,
            adventure.title,
            adventure.shard_key,
            zone + 1,
            self._shard_keys.shard_count,
        )
        await self._repository.add(adventure)

        restored = await self._repository.get(adventure.adventure_id)
        if restored is None:
            log.error("пригоду %s не знайдено після запису", adventure.adventure_id)
            return False
        log.info(
            "прочитано: «%s», рівні %s–%s, гравців %s–%s, теги %s, відгуків %d",
            restored.title,
            restored.levels.minimum,
            restored.levels.maximum,
            restored.party.minimum,
            restored.party.maximum,
            ", ".join(restored.tags),
            len(restored.reviews),
        )
        same = restored == adventure
        log.info("прочитаний документ збігається із записаним: %s", "так" if same else "ні")
        log.info("пригод у колекції: %d", await self._repository.count())
        return same
