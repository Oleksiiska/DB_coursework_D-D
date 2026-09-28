"""Помилки предметної області.

Не залежать ані від транспорту, ані від сховища: presentation-шар
перекладає їх у коди HTTP, infrastructure — у записи журналу.
"""

from __future__ import annotations


class DomainError(Exception):
    """Базова помилка предметної області."""


class ValidationError(DomainError):
    """Документ не задовольняє інваріанти сутності.

    Породжує запис у журналі браку (п. 2.11).
    """

    def __init__(self, entity: str, reasons: list[str]) -> None:
        self.entity = entity
        self.reasons = reasons
        super().__init__(f"{entity}: {'; '.join(reasons)}")


class EntityNotFound(DomainError):
    def __init__(self, entity: str, entity_id: str) -> None:
        self.entity = entity
        self.entity_id = entity_id
        super().__init__(f"{entity} {entity_id} не знайдено")


class SourceExhausted(DomainError):
    """Джерело документів вичерпано: CSV дочитано до кінця."""


class UnknownVocabularyTerm(DomainError):
    """Значення не знайдено в контрольованому словнику.

    Не відкидає документ: поле лишається порожнім, значення
    потрапляє в журнал нерозпізнаних (п. 2.11, правило 4).
    """

    def __init__(self, vocabulary: str, term: str) -> None:
        self.vocabulary = vocabulary
        self.term = term
        super().__init__(f"{vocabulary}: невідоме значення {term!r}")
