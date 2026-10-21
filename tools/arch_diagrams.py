#!/usr/bin/env python3
"""Побудова схем модулів і класів безпосередньо з вихідного коду.

Схеми не малюються вручну, а виводяться з коду, тому завжди
відповідають фактичному стану проєкту. На кожну ставиться дата.

Джерела зв'язків:
    імпорти між модулями        — синтаксичне дерево (ast);
    успадкування                — `__bases__` класів;
    реалізація портів           — структурна відповідність протоколу
                                  з перевіркою сигнатур методів;
    залежності через конструктор — анотації параметрів `__init__`;
    композиція                  — поля датакласів.

Використання:
    python tools/arch_diagrams.py --out docs/diagrams --plantuml plantuml.jar
    python tools/arch_diagrams.py --date 24.09.2026 --no-render
"""

from __future__ import annotations

import argparse
import ast
import dataclasses
import datetime as dt
import enum
import importlib
import inspect
import json
import re
import subprocess
import sys
import typing
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ("app", "workers")

LAYERS: dict[str, tuple[str, str]] = {
    "processes": ("процеси", "#FFF8E7"),
    "presentation": ("presentation — подання", "#F8F4EC"),
    "application": ("application — сценарії", "#F2EEF6"),
    "domain": ("domain — предметна область", "#EDF4F2"),
    "infrastructure": ("infrastructure — адаптери", "#F4F4F4"),
}

#: Заливка класів у представленнях без кластерів — насиченіша за тло пакетів,
#: щоб шари розрізнялися без рамок
FILLS = {
    "processes": "#FCE9B8",
    "presentation": "#F1E0C0",
    "application": "#E2D8F3",
    "domain": "#D2EADF",
    "infrastructure": "#E1E1E1",
}

STEREOTYPES = {
    "entity": "сутність",
    "part": "вкладене",
    "association_class": "асоціативний клас",
    "value": "значення",
    "enum": "перелік",
    "spec": "специфікація",
    "error": "помилка",
    "service": "служба",
    "outbound": "вихідний порт",
    "inbound": "вхідний порт",
    "dto": "DTO",
    "handler": "обробник",
    "adapter": "адаптер",
    "controller": "контролер",
    "process": "процес",
    "config": "конфігурація",
}

COMMON_SKIN = """\
skinparam backgroundColor white
skinparam shadowing false
skinparam dpi 150
skinparam defaultFontSize 12
skinparam classAttributeIconSize 0
skinparam packageStyle rectangle
skinparam ArrowColor #333333
skinparam HeaderFontColor #777777
skinparam HeaderFontSize 10
hide empty members
"""

GENERIC_CONTAINERS = {"list", "tuple", "set", "frozenset", "Sequence", "Iterable", "AsyncIterator"}
MAPPINGS = {"dict", "Mapping"}

# --------------------------------------------------------------------------
# Модель
# --------------------------------------------------------------------------


@dataclass
class ModuleInfo:
    name: str
    group: str
    imports: set[str] = field(default_factory=set)


@dataclass
class Relation:
    source: str
    target: str
    kind: str  # generalization | realization | dependency | composition | association | reference | use
    label: str = ""
    multiplicity: str = ""


@dataclass
class ClassInfo:
    name: str
    module: str
    group: str
    obj: type
    kind: str
    is_protocol: bool
    is_abstract: bool
    role: str = ""

    @property
    def layer(self) -> str:
        return self.group.split(".")[0]


# --------------------------------------------------------------------------
# Аналіз
# --------------------------------------------------------------------------


def group_of(module: str) -> str | None:
    """Модуль → вузол схеми модулів (пакет другого рівня)."""
    parts = module.split(".")
    if parts[0] == "workers":
        return f"processes.{parts[1]}" if len(parts) > 1 else None
    if parts[0] != "app" or len(parts) < 2:
        return None
    if parts[1] == "main":
        return "processes.api"
    if len(parts) == 2:
        return None
    return ".".join(parts[1:3])


