"""Адаптери джерел документів. Усі реалізують порт `DocumentSource`."""

from app.infrastructure.sources.crawler_source import CrawlerSource, PolitenessPolicy
from app.infrastructure.sources.csv_source import CsvSource
from app.infrastructure.sources.generator_source import (
    GeneratorSource,
    MarkovTextBuilder,
    ThemeProfile,
)

__all__ = [
    "CrawlerSource",
    "CsvSource",
    "GeneratorSource",
    "MarkovTextBuilder",
    "PolitenessPolicy",
    "ThemeProfile",
]
