"""Автоматична перевірка правила залежностей (п. 4.7).

Імпорт із зовнішнього шару у внутрішній є помилкою архітектури,
і вона має виявлятися тестом, а не оглядом коду.
"""

from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2] / "app"

#: Що кожному шару дозволено імпортувати
ALLOWED: dict[str, set[str]] = {
    "domain": {"domain"},
    "application": {"domain", "application"},
    "presentation": {"domain", "application", "presentation"},
    "infrastructure": {"domain", "application", "infrastructure"},
}


def _layer_of(path: pathlib.Path) -> str | None:
    parts = path.relative_to(ROOT).parts
    return parts[0] if parts and parts[0] in ALLOWED else None


def _imported_layers(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    layers: set[str] = set()
    for node in ast.walk(tree):
        module = None
        if isinstance(node, ast.ImportFrom) and node.module:
            module = node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("app."):
                    layers.add(alias.name.split(".")[1])
            continue
        if module and module.startswith("app."):
            layers.add(module.split(".")[1])
    return layers


def test_dependency_rule_holds() -> None:
    violations: list[str] = []
    for path in ROOT.rglob("*.py"):
        layer = _layer_of(path)
        if layer is None:
            continue
        for imported in _imported_layers(path):
            if imported not in ALLOWED[layer]:
                violations.append(f"{path.relative_to(ROOT)}: {layer} -> {imported}")
    assert not violations, "порушення правила залежностей:\n" + "\n".join(violations)


def test_domain_imports_no_third_party() -> None:
    """Доменний шар використовує лише стандартну бібліотеку."""
    allowed_roots = {"app", "typing", "dataclasses", "enum", "datetime", "abc", "__future__"}
    violations: list[str] = []
    for path in (ROOT / "domain").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            for name in names:
                root = name.split(".")[0]
                if root not in allowed_roots:
                    violations.append(f"{path.relative_to(ROOT)}: {name}")
    assert not violations, "домен залежить від зовнішнього коду:\n" + "\n".join(violations)