def discover_modules() -> dict[str, ModuleInfo]:
    modules: dict[str, ModuleInfo] = {}
    for package in PACKAGES:
        for path in sorted((ROOT / package).rglob("*.py")):
            name = ".".join(path.relative_to(ROOT).with_suffix("").parts)
            if name.endswith(".__init__"):
                name = name[: -len(".__init__")]
            group = group_of(name)
            if group is None:
                continue
            info = ModuleInfo(name, group)
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] in PACKAGES:
                    info.imports.add(node.module)
                elif isinstance(node, ast.Import):
                    info.imports.update(a.name for a in node.names if a.name.split(".")[0] in PACKAGES)
            modules[name] = info
    return modules


def classify(cls: type, group: str) -> str:
    if getattr(cls, "_is_protocol", False):
        if group == "application.ports":
            return "inbound"
        if group == "domain.ports":
            return "outbound"
        return "spec"
    if issubclass(cls, enum.Enum):
        return "enum"
    if issubclass(cls, BaseException):
        return "error"
    by_group = {
        "domain.entities": "entity",
        "domain.values": "value",
        "domain.services": "service",
        "application.dto": "dto",
        "application.queries": "handler",
        "application.commands": "handler",
        "infrastructure.config": "config",
    }
    if group in by_group:
        return by_group[group]
    layer = group.split(".")[0]
    return {"infrastructure": "adapter", "presentation": "controller", "processes": "process"}.get(layer, "value")


def discover_classes(modules: dict[str, ModuleInfo]) -> dict[str, ClassInfo]:
    sys.path.insert(0, str(ROOT))
    classes: dict[str, ClassInfo] = {}
    for name, info in modules.items():
        module = importlib.import_module(name)
        for obj in vars(module).values():
            if not inspect.isclass(obj) or obj.__module__ != name or obj is typing.Protocol:
                continue
            if obj.__name__ in classes:
                raise SystemExit(f"неоднозначна назва класу: {obj.__name__}")
            classes[obj.__name__] = ClassInfo(
                name=obj.__name__,
                module=name,
                group=info.group,
                obj=obj,
                kind=classify(obj, info.group),
                is_protocol=bool(getattr(obj, "_is_protocol", False)),
                is_abstract=inspect.isabstract(obj),
            )
    return classes


def typevar_names(modules: dict[str, ModuleInfo]) -> set[str]:
    names: set[str] = set()
    for name in modules:
        for key, value in vars(sys.modules[name]).items():
            if isinstance(value, typing.TypeVar):
                names.add(key)
    return names


def referenced(annotation: str, classes: dict[str, ClassInfo]) -> list[str]:
    return [n for n in re.findall(r"[A-Za-z_]\w*", annotation) if n in classes]


def parse_annotation(annotation: str, classes: dict[str, ClassInfo]) -> tuple[list[str], str, str]:
    """Анотація → (цільові класи, кратність, підпис ребра)."""
    text = annotation.replace(" ", "")
    head = re.match(r"(\w+)\[", text)
    outer = head.group(1) if head else text.split("|")[0]
    names = referenced(text, classes)
    if outer in MAPPINGS and len(names) >= 2:
        return names[1:], "*", "реєстр"
    if outer in GENERIC_CONTAINERS:
        return names, "*", ""
    if outer in classes and len(names) > 1:
        return [outer], "", "[" + ", ".join(names[1:]) + "]"
    optional = "|None" in text or "None|" in text
    return names[:1], ("0..1" if optional else ""), ""


def normalize_type(annotation: object, tvars: set[str]) -> str | None:
    """Нормальна форма типу для порівняння сигнатур; None — будь-який тип."""
    text = (annotation if isinstance(annotation, str) else getattr(annotation, "__name__", str(annotation)))
    text = text.replace(" ", "").split("|")[0]
    text = re.sub(r"\[.*\]$", "", text)
    return None if text in tvars else text


