"""Lore self-check and the compiled ``Guard``.

``compile()`` is where broken lore fails loudly (Pydantic-style, at
import/startup time): unknown relation targets, unresolvable forward refs,
impossible disjointness, empty ``one_of``, nonsensical cardinalities. What
survives is a ``Guard``: an immutable bundle of class registry, relation/attr
specs, disjoint pairs, and rules that sessions and checks read from.
"""

from __future__ import annotations

import inspect
import types
import typing
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, Any, Callable, Iterable, Union

from pydantic.fields import FieldInfo

from .schema import Entity, Lore, LoreError, Severity, _RawRule, _RelTarget

if TYPE_CHECKING:
    from .session import Session


@dataclass(frozen=True)
class RelationSpec:
    predicate: str  # "DefiningClass.field_name"
    owner: str  # class that defines the field (base-most registered declarer)
    field: str
    target: str  # registered target class name (after forward-ref resolution)
    max_per_target: int | None
    severity: Severity


@dataclass(frozen=True)
class AttrSpec:
    attr: str  # "DefiningClass.field_name"
    owner: str
    field: str
    one_of: tuple[Any, ...] | None
    severity: Severity


@dataclass(frozen=True)
class RuleSpec:
    name: str
    fn: Callable[..., Any]
    message: str
    severity: Severity
    target: str  # registered class name; subclass instances fire the rule too


@dataclass(frozen=True)
class CompiledClass:
    cls: type[Entity]
    ancestors: tuple[str, ...]  # registered ancestor names in MRO order, self first
    relations: dict[str, RelationSpec] = field(default_factory=dict)  # by field name
    attributes: dict[str, AttrSpec] = field(default_factory=dict)  # by field name


class Guard:
    """A compiled, validated lore. Create sessions from it."""

    def __init__(
        self,
        name: str,
        classes: dict[str, CompiledClass],
        relations: dict[str, RelationSpec],
        attributes: dict[str, AttrSpec],
        disjoint_pairs: tuple[tuple[str, str], ...],
        rules: tuple[RuleSpec, ...],
    ) -> None:
        self.name = name
        self.classes = classes
        self.relations = relations  # by predicate
        self.attributes = attributes  # by attr name
        self.disjoint_pairs = disjoint_pairs
        self.rules = rules

    def session(self, seed: Iterable[Entity] = ()) -> "Session":
        """Open a session, grounding ``seed`` instances as trusted facts."""
        from .session import Session

        return Session(self, seed=seed)


def _lore_extra(info: FieldInfo) -> dict[str, Any]:
    extra = info.json_schema_extra
    if isinstance(extra, dict):
        lore = extra.get("lore")
        if isinstance(lore, dict):
            return lore
    return {}


def _rel_target_of(info: FieldInfo) -> _RelTarget | None:
    """Find the ``_RelTarget`` marker on a field, including ``Relation[X] | None``."""
    for meta in info.metadata:
        if isinstance(meta, _RelTarget):
            return meta
    if typing.get_origin(info.annotation) in (Union, types.UnionType):
        for arg in typing.get_args(info.annotation):
            if typing.get_origin(arg) is Annotated:
                for meta in typing.get_args(arg)[1:]:
                    if isinstance(meta, _RelTarget):
                        return meta
    return None


def _defining_class(cls: type[Entity], field_name: str, registry: dict[str, type[Entity]]) -> str:
    """The base-most registered class in ``cls.__mro__`` that declares the field.

    Inherited fields keep the ancestor's predicate name, so e.g. a
    ``VIPRefund(Refund)`` edge still counts against ``"Refund.refunds"``
    cardinality limits.
    """
    defining = cls.__name__
    for klass in cls.__mro__:
        # inspect.get_annotations sees the class's own annotations even under
        # PEP 649 deferred evaluation (py3.14+), unlike __dict__["__annotations__"].
        own_annotations = inspect.get_annotations(klass)
        if field_name in own_annotations and registry.get(klass.__name__) is klass:
            defining = klass.__name__
    return defining


def _resolve_target(target: type[Entity] | str, registry: dict[str, type[Entity]], where: str) -> str:
    if isinstance(target, str):
        if target not in registry:
            raise LoreError(
                f"{where}: relation target {target!r} is not a registered entity class"
            )
        return target
    registered = registry.get(target.__name__)
    if registered is not target:
        raise LoreError(
            f"{where}: relation target {target.__name__!r} is not registered with this "
            "lore (did you forget @lore.entity?)"
        )
    return target.__name__


