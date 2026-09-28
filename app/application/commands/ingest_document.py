"""Сценарій приймання документів — шлях команд CQRS."""

from __future__ import annotations

from typing import Mapping

from app.application.dto import IngestReport
from app.domain.ports import (
    CrudRepository,
    DataQualityLog,
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
from app.domain.values import DocumentKind


class IngestDocumentHandler:
    """Єдиний обробник для всіх джерел і всіх видів документів.

    Оскільки CSV, генератор і краулер реалізують спільний порт, логіка
    валідації, трансформацій, обчислення ключа розподілу та індексації
    існує в одному екземплярі.

    Сховище обирається за видом документа з реєстру репозиторіїв;
    індекс — з реєстру індексів. Перелік видів і ознака індексації
    оголошені в домені (`DocumentKind`), тож новий вид документа не
    змінює код обробника. Реєстр перевіряється при створенні: вид без
    репозиторію означав би, що документи цього виду мовчки губляться
    на шляху запису.
    """

    def __init__(
        self,
        repositories: Mapping[DocumentKind, CrudRepository],
        indexes: Mapping[DocumentKind, SearchIndex],
        graph: GraphRepository,
        metrics: MetricsSink,
        quality_log: DataQualityLog,
        validator: DocumentValidator,
        shard_keys: ShardKeyService,
        classifier: EnvironmentClassifier,
        vocabulary: VocabularyNormalizer,
    ) -> None:
        missing_repositories = set(DocumentKind) - set(repositories)
        if missing_repositories:
            raise ValueError(f"немає репозиторію для: {sorted(k.value for k in missing_repositories)}")
        missing_indexes = {kind for kind in DocumentKind if kind.is_indexed} - set(indexes)
        if missing_indexes:
            raise ValueError(f"немає індексу для: {sorted(k.value for k in missing_indexes)}")

        self._repositories = dict(repositories)
        self._indexes = dict(indexes)
        self._graph = graph
        self._metrics = metrics
        self._quality_log = quality_log
        self._validator = validator
        self._shard_keys = shard_keys
        self._classifier = classifier
        self._vocabulary = vocabulary

    async def handle(self, batch: list[dict], source_name: str) -> IngestReport:
        """Обробка пакета: групування за видом, валідація, трансформації,
        паралельний запис у сховище, індекс і граф.

        Запис виконується пакетами, а не поодинці: кількість звернень
        до сховища зменшується на два порядки.
        """
        raise NotImplementedError

    def _group_by_kind(self, batch: list[dict]) -> dict[DocumentKind, list[dict]]:
        raise NotImplementedError

    def _transform(self, kind: DocumentKind, document: dict) -> dict:
        """Приведення типів і похідні значення.

        Розбір складених рядків винесено в офлайн-підготовку CSV,
        тут лишаються ключ розподілу, класифікація середовища монстра
        та нормалізація за словниками.
        """
        raise NotImplementedError

    async def _persist(self, kind: DocumentKind, documents: list[dict]) -> tuple[int, int, int]:
        """Паралельний запис у три сховища. Повертає (збережено, проіндексовано, ребер)."""
        raise NotImplementedError

    async def _reject(self, document: dict, reasons: list[str]) -> None:
        """Журнал браку через порт якості даних."""
        raise NotImplementedError
