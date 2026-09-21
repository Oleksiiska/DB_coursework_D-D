"""Сценарій приймання документів — шлях команд CQRS."""

from __future__ import annotations

from app.application.dto import IngestReport
from app.domain.ports import (
    AdventureRepository,
    GraphRepository,
    MetricsSink,
    SearchIndex,
)
from app.domain.services import (
    DocumentValidator,
    EnvironmentClassifier,
    ShardKeyService,
    VocabularyNormalizer,
)


class IngestDocumentHandler:
    """Єдиний обробник для всіх джерел.

    Оскільки CSV, генератор і краулер реалізують спільний порт,
    логіка валідації, трансформацій, обчислення ключа розподілу
    та індексації існує в одному екземплярі.

    Найскладніший обробник системи: використовує всі три доменні
    служби перетворення й пише у три сховища.
    """

    def __init__(
        self,
        repository: AdventureRepository,
        index: SearchIndex,
        graph: GraphRepository,
        metrics: MetricsSink,
        validator: DocumentValidator,
        shard_keys: ShardKeyService,
        classifier: EnvironmentClassifier,
        vocabulary: VocabularyNormalizer,
    ) -> None:
        self._repository = repository
        self._index = index
        self._graph = graph
        self._metrics = metrics
        self._validator = validator
        self._shard_keys = shard_keys
        self._classifier = classifier
        self._vocabulary = vocabulary

    async def handle(self, batch: list[dict], source_name: str) -> IngestReport:
        """Обробка пакета: валідація, трансформації, паралельний запис.

        Запис виконується пакетами, а не поодинці: кількість звернень
        до сховища зменшується на два порядки.
        """
        raise NotImplementedError

    def _transform(self, document: dict) -> dict:
        """Трансформації формату та обчислювані поля.

        Розбір складених рядків винесено в офлайн-підготовку,
        тут лишається приведення типів і похідні значення.
        """
        raise NotImplementedError

    async def _persist(self, documents: list[dict]) -> tuple[int, int, int]:
        """Паралельний запис у три сховища. Повертає (збережено, проіндексовано, ребер)."""
        raise NotImplementedError

    async def _reject(self, document: dict, reasons: list[str]) -> None:
        """Журнал браку."""
        raise NotImplementedError