def _check_severity(value: Any, where: str) -> Severity:
    if value not in ("reject", "flag"):
        raise LoreError(f"{where}: severity must be 'reject' or 'flag', got {value!r}")
    return value


def _add_unique(mapping: dict[str, Any], key: str, spec: Any, kind: str) -> None:
    existing = mapping.get(key)
    if existing is not None and existing != spec:
        raise LoreError(
            f"{kind} {key!r} is declared twice with different options "
            "(a subclass may not redeclare an inherited field with new lore options)"
        )
    mapping[key] = spec


def compile_lore(lore: Lore) -> Guard:
    registry = dict(lore._classes)
    classes: dict[str, CompiledClass] = {}
    relations: dict[str, RelationSpec] = {}
    attributes: dict[str, AttrSpec] = {}

    for name, cls in registry.items():
        ancestors = tuple(
            klass.__name__ for klass in cls.__mro__ if registry.get(klass.__name__) is klass
        )
        class_relations: dict[str, RelationSpec] = {}
        class_attributes: dict[str, AttrSpec] = {}
        for field_name, info in cls.model_fields.items():
            if field_name == "id":
                continue
            extra = _lore_extra(info)
            owner = _defining_class(cls, field_name, registry)
            predicate = f"{owner}.{field_name}"
            severity = _check_severity(extra.get("severity", "reject"), predicate)
            rel = _rel_target_of(info)
            if rel is not None:
                if extra.get("kind") == "one_of":
                    raise LoreError(f"{predicate}: one_of() cannot be used on a relation field")
                target_name = _resolve_target(rel.target, registry, predicate)
                max_per_target = extra.get("max_per_target")
                if max_per_target is not None and (
                    not isinstance(max_per_target, int) or max_per_target < 1
                ):
                    raise LoreError(
                        f"{predicate}: max_per_target must be an int >= 1, got {max_per_target!r}"
                    )
                spec = RelationSpec(predicate, owner, field_name, target_name, max_per_target, severity)
                _add_unique(relations, predicate, spec, "relation")
                class_relations[field_name] = spec
            else:
                if extra.get("kind") == "relation":
                    raise LoreError(
                        f"{predicate}: relation() options on a field without a Relation[...] annotation"
                    )
                allowed = extra.get("one_of")
                if allowed is not None and len(allowed) == 0:
                    raise LoreError(f"{predicate}: one_of() needs at least one allowed value")
                spec = AttrSpec(
                    predicate, owner, field_name, tuple(allowed) if allowed else None, severity
                )
                _add_unique(attributes, predicate, spec, "attribute")
                class_attributes[field_name] = spec
        classes[name] = CompiledClass(cls, ancestors, class_relations, class_attributes)

    pairs: set[tuple[str, str]] = set()
    for name, cls in registry.items():
        for other in getattr(cls, "lore_disjoint_with", ()) or ():
            other_name = other if isinstance(other, str) else other.__name__
            if registry.get(other_name) is None or (
                not isinstance(other, str) and registry.get(other_name) is not other
            ):
                raise LoreError(
                    f"{name}.lore_disjoint_with names {other_name!r}, which is not a "
                    "registered entity class"
                )
            pairs.add(tuple(sorted((name, other_name))))  # symmetric closure
    for a, b in sorted(pairs):
        if b in classes[a].ancestors or a in classes[b].ancestors:
            raise LoreError(
                f"{a} is declared disjoint with {b}, but one subclasses the other — "
                "no instance could ever satisfy this lore"
            )

    rules: list[RuleSpec] = []
    for raw in lore._rules:
        target_name = _resolve_rule_target(raw, registry)
        rules.append(RuleSpec(raw.name, raw.fn, raw.message, raw.severity, target_name))

    return Guard(lore.name, classes, relations, attributes, tuple(sorted(pairs)), tuple(rules))


def _resolve_rule_target(raw: _RawRule, registry: dict[str, type[Entity]]) -> str:
    target = raw.target
    if isinstance(target, str):
        if target not in registry:
            raise LoreError(
                f"rule {raw.name!r} targets {target!r}, which is not a registered entity class"
            )
        return target
    if isinstance(target, type) and registry.get(target.__name__) is target:
        return target.__name__
    raise LoreError(
        f"rule {raw.name!r}: first-parameter annotation {target!r} is not a registered "
        "entity class"
    )