def conforms(cls: type, protocol: type, tvars: set[str]) -> bool:
    """Структурна відповідність із перевіркою сигнатур.

    Сама по собі перевірка `runtime_checkable` порівнює лише назви
    членів, тому будь-який клас з методом `handle` відповідав би всім
    вхідним портам. Тут додатково порівнюються параметри й тип результату.
    """
    for attr in typing._get_protocol_attrs(protocol):  # noqa: SLF001
        expected = inspect.getattr_static(protocol, attr, None)
        actual = inspect.getattr_static(cls, attr, None)
        if actual is None:
            actual = getattr(cls, attr, None)
        if actual is None:
            return False
        if isinstance(expected, property) or not callable(expected):
            continue
        try:
            exp_sig = inspect.signature(expected)
            act_sig = inspect.signature(actual)
        except (TypeError, ValueError):
            return False
        if list(exp_sig.parameters) != list(act_sig.parameters):
            return False
        exp_ret = normalize_type(exp_sig.return_annotation, tvars)
        act_ret = normalize_type(act_sig.return_annotation, tvars)
        if exp_ret and act_ret and exp_ret != act_ret:
            return False
    return True


def project_bases(info: ClassInfo, classes: dict[str, ClassInfo]) -> list[str]:
    bases = []
    for base in getattr(info.obj, "__orig_bases__", info.obj.__bases__):
        origin = typing.get_origin(base) or base
        name = getattr(origin, "__name__", "")
        if name in classes and name != info.name:
            bases.append(name)
    return bases


def ancestors(name: str, classes: dict[str, ClassInfo]) -> set[str]:
    seen: set[str] = set()
    stack = [name]
    while stack:
        for base in project_bases(classes[stack.pop()], classes):
            if base not in seen:
                seen.add(base)
                stack.append(base)
    return seen


def analyse(classes: dict[str, ClassInfo], tvars: set[str]) -> list[Relation]:
    relations: list[Relation] = []
    protocols = [c for c in classes.values() if c.is_protocol]

    # Успадкування
    for info in classes.values():
        for base in project_bases(info, classes):
            relations.append(Relation(info.name, base, "generalization"))

    # Реалізація портів: лише найконкретніший протокол і лише там,
    # де його не реалізує вже предок класу
    realized: dict[str, set[str]] = {}
    for info in classes.values():
        if info.is_protocol:
            continue
        realized[info.name] = {p.name for p in protocols if conforms(info.obj, p.obj, tvars)}
    for name, protos in realized.items():
        inherited = set().union(*(realized.get(a, set()) for a in ancestors(name, classes)))
        for proto in protos - inherited:
            more_specific = any(proto in ancestors(other, classes) for other in protos if other != proto)
            if not more_specific:
                relations.append(Relation(name, proto, "realization"))

    # Залежності через конструктор. Кілька параметрів одного типу
    # (три екземпляри SearchUseCase у контролері) зводяться в одне ребро
    # з кратністю та назвами параметрів.
    for info in classes.values():
        if info.is_protocol or dataclasses.is_dataclass(info.obj) or "__init__" not in vars(info.obj):
            continue
        by_target: dict[tuple[str, str, str], list[str]] = defaultdict(list)
        for param in list(inspect.signature(info.obj.__init__).parameters.values())[1:]:
            if param.annotation is inspect.Parameter.empty:
                continue
            targets, mult, label = parse_annotation(str(param.annotation), classes)
            for target in targets:
                by_target[(target, mult, label)].append(param.name.lstrip("_"))
        for (target, mult, label), params in by_target.items():
            if len(params) > 1 and not mult and not label:
                mult, label = str(len(params)), "\\n".join(params)
            relations.append(Relation(info.name, target, "dependency", label, mult))

    # Композиція й асоціація для датакласів
    for info in classes.values():
        if not dataclasses.is_dataclass(info.obj) or info.kind in {"config"}:
            continue
        for f in dataclasses.fields(info.obj):
            targets, mult, _ = parse_annotation(str(f.type), classes)
            for target in targets:
                target_info = classes[target]
                kind = "composition" if dataclasses.is_dataclass(target_info.obj) else "association"
                relations.append(Relation(info.name, target, kind, "", mult or "1"))

    # Використання в сигнатурах методів (для деталізованих зрізів)
    for info in classes.values():
        for mname, member in vars(info.obj).items():
            if mname.startswith("__"):
                continue
            if isinstance(member, (staticmethod, classmethod)):
                member = member.__func__
            if not inspect.isfunction(member):
                continue
            sig = inspect.signature(member)
            annotations = [str(p.annotation) for p in sig.parameters.values() if p.annotation is not inspect.Parameter.empty]
            if sig.return_annotation is not inspect.Signature.empty:
                annotations.append(str(sig.return_annotation))
            for ann in annotations:
                for target in referenced(ann, classes):
                    if target != info.name:
                        relations.append(Relation(info.name, target, "use"))

    relations.extend(key_references(classes, relations))
    assign_roles(classes, relations)
    return dedupe(relations)


