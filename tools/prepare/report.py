"""Звіт вимірювань: числа, на які спираються розділ 2 і додаток А.

Кожен вимір прив'язано до місця у звіті (таблиця чи підрозділ) і
поставлено поруч зі значенням, наведеним у версії звіту v4, тож після
підготовки даних видно, які числа збіглися, а які слід оновити. Результат —
``measurements.md`` для читання і ``measurements.json`` для подальшої обробки.
"""

from __future__ import annotations

import json
import math
import platform
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

NBSP = "\u00a0"


def number(value: float | int | None, digits: int = 1) -> str:
    """Число в записі звіту: пробіл між тисячами, кома в дробовій частині."""
    if value is None:
        return "—"
    if isinstance(value, float) and not value.is_integer():
        text = f"{value:,.{digits}f}"
    else:
        text = f"{int(round(value)):,}"
    return text.replace(",", NBSP).replace(".", ",")


def percent(part: int, total: int, digits: int = 1) -> str:
    """Частка у відсотках з одним знаком після коми; нульовий дробовий знак не пишеться."""
    if not total:
        return "—"
    text = f"{100 * part / total:.{digits}f}".replace(".", ",")
    if digits and text.endswith("," + "0" * digits):
        text = text[: -(digits + 1)]
    return text + "%"


def share(part: int, total: int, digits: int = 1) -> str:
    """«12,5% (6 092 з 48 895)»."""
    return f"{percent(part, total, digits)} ({number(part)} з {number(total)})"


def top(counter: Counter | dict, limit: int = 10) -> str:
    items = counter.most_common(limit) if isinstance(counter, Counter) else list(counter.items())[:limit]
    return ", ".join(f"{name} ({number(count)})" for name, count in items) or "—"


@dataclass(slots=True)
class Distribution:
    """Медіана, середнє й максимум (таблиця 2.11)."""

    values: list[float] = field(default_factory=list)

    def add(self, value: float | None) -> None:
        if value is not None:
            self.values.append(value)

    @property
    def count(self) -> int:
        return len(self.values)

    def median(self) -> float | None:
        if not self.values:
            return None
        ordered = sorted(self.values)
        middle = len(ordered) // 2
        return float(ordered[middle]) if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2

    def mean(self) -> float | None:
        return sum(self.values) / len(self.values) if self.values else None

    def maximum(self) -> float | None:
        return max(self.values) if self.values else None

    def percentile(self, p: float) -> float | None:
        """Перцентиль методом найближчого рангу."""
        if not self.values:
            return None
        ordered = sorted(self.values)
        return float(ordered[max(0, math.ceil(p / 100 * len(ordered)) - 1)])

    def describe(self, digits: int = 1) -> str:
        return (f"медіана {number(self.median(), digits)}, середнє {number(self.mean(), digits)}, "
                f"максимум {number(self.maximum(), digits)} (n = {number(self.count)})")


@dataclass(slots=True)
class Measure:
    where: str
    what: str
    value: str
    reported: str | None = None
    raw: Any = None


@dataclass(slots=True)
class Section:
    title: str
    measures: list[Measure] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class Measurements:
    """Накопичення вимірів за кроками підготовки (таблиця 2.4)."""

    def __init__(self) -> None:
        self.started_at = datetime.now(timezone.utc)
        self.sections: dict[str, Section] = {}
        #: Знімки джерел (підрозділ 2.9): коміт, архів, дата обходу
        self.snapshots: dict[str, str] = {}
        self.steps: dict[str, str] = {}

    def section(self, key: str, title: str) -> Section:
        if key not in self.sections:
            self.sections[key] = Section(title)
        return self.sections[key]

    def add(self, key: str, where: str, what: str, value: str, reported: str | None = None, raw: Any = None) -> None:
        self.sections[key].measures.append(Measure(where, what, value, reported, raw))

    def note(self, key: str, text: str) -> None:
        self.sections[key].notes.append(text)

    def step(self, name: str, status: str) -> None:
        self.steps[name] = status

    # --- подання ---

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "python": platform.python_version(),
            "steps": self.steps,
            "snapshots": self.snapshots,
            "sections": {key: asdict(section) for key, section in self.sections.items()},
        }

    def render(self) -> str:
        lines = [
            "# Виміри підготовлених даних",
            "",
            f"Підготовку виконано {self.started_at:%d.%m.%Y %H:%M} UTC. Колонка «Звіт v4» — значення, "
            "наведене у версії звіту від 22.10.2026; порожня — у звіті числа немає або там стоїть TODO.",
            "",
            "## Кроки",
            "",
            "| Крок | Результат |",
            "|---|---|",
        ]
        lines += [f"| {name} | {status} |" for name, status in self.steps.items()]
        if self.snapshots:
            lines += ["", "## Знімки джерел (підрозділ 2.9)", "", "| Джерело | Знімок |", "|---|---|"]
            lines += [f"| {name} | {value} |" for name, value in self.snapshots.items()]
        for section in self.sections.values():
            lines += ["", f"## {section.title}", ""]
            if section.measures:
                lines += ["| Де у звіті | Показник | Виміряно | Звіт v4 |", "|---|---|---|---|"]
                for m in section.measures:
                    lines.append(f"| {_md(m.where)} | {_md(m.what)} | {_md(m.value)} | {_md(m.reported or '')} |")
            for text in section.notes:
                lines += ["", text]
        return "\n".join(lines) + "\n"

    def save(self, directory: Path) -> tuple[Path, Path]:
        directory.mkdir(parents=True, exist_ok=True)
        markdown = directory / "measurements.md"
        data = directory / "measurements.json"
        markdown.write_text(self.render(), encoding="utf-8")
        data.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return markdown, data


def _md(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def counts_line(pairs: Iterable[tuple[str, int]]) -> str:
    return ", ".join(f"{name} ({number(count)})" for name, count in pairs) or "—"
