"""Автоматична перевірка правила залежностей (п. 3.7).

Імпорт із зовнішнього шару у внутрішній є помилкою архітектури,
і вона має виявлятися тестом, а не оглядом коду. Дозволені імпорти
відповідають таблицям 3.2 і 3.8 звіту.
"""

from __future__ import annotations

import ast
import pathlib
import sys

PROJECT = pathlib.Path(__file__).resolve().parents[2]
ROOT = PROJECT / "app"

#: Що кожному шару дозволено імпортувати
ALLOWED: dict[str, set[str]] = {
    "domain": {"domain"},
    "application": {"domain", "application"},
    "presentation": {"application", "presentation"},
    "infrastructure": {"domain", "application", "infrastructure"},
}

#: Процеси бачать порти й композиційний корінь, але не адаптери напряму
WORKERS_ALLOWED = ("app.domain", "app.application", "app.infrastructure.config", "workers")


def _layer_of(path: pathlib.Path) -> str | None:
    parts = path.relative_to(ROOT).parts
    return parts[0] if parts and parts[0] in ALLOWED else None


def _imported_modules(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def _imported_layers(path: pathlib.Path) -> set[str]:
    return {m.split(".")[1] for m in _imported_modules(path) if m.startswith("app.") and m.count(".") >= 1}


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


def test_workers_use_ports_and_composition_root_only() -> None:
    """Процес отримує адаптери лише через композиційний корінь."""
    violations: list[str] = []
    for path in (PROJECT / "workers").rglob("*.py"):
        for module in _imported_modules(path):
            if module.split(".")[0] in {"app", "workers"} and not module.startswith(WORKERS_ALLOWED):
                violations.append(f"{path.relative_to(PROJECT)}: {module}")
    assert not violations, "процес імпортує адаптер напряму:\n" + "\n".join(violations)


def test_domain_imports_no_third_party() -> None:
    """Доменний шар використовує лише стандартну бібліотеку (п. 3.2): стемер
    Портера, хеш ключа розподілу й нормалізація Unicode — її засобами."""
    allowed_roots = set(sys.stdlib_module_names) | {"app"}
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