def key_references(classes: dict[str, ClassInfo], relations: list[Relation]) -> list[Relation]:
    """Посилання між сутностями за ключем: `author_ids`, `monster_id`, `tags`.

    Сутності зберігаються в різних колекціях і посилаються одна на одну
    ідентифікаторами, а не об'єктами. Саме ці посилання стають ребрами графа.
    """
    entities = [c for c in classes.values() if c.group == "domain.entities" and dataclasses.is_dataclass(c.obj)]
    parts = {r.target for r in relations if r.kind == "composition" and r.source in {e.name for e in entities}}
    owners: dict[str, set[str]] = defaultdict(set)
    for r in relations:
        if r.kind == "composition":
            owners[r.target].add(r.source)

    key_of: dict[str, str] = {}
    plural_of: dict[str, str] = {}
    for e in entities:
        if e.name in parts:
            continue
        first = dataclasses.fields(e.obj)[0].name
        if first.endswith("_id"):
            key_of[first] = e.name
        plural_of[e.name.lower() + "s"] = e.name

    result: list[Relation] = []
    for e in entities:
        own_key = dataclasses.fields(e.obj)[0].name
        for f in dataclasses.fields(e.obj):
            if f.name == own_key:
                continue
            base = f.name[:-1] if f.name.endswith("_ids") else f.name
            target = None
            for key, cls_name in key_of.items():
                if base == key or base.endswith("_" + key):
                    target = cls_name
                    break
            if target is None:
                target = plural_of.get(f.name)
            if target is None or target in owners.get(e.name, set()):
                continue
            annotation = str(f.type).replace(" ", "")
            many = f.name.endswith("_ids") or f.name in plural_of or annotation.startswith(("list[", "tuple["))
            optional = "None" in annotation
            mult = "*" if many else ("0..1" if optional else "1")
            result.append(Relation(e.name, target, "reference", f.name, mult))
    # Кілька посилань сутності на саму себе з однаковою кратністю
    # зводяться в одну петлю з кількома підписами
    merged: dict[tuple[str, str, str], Relation] = {}
    for r in result:
        key = (r.source, r.target, r.multiplicity)
        if r.source == r.target and key in merged:
            merged[key].label += "\\n" + r.label
        else:
            merged[key if r.source == r.target else (r.source, r.target, r.label)] = r
    return list(merged.values())


def assign_roles(classes: dict[str, ClassInfo], relations: list[Relation]) -> None:
    """Уточнення стереотипів сутностей за структурою коду.

    Частина, вкладена в іншу сутність, — «вкладене»; вкладена частина,
    що ще й посилається на іншу сутність, — асоціативний клас. Так само
    асоціативним класом є сутність без власного ключа, що посилається на
    дві інші: вона існує лише як дані на зв'язку між ними.
    """
    composed = {r.target for r in relations if r.kind == "composition" and classes[r.source].group == "domain.entities"}
    referencing = {r.source for r in relations if r.kind == "reference"}
    targets: dict[str, set[str]] = defaultdict(set)
    for r in relations:
        if r.kind == "reference":
            targets[r.source].add(r.target)
    for info in classes.values():
        if info.group != "domain.entities":
            continue
        if info.name in composed:
            info.role = "association_class" if info.name in referencing else "part"
        elif dataclasses.is_dataclass(info.obj):
            # Без власного ключа й з посиланнями на дві сутності —
            # асоціативний клас на зв'язку між ними (участь монстра в зустрічі)
            first = dataclasses.fields(info.obj)[0].name
            if not first.endswith("_id") and len(targets[info.name]) >= 2:
                info.role = "association_class"


