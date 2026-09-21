"""Обробники команд — шлях запису CQRS."""

from app.application.commands.configure_rate import ConfigureRateHandler
from app.application.commands.ingest_document import IngestDocumentHandler

__all__ = ["ConfigureRateHandler", "IngestDocumentHandler"]
