"""Асинхронний контракт портів (підрозділ 3.5).

Одне синхронне звернення всередині асинхронного ланцюга зупиняє весь
цикл подій. Тому кожен метод порту оголошено співпрограмою, а кожна
реалізація — адаптер чи обробник — зобов'язана зберегти це. Перевірка
спирається на ту саму модель коду, з якої будуються схеми розділу 3.
"""

from __future__ import annotations

import inspect
import typing

import pytest

from tools import arch_diagrams as g

PORT_KINDS = {"inbound", "outbound"}


def _is_async(member: object) -> bool:
    return inspect.iscoroutinefunction(member) or inspect.isasyncgenfunction(member)


def _port_methods(port: type) -> list[str]:
    """Методи протоколу; властивості (наприклад, `name` джерела) не перевіряються."""
    names = []
    for attr in sorted(typing._get_protocol_attrs(port)):  # noqa: SLF001
        member = inspect.getattr_static(port, attr)
        if not isinstance(member, property) and callable(member):
            names.append(attr)
    return names


@pytest.fixture(scope="module")
def model():
    modules = g.discover_modules()
    classes = g.discover_classes(modules)
    relations = g.analyse(classes, g.typevar_names(modules))
    return classes, relations


def test_every_port_method_is_a_coroutine(model) -> None:
    classes, _ = model
    blocking = [
        f"{c.name}.{m}"
        for c in classes.values() if c.kind in PORT_KINDS
        for m in _port_methods(c.obj)
        if not _is_async(inspect.getattr_static(c.obj, m))
    ]
    assert not blocking, f"синхронні методи портів: {blocking}"


def test_implementations_keep_methods_async(model) -> None:
    classes, relations = model
    blocking = []
    for r in relations:
        port, impl = classes[r.target], classes[r.source]
        if r.kind != "realization" or port.kind not in PORT_KINDS:
            continue
        for m in _port_methods(port.obj):
            if not _is_async(getattr(impl.obj, m)):
                blocking.append(f"{impl.name}.{m}")
    assert not blocking, f"синхронна реалізація асинхронного порту: {blocking}"