def dedupe(relations: list[Relation]) -> list[Relation]:
    seen: set[tuple] = set()
    result = []
    for r in relations:
        key = (r.source, r.target, r.kind, r.label)
        if key not in seen:
            seen.add(key)
            result.append(r)
    return result


# --------------------------------------------------------------------------
# Вивід PlantUML
# --------------------------------------------------------------------------


def header(date: str) -> str:
    return f"header\nстаном на {date} · побудовано з вихідного коду\nendheader\n"


def class_keyword(info: ClassInfo) -> str:
    if info.is_protocol:
        return "interface"
    if info.kind == "enum":
        return "enum"
    if info.is_abstract:
        return "abstract class"
    return "class"


def short_type(annotation: object) -> str:
    text = annotation if isinstance(annotation, str) else getattr(annotation, "__name__", str(annotation))
    return text.replace("typing.", "").replace("'", "").replace('"', "")


def members(info: ClassInfo, compact: bool, protected: bool = True) -> list[str]:
    """Поля й методи класу. У компактному режимі — без типів параметрів."""
    lines: list[str] = []
    obj = info.obj
    if dataclasses.is_dataclass(obj):
        for f in dataclasses.fields(obj):
            lines.append(f"+{f.name}: {short_type(f.type)}")
    elif "__init__" in vars(obj) and not info.is_protocol:
        for p in list(inspect.signature(obj.__init__).parameters.values())[1:]:
            if p.annotation is not inspect.Parameter.empty:
                lines.append(f"-{p.name.lstrip('_')}: {short_type(p.annotation)}")
    methods = []
    for mname, member in vars(obj).items():
        if mname.startswith("__"):
            continue
        if isinstance(member, (staticmethod, classmethod)):
            member = member.__func__
        if isinstance(member, property):
            methods.append(f"+{mname}")
            continue
        if not inspect.isfunction(member):
            continue
        if dataclasses.is_dataclass(obj) and mname.startswith("_"):
            continue
        is_abstract = getattr(member, "__isabstractmethod__", False)
        if mname.startswith("_") and not protected and not is_abstract:
            continue
        visibility = "#" if mname.startswith("_") else "+"
        sig = inspect.signature(member)
        params = [p for p in sig.parameters.values() if p.name not in {"self", "cls"}]
        if compact:
            rendered = ", ".join(p.name for p in params)
        else:
            rendered = ", ".join(
                f"{p.name}: {short_type(p.annotation)}" if p.annotation is not inspect.Parameter.empty else p.name
                for p in params
            )
        ret = ""
        if sig.return_annotation not in (inspect.Signature.empty, None, "None"):
            ret = f": {short_type(sig.return_annotation)}"
        prefix = "{abstract} " if getattr(member, "__isabstractmethod__", False) else ""
        methods.append(f"{prefix}{visibility}{mname}({rendered}){ret}")
    if methods:
        if lines:
            lines.append("--")
        lines.extend(methods)
    return lines


def render_class(info: ClassInfo, show_members: bool, compact: bool, colour: str = "", protected: bool = True) -> str:
    stereotype = STEREOTYPES.get(info.role or info.kind, info.kind)
    head = f"{class_keyword(info)} {info.name} <<{stereotype}>>" + (f" {colour}" if colour else "")
    if not show_members:
        return head
    body = members(info, compact, protected)
    if not body:
        return head
    return head + " {\n" + "\n".join("  " + line for line in body) + "\n}"


