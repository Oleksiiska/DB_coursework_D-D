"""Генератор схем працює на поточному коді, а кожен порт має реалізацію.

Перевірка спирається на той самий аналіз, з якого будуються схеми
звіту, тому схема й тест не можуть розійтися.
"""

from __future__ import annotations

import json

import pytest

from tools import arch_diagrams as g

DIAGRAMS = g.ROOT / "docs" / "diagrams"


@pytest.fixture(scope="module")
def model():
    modules = g.discover_modules()
    classes = g.discover_classes(modules)
    relations = g.analyse(classes, g.typevar_names(modules))
    return modules, classes, relations


def _implemented(classes, relations, kind: str, implementer_layer: str) -> dict[str, set[str]]:
    ports = {c.name for c in classes.values() if c.kind == kind}
    found: dict[str, set[str]] = {p: set() for p in ports}
    for r in relations:
        if r.kind == "realization" and r.target in ports and classes[r.source].layer == implementer_layer:
            found[r.target].add(r.source)
    return found


def test_every_outbound_port_has_an_adapter(model) -> None:
    _, classes, relations = model
    found = _implemented(classes, relations, "outbound", "infrastructure")
    # спеціалізовані репозиторії реалізує нащадок узагальненого адаптера
    missing = [p for p, impls in found.items() if not impls]
    assert not missing, f"порти без адаптера: {missing}"


def test_every_inbound_port_has_exactly_one_handler(model) -> None:
    _, classes, relations = model
    found = _implemented(classes, relations, "inbound", "application")
    wrong = {p: sorted(i) for p, i in found.items() if len(i) != 1}
    assert not wrong, f"неоднозначна або відсутня реалізація: {wrong}"


def test_views_build(model) -> None:
    modules, classes, relations = model
    views = g.build_views("01.01.2026", modules, classes, relations)
    assert set(views) == {
        "01-modules", "02-domain-model", "03-ports-adapters",
        "04-query-path", "05-command-path", "06-search-slice",
    }
    assert all("станом на 01.01.2026" in text for text in views.values())


def test_committed_diagrams_match_code(model) -> None:
    """Збережені схеми збігаються з побудованими з поточного коду.

    Зміна коду без повторного запуску `tools/arch_diagrams.py` ламає
    тест, тому схема в звіті не може непомітно застаріти.
    """
    summary = DIAGRAMS / "summary.json"
    if not summary.exists():
        pytest.skip("схеми ще не побудовано")
    modules, classes, relations = model
    date = json.loads(summary.read_text(encoding="utf-8"))["date"]
    stale = [
        name for name, text in g.build_views(date, modules, classes, relations).items()
        if (DIAGRAMS / f"{name}.puml").read_text(encoding="utf-8") != text
    ]
    assert not stale, f"схеми застаріли, перебудуйте: {stale}"
