"""Процес перевірки сховища: запис документа через порт і читання назад.

Одноразовий процес для перевірки зв'язку контейнера застосунку з
контейнером документного сховища (п. 3.6.4, 4.2). Отримує порт
`CrudRepository`, тож про конкретне сховище не знає: адаптер підставляє
композиційний корінь.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from app.domain.entities import Adventure, Review
from app.domain.ports import CrudRepository
from app.domain.values import LevelRange, Money, PartySize, Provenance, SourceOrigin

log = logging.getLogger("storage-check")


class StorageCheck:
    """Записує пригоду з вкладеними об'єктами, читає її за ключем і
    порівнює з записаною. Код завершення процесу — результат порівняння."""

    def __init__(self, repository: CrudRepository[Adventure]) -> None:
        self._repository = repository

    @staticmethod
    def sample() -> Adventure:
        """Документ зі складною структурою: діапазони, ціна, відгуки.

        Час — з точністю до секунди: сховище зберігає мілісекунди, а
        порівняння має бути точним.
        """
        key = f"check-{uuid.uuid4().hex[:8]}"
        return Adventure(
            adventure_id=key,
            title="Прокляте село на болотах",
            synopsis="Партія розслідує зникнення мешканців села біля боліт.",
            provenance=Provenance(origin=SourceOrigin.GENERATED, derived_fields=["synopsis"]),
            system="D&D 5e",
            language="uk",
            levels=LevelRange(minimum=1, maximum=3),
            party=PartySize(minimum=3, maximum=5),
            duration_hours=4.0,
            price=Money(amount=0.0, currency="USD"),
            tags=["horror", "investigation"],
            reviews=[Review(review_id=f"{key}-r1", author_name="Ведучий", text="Добре для першої гри.")],
            created_at=datetime.now(timezone.utc).replace(microsecond=0),
        )

    async def run(self) -> bool:
        adventure = self.sample()
        log.info("запис пригоди %s «%s»", adventure.adventure_id, adventure.title)
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