def render_relation(r: Relation) -> str:
    """Кожне ребро орієнтоване за потоком залежностей: у розкладці
    абстракція опиняється вище (лівіше) за свою реалізацію."""
    mult = f'"{r.multiplicity}" ' if r.multiplicity else ""
    label = f" : {r.label}" if r.label else ""
    if r.kind == "realization":
        return f"{r.target} <|.. {r.source}"
    if r.kind == "generalization":
        return f"{r.target} <|-- {r.source}"
    if r.kind == "composition":
        return f"{r.source} *-- {mult}{r.target}{label}"
    if r.kind in {"association", "reference"}:
        return f"{r.source} --> {mult}{r.target}{label}"
    if r.kind == "use":
        return f"{r.source} ..> {r.target}"
    return f"{r.source} --> {mult}{r.target}{label}"


def packages_block(
    names: set[str], classes: dict[str, ClassInfo], with_members: set[str], compact: bool,
    clusters: bool = True, protected: bool = True,
) -> str:
    by_layer: dict[str, list[ClassInfo]] = defaultdict(list)
    for n in names:
        by_layer[classes[n].layer].append(classes[n])
    out = []
    if not clusters:
        for layer, (_, colour) in LAYERS.items():
            for info in sorted(by_layer.get(layer, []), key=lambda c: (c.group, c.name)):
                out.append(render_class(info, info.name in with_members, compact, FILLS[layer], protected))
        return "\n".join(out)
    for layer, (label, colour) in LAYERS.items():
        if layer not in by_layer:
            continue
        out.append(f'package "{label}" {colour} {{')
        for info in sorted(by_layer[layer], key=lambda c: (c.group, c.name)):
            out.append(render_class(info, info.name in with_members, compact, "", protected))
        out.append("}")
    return "\n".join(out)


def class_view(
    name: str,
    date: str,
    nodes: set[str],
    relations: list[Relation],
    classes: dict[str, ClassInfo],
    kinds: set[str],
    with_members: set[str] = frozenset(),
    compact: bool = False,
    direction: str = "",
    exclude: set[tuple[str, str, str]] = frozenset(),
    legend: str = "",
    clusters: bool = True,
    protected: bool = True,
) -> str:
    rels = [
        r for r in relations
        if r.source in nodes and r.target in nodes and r.kind in kinds
        and (r.kind, r.source, r.target) not in exclude
    ]
    strong = {(r.source, r.target) for r in rels if r.kind != "use"}
    rels = [r for r in rels if r.kind != "use" or (r.source, r.target) not in strong]
    parts = [f"@startuml {name}", COMMON_SKIN, header(date)]
    if direction:
        parts.append(direction)
    parts.append(packages_block(nodes, classes, set(with_members), compact, clusters, protected))
    if not clusters:
        present = [layer for layer in LAYERS if any(classes[n].layer == layer for n in nodes)]
        legend = "\n".join(f"<back:{FILLS[l]}>      </back>  {LAYERS[l][0]}" for l in present) + ("\n" + legend if legend else "")
    order = {"realization": 0, "generalization": 1, "dependency": 2, "composition": 3, "association": 4, "reference": 5, "use": 6}
    parts.extend(render_relation(r) for r in sorted(rels, key=lambda r: (order[r.kind], r.source, r.target)))
    if legend:
        parts.append(f"legend right\n{legend}\nendlegend")
    parts.append("@enduml\n")
    return "\n".join(parts)


