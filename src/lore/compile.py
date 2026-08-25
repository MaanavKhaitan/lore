"""Lore self-check and the compiled ``Guard``.

``compile()`` is where broken lore fails loudly (Pydantic-style, at
import/startup time): unknown relation targets, unresolvable forward refs,
impossible disjointness, empty ``one_of``, nonsensical cardinalities. What
survives is a ``Guard``: an immutable bundle of class registry, relation/attr
specs, disjoint pairs, and rules that sessions and checks read from.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import types
import typing
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, Any, Callable, Iterable, Union

from pydantic.fields import FieldInfo

from .engine import _an
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
    many: bool = False  # Relation[list[X]]: one edge per element, set-with-accretion
    # Relation characteristics (inert defaults keep equality stable for
    # inherited fields). ``asymmetric=True`` implies ``irreflexive=True`` here.
    transitive: bool = False
    symmetric: bool = False
    asymmetric: bool = False
    irreflexive: bool = False
    inverse_of: str | None = None  # field name on the *target* class


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
        inverses: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        self.classes = classes
        self.relations = relations  # by predicate
        self.attributes = attributes  # by attr name
        self.disjoint_pairs = disjoint_pairs
        self.rules = rules
        self.inverses = inverses or {}  # predicate → inverse predicate, both directions
        self._fingerprint: str | None = None

    @property
    def fingerprint(self) -> str:
        """Content-hash of everything that can change a verdict.

        Two independently defined lores with identical declarations produce
        the same fingerprint; any semantic change (classes, ancestry,
        relation/attribute options, attribute field types, severities,
        disjointness, rule registrations) produces a different one — attr
        values round-trip through their annotations, so a changed annotation
        would otherwise silently coerce restored values. Session snapshots are
        stamped with it and ``restore`` refuses a mismatch. Caveat:
        ``@lore.rule`` function *bodies* are not hashed — only their
        name/message/severity/target — so renaming a rule invalidates old
        snapshots, but silently editing its logic does not.
        """
        if self._fingerprint is None:
            spec = {
                "lore": self.name,
                "classes": {name: list(c.ancestors) for name, c in sorted(self.classes.items())},
                "relations": [
                    [
                        s.predicate,
                        s.owner,
                        s.field,
                        s.target,
                        s.max_per_target,
                        s.severity,
                        s.transitive,
                        s.symmetric,
                        s.asymmetric,
                        s.irreflexive,
                        # Appended only for many fields, so scalar-only lores
                        # keep their pre-many fingerprints (and snapshots).
                        *(["many"] if s.many else []),
                    ]
                    for _, s in sorted(self.relations.items())
                ],
                # The completed pair map, not per-spec inverse_of: declaring an
                # inverse from either side is the same semantics, so it must be
                # the same fingerprint.
                "inverses": sorted(sorted(pair) for pair in self.inverses.items()),
                "attributes": [
                    [
                        s.attr,
                        s.owner,
                        s.field,
                        str(self.classes[s.owner].cls.model_fields[s.field].annotation),
                        None if s.one_of is None else [repr(v) for v in s.one_of],
                        s.severity,
                    ]
                    for _, s in sorted(self.attributes.items())
                ],
                "disjoint": [list(pair) for pair in self.disjoint_pairs],
                "rules": [[r.name, r.message, r.severity, r.target] for r in self.rules],
            }
            digest = hashlib.sha256(
                json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            self._fingerprint = f"sha256:{digest}"
        return self._fingerprint

    def session(self, seed: Iterable[Entity] = (), *, validate_seed: bool = True) -> "Session":
        """Open a session, grounding ``seed`` instances as trusted facts.

        The seed is checked against the lore by default (``LoreError`` on
        reject-severity violations); pass ``validate_seed=False`` to skip.
        Skipping means seed inconsistencies are trusted silently — including
        an id typed under two incomparable classes, which rehydrates as just
        one of them with the other branch's rules skipped.
        """
        from .session import Session

        return Session(self, seed=seed, validate_seed=validate_seed)

    def restore(self, blob: str | bytes) -> "Session":
        """Rehydrate a session from a :meth:`~lore.session.Session.snapshot`
        blob (see there for the durability contract)."""
        from .session import Session

        return Session.restore(self, blob)

    def to_context(self) -> str:
        """Render the lore as plain English for a system prompt.

        Prevention to the session's detection: paste this into the agent's
        prompt so it knows the rules before violating them (and so
        context-only vs validation-only vs both can be compared). Output is
        deterministic — classes and fields in declaration order, rules in
        registration order.
        """
        lines = [
            f"You are operating in the domain '{self.name}'. The entity classes and "
            "rules below define what is valid; outputs that violate a rule are "
            "rejected. Always reference existing entities by their exact id.",
            "",
            "Entities:",
        ]
        for name, compiled in self.classes.items():
            parents = compiled.ancestors[1:]
            header = f"{name} (a kind of {parents[0]})" if parents else name
            fields = ["id"]
            for field_name, attr in compiled.attributes.items():
                if attr.owner != name:
                    continue  # inherited — described on the declaring class
                if attr.one_of is not None:
                    allowed = ", ".join(repr(v) for v in attr.one_of)
                    fields.append(f"{field_name} (one of {allowed}){_advisory(attr.severity)}")
                else:
                    fields.append(field_name)
            for field_name, rel in compiled.relations.items():
                if rel.owner != name:
                    continue
                if rel.many:
                    fields.append(f"{field_name} -> {rel.target} ids (zero or more)")
                else:
                    fields.append(f"{field_name} -> {rel.target} id")
            lines.append(f"- {header}: {', '.join(fields)}")
        lines += ["", "Rules:"]
        for a, b in self.disjoint_pairs:
            lines.append(f"- Nothing can be both {_an(a)} and {_an(b)}.")
        for spec in self.relations.values():
            if spec.max_per_target is None:
                continue
            article = _an(spec.target)
            lines.append(
                f"- {article[0].upper()}{article[1:]} can have at most "
                f"{spec.max_per_target} {spec.owner} pointing at it via "
                f"'{spec.field}'.{_advisory(spec.severity)}"
            )
        for spec in self.relations.values():
            advisory = _advisory(spec.severity)
            f = spec.field
            if spec.transitive:
                lines.append(f"- If A '{f}' B and B '{f}' C, then A '{f}' C.{advisory}")
            if spec.symmetric:
                lines.append(
                    f"- If A '{f}' B, then B '{f}' A — two directions of the same fact.{advisory}"
                )
            if spec.asymmetric:
                lines.append(f"- If A '{f}' B, then B cannot '{f}' A.{advisory}")
            if spec.irreflexive:
                if spec.transitive:
                    lines.append(
                        f"- Nothing can be its own '{f}', directly or through a chain "
                        f"of '{f}' links — no cycles.{advisory}"
                    )
                else:
                    lines.append(f"- Nothing can be its own '{f}'.{advisory}")
        rendered_pairs: set[frozenset[str]] = set()
        for predicate, inverse in self.inverses.items():
            pair = frozenset((predicate, inverse))
            if pair in rendered_pairs:
                continue
            rendered_pairs.add(pair)
            lines.append(
                f"- '{predicate}' and '{inverse}' are two directions of the same fact."
            )
        flagged = [f"'{p}'" for p, spec in self.relations.items() if spec.severity == "flag"]
        suffix = f" (advisory for {', '.join(flagged)}: flagged, not rejected)" if flagged else ""
        lines.append(f"- Every relation field must reference an entity that exists.{suffix}")
        for rule in self.rules:
            template = rule.message.replace("{obj.", "{")
            lines.append(f"- Never: {template}{_advisory(rule.severity)}")
        return "\n".join(lines)


def _advisory(severity: Severity) -> str:
    return " (advisory: flagged, not rejected)" if severity == "flag" else ""


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


def _check_characteristics(spec: RelationSpec) -> None:
    """Reject contradictory characteristic combinations at compile time."""
    p = spec.predicate
    if spec.symmetric and spec.asymmetric:
        raise LoreError(f"{p}: a relation cannot be both symmetric and asymmetric")
    if spec.symmetric and spec.transitive and spec.irreflexive:
        raise LoreError(
            f"{p}: symmetric + transitive + irreflexive/asymmetric is unsatisfiable — "
            "any edge would derive a forbidden self-edge"
        )
    if spec.symmetric and spec.target != spec.owner:
        raise LoreError(
            f"{p}: a symmetric relation must point at its own class, "
            f"but the target is {spec.target!r}"
        )
    if spec.symmetric and spec.inverse_of is not None:
        raise LoreError(
            f"{p}: a symmetric relation cannot also declare inverse_of — "
            "symmetric already means the fact holds in both directions"
        )
    if spec.inverse_of is not None and not isinstance(spec.inverse_of, str):
        raise LoreError(f"{p}: inverse_of must be a field name string, got {spec.inverse_of!r}")


def _complete_inverses(
    classes: dict[str, CompiledClass], relations: dict[str, RelationSpec]
) -> dict[str, str]:
    """Resolve ``inverse_of`` declarations into a predicate→predicate map.

    The field name is looked up on the relation's *target* class, so
    inheritance lands on the defining predicate. A one-sided declaration
    completes the pair; the finished map must be an involution — each
    predicate has at most one inverse and both directions agree.
    """
    inverses: dict[str, str] = {}
    for predicate, spec in relations.items():
        if spec.inverse_of is None:
            continue
        inv_spec = classes[spec.target].relations.get(spec.inverse_of)
        if inv_spec is None:
            raise LoreError(
                f"{predicate}: inverse_of names {spec.inverse_of!r}, but {spec.target} "
                f"has no relation field named {spec.inverse_of!r} — both fields of an "
                "inverse pair must be declared"
            )
        if inv_spec.predicate == predicate:
            raise LoreError(
                f"{predicate}: inverse_of names its own field — use symmetric=True instead"
            )
        if inv_spec.symmetric:
            raise LoreError(
                f"{predicate}: inverse_of names {inv_spec.predicate!r}, which is symmetric — "
                "a symmetric relation cannot also have an inverse"
            )
        owner_ancestors = classes[spec.owner].ancestors
        target_ancestors = classes[inv_spec.target].ancestors
        if spec.owner not in target_ancestors and inv_spec.target not in owner_ancestors:
            raise LoreError(
                f"{predicate}: inverse_of names {inv_spec.predicate!r}, but that relation "
                f"points at {inv_spec.target!r}, not at {spec.owner!r} — the two fields "
                "do not describe the same pair of classes in opposite directions"
            )
        for a, b in ((predicate, inv_spec.predicate), (inv_spec.predicate, predicate)):
            existing = inverses.get(a)
            if existing is not None and existing != b:
                raise LoreError(
                    f"{a} cannot be the inverse of both {existing!r} and {b!r} — "
                    "each relation has at most one inverse"
                )
            inverses[a] = b
    return inverses


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
                spec = RelationSpec(
                    predicate,
                    owner,
                    field_name,
                    target_name,
                    max_per_target,
                    severity,
                    many=rel.many,
                    transitive=bool(extra.get("transitive")),
                    symmetric=bool(extra.get("symmetric")),
                    asymmetric=bool(extra.get("asymmetric")),
                    irreflexive=bool(extra.get("irreflexive")) or bool(extra.get("asymmetric")),
                    inverse_of=extra.get("inverse_of"),
                )
                _check_characteristics(spec)
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

    inverses = _complete_inverses(classes, relations)

    rules: list[RuleSpec] = []
    for raw in lore._rules:
        target_name = _resolve_rule_target(raw, registry)
        rules.append(RuleSpec(raw.name, raw.fn, raw.message, raw.severity, target_name))

    return Guard(
        lore.name, classes, relations, attributes, tuple(sorted(pairs)), tuple(rules), inverses
    )


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
