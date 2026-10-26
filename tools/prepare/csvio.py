"""Запис підготовлених CSV у форматі, який читає CsvSource.

Формат описано в README («Формат підготовлених CSV»): назви колонок —
назви полів моделі, порожня клітинка — відсутнє значення, переліки й
вкладені об'єкти — JSON, час — ISO 8601. Файл пишеться під тимчасовою
назвою й перейменовується лише після успішного завершення, тож перерваний
запуск не залишає напівзаписаного CSV, який csv-loader прийняв би за готовий.
"""

from __future__ import annotations

import csv
import dataclasses
import enum
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


def cell(value: Any) -> str:
    """Значення → текст клітинки."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, enum.Enum):
        return cell(value.value)
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return moment.isoformat()
    if isinstance(value, str):
        return value.replace("\x00", "")  # нульовий символ у тексті чату — сміття, а не дані
    if isinstance(value, (int, float)):
        return str(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        value = dataclasses.asdict(value)
    if isinstance(value, (list, tuple, set, frozenset, dict)):
        return json.dumps(_clean(value), ensure_ascii=False, separators=(",", ":"))
    raise TypeError(f"значення типу {type(value).__name__} не має подання в CSV")


def _clean(value: Any) -> Any:
    """Вкладені значення без нульових символів; множини — списками."""
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_clean(v) for v in value]
    return value


class CsvTable:
    """Один CSV-файл таблиці 2.3 з фіксованим набором колонок.

    Ключ рядка поза набором колонок — помилка: інакше хибно названа колонка
    мовчки зникла б із файлу, а поле в документі лишилося б порожнім.
    """

    def __init__(self, path: Path, columns: Sequence[str]) -> None:
        self.path = Path(path)
        self.columns = list(columns)
        self.rows = 0
        self._part = self.path.with_name(self.path.name + ".part")
        self._stream: Any = None
        self._writer: Any = None

    def __enter__(self) -> "CsvTable":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # errors=replace: поодинокий сурогат (зіпсований емодзі в повідомленні чату)
        # стає «?», а не зупиняє запис усього файлу
        self._stream = self._part.open("w", encoding="utf-8", errors="replace", newline="")
        self._writer = csv.writer(self._stream)
        self._writer.writerow(self.columns)
        return self

    def write(self, row: Mapping[str, Any]) -> None:
        unknown = set(row) - set(self.columns)
        if unknown:
            raise KeyError(f"{self.path.name}: колонок {sorted(unknown)} немає в переліку")
        self._writer.writerow([cell(row.get(name)) for name in self.columns])
        self.rows += 1

    def write_all(self, rows: Iterable[Mapping[str, Any]]) -> None:
        for row in rows:
            self.write(row)

    def __exit__(self, kind: Any, error: Any, traceback: Any) -> None:
        self._stream.close()
        if kind is None:
            os.replace(self._part, self.path)
        else:
            self._part.unlink(missing_ok=True)


def read_rows(path: Path) -> Iterable[dict[str, str]]:
    """Рядки готового CSV — так само, як їх читає CsvSource."""
    import sys

    csv.field_size_limit(sys.maxsize)
    with Path(path).open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            yield {key: value for key, value in row.items() if key is not None}