def module_view(date: str, modules: dict[str, ModuleInfo], classes: dict[str, ClassInfo], relations: list[Relation]) -> str:
    groups = sorted({m.group for m in modules.values()})
    imports: set[tuple[str, str]] = set()
    for m in modules.values():
        for imported in m.imports:
            target = group_of(imported)
            if target and target != m.group:
                imports.add((m.group, target))
    realizes: set[tuple[str, str]] = set()
    for r in relations:
        if r.kind == "realization":
            a, b = classes[r.source].group, classes[r.target].group
            if a != b and (a, b) not in imports:
                realizes.add((a, b))

    def node_id(g: str) -> str:
        return "m_" + g.replace(".", "_")

    lines = ["@startuml 01-modules", COMMON_SKIN, header(date), "skinparam componentStyle rectangle"]
    by_layer: dict[str, list[str]] = defaultdict(list)
    for g in groups:
        by_layer[g.split(".")[0]].append(g)
    for layer, (label, colour) in LAYERS.items():
        if layer not in by_layer:
            continue
        lines.append(f'package "{label}" {colour} {{')
        for g in by_layer[layer]:
            lines.append(f'  component "{g.split(".", 1)[1]}" as {node_id(g)}')
        lines.append("}")
    # Ребро записується так, щоб у розкладці ціль опинилася вище за джерело
    # там, де залежність спрямована «вгору»: інфраструктура стоїть під доменом
    # (шар 4 на рисунку 3.1), а реалізація порту — під самим портом.
    for a, b in sorted(imports):
        if a.startswith("infrastructure."):
            lines.append(f"{node_id(b)} <-- {node_id(a)}")
        else:
            lines.append(f"{node_id(a)} --> {node_id(b)}")
    for a, b in sorted(realizes):
        lines.append(f"{node_id(b)} <|.. {node_id(a)}")
    # Невидимі ребра задають порядок шарів згори вниз: процеси → подання →
    # сценарії → домен; інфраструктура — під доменом.
    chain = [by_layer[layer][0] for layer in ("processes", "presentation", "application", "domain") if by_layer.get(layer)]
    for upper, lower in zip(chain, chain[1:]):
        lines.append(f"{node_id(upper)} -[hidden]down-> {node_id(lower)}")
    lines.append("legend right\n  ──>  імпорт модуля\n  ‥‥▷  реалізація портів (без імпорту)\nendlegend")
    lines.append("@enduml\n")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Представлення
# --------------------------------------------------------------------------


def build_views(date: str, modules, classes, relations) -> dict[str, str]:
    def names(pred) -> set[str]:
        return {c.name for c in classes.values() if pred(c)}

    def targets(sources: set[str], kind: str) -> set[str]:
        return {r.target for r in relations if r.kind == kind and r.source in sources}

    def sources_of(targets_: set[str], kind: str) -> set[str]:
        return {r.source for r in relations if r.kind == kind and r.target in targets_}

    views: dict[str, str] = {"01-modules": module_view(date, modules, classes, relations)}

    # 02 — доменна модель: сутності, їх частини, значення й посилання за ключем
    domain_kinds = {"composition", "association", "reference"}
    nodes = names(lambda c: c.group == "domain.entities")
    while True:
        grown = nodes | {t for t in targets(nodes, "composition") | targets(nodes, "association") if classes[t].group.startswith("domain")}
        if grown == nodes:
            break
        nodes = grown
    views["02-domain-model"] = class_view(
        "02-domain-model", date, nodes, relations, classes, domain_kinds,
        direction="left to right direction",
        legend="  ◆──  композиція (вкладений об'єкт)\n  ──>  посилання за ключем (підпис — назва поля) або тип-перелік",
    )

    # 03 — вихідні порти та адаптери, що їх реалізують
    outbound = names(lambda c: c.kind == "outbound")
    implementers = sources_of(outbound, "realization")
    implementers |= {r.source for r in relations if r.kind == "generalization" and r.target in implementers}
    views["03-ports-adapters"] = class_view(
        "03-ports-adapters", date, outbound | implementers, relations, classes,
        {"realization", "generalization"}, direction="left to right direction", clusters=False,
    )

    # 04 — шлях запитів: контролери → вхідні порти ← обробники → вихідні порти
    query_handlers = names(lambda c: c.group == "application.queries")
    query_ports = targets(query_handlers, "realization")
    controllers = {r.source for r in relations if r.kind == "dependency" and r.target in query_ports}
    driven = {t for t in targets(query_handlers, "dependency") if classes[t].kind in {"outbound", "service"}}
    views["04-query-path"] = class_view(
        "04-query-path", date, query_handlers | query_ports | controllers | driven, relations, classes,
        {"realization", "generalization", "dependency"}, direction="left to right direction",
        exclude={("generalization", a, b) for a in driven for b in driven}, clusters=False,
    )

    # 05 — шлях команд: процеси й контролер → вхідні порти ← обробники → вихідні порти
    command_handlers = names(lambda c: c.group == "application.commands")
    command_ports = targets(command_handlers, "realization")
    drivers = {
        r.source for r in relations
        if r.kind == "dependency" and classes[r.source].kind in {"controller", "process"}
        and (r.target in command_ports or (classes[r.source].kind == "process" and classes[r.target].kind == "outbound"))
    }
    driven = {t for t in targets(command_handlers | drivers, "dependency") if classes[t].kind in {"outbound", "service"}}
    views["05-command-path"] = class_view(
        "05-command-path", date, command_handlers | command_ports | drivers | driven, relations, classes,
        {"realization", "dependency"}, direction="left to right direction", clusters=False,
    )

    # 06 — деталізований зріз «пошук пригод»
    slice_nodes = {
        "SearchController", "SearchUseCase", "CachedSearchHandler", "SearchAdventuresHandler",
        "SearchQueryDTO", "SearchResultDTO", "AdventureCardDTO", "QueryNormalizer",
        "SearchIndex", "CrudRepository", "CacheStore", "MetricsSink",
        "SearchIndexAdapter", "MongoCrudRepository", "RedisCacheAdapter", "RedisMetricsAdapter",
    } & set(classes)
    # Контролер — без членів: його три поля показані підписом ребра
    with_members = {
        "SearchUseCase", "CachedSearchHandler", "SearchAdventuresHandler",
        "SearchQueryDTO", "SearchResultDTO", "QueryNormalizer",
        "SearchIndex", "CrudRepository", "CacheStore", "MetricsSink",
    }
    views["06-search-slice"] = class_view(
        "06-search-slice", date, slice_nodes, relations, classes,
        {"realization", "generalization", "dependency", "use"}, with_members=with_members, compact=True,
        direction="left to right direction", clusters=False, protected=False,
    )
    return views


# --------------------------------------------------------------------------
# Запуск
# --------------------------------------------------------------------------


def render(puml: Path, plantuml: Path) -> None:
    for fmt in ("png", "svg"):
        subprocess.run(
            ["java", "-DPLANTUML_LIMIT_SIZE=16384", "-jar", str(plantuml), f"-t{fmt}", "-charset", "UTF-8", str(puml)],
            check=True, capture_output=True,
        )


def summary(modules, classes, relations) -> dict:
    counts: dict[str, int] = defaultdict(int)
    for r in relations:
        counts[r.kind] += 1
    kinds: dict[str, int] = defaultdict(int)
    for c in classes.values():
        kinds[c.kind] += 1
    return {
        "modules": len(modules),
        "groups": len({m.group for m in modules.values()}),
        "classes": len(classes),
        "classes_by_kind": dict(sorted(kinds.items())),
        "relations_by_kind": dict(sorted(counts.items())),
        "realizations": sorted(f"{r.source} ..|> {r.target}" for r in relations if r.kind == "realization"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "diagrams")
    parser.add_argument("--plantuml", type=Path, default=ROOT / "plantuml.jar")
    parser.add_argument("--date", default=dt.date.today().strftime("%d.%m.%Y"))
    parser.add_argument("--no-render", action="store_true")
    args = parser.parse_args()

    modules = discover_modules()
    classes = discover_classes(modules)
    relations = analyse(classes, typevar_names(modules))
    views = build_views(args.date, modules, classes, relations)

    args.out.mkdir(parents=True, exist_ok=True)
    for name, text in views.items():
        path = args.out / f"{name}.puml"
        path.write_text(text, encoding="utf-8")
        if not args.no_render:
            render(path, args.plantuml)
    info = summary(modules, classes, relations)
    info["date"] = args.date
    (args.out / "summary.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in info.items() if k != "realizations"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
