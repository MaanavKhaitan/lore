"""Render a compiled Guard as the World-tab JSON payload.

The payload is purely structural — the viewer derives its plain-English
badges from the boolean characteristics — except for rules and goals, whose
``plain`` strings are precomputed here mirroring ``Guard.to_context()``
phrasing, so the English the UI shows is the English the agent was prompted
with.
"""

from __future__ import annotations

import inspect
import types
import typing
from typing import Any

from ..compile import Guard, _advisory

_SCALARS = (str, int, float, bool)


def _render_type(annotation: Any) -> str:
    """A short, human-facing rendering of a field annotation.

    Display-only (never parsed back): scalars by name, unions joined with
    ``|``, parameterized generics recursively; anything else falls back to
    ``str(annotation)`` with module prefixes stripped.
    """
    if annotation is None or annotation is type(None):
        return "None"
    origin = typing.get_origin(annotation)
    if origin in (types.UnionType, typing.Union):
        return " | ".join(_render_type(arg) for arg in typing.get_args(annotation))
    if origin is not None:
        args = ", ".join(_render_type(arg) for arg in typing.get_args(annotation))
        name = getattr(origin, "__name__", str(origin))
        return f"{name}[{args}]" if args else name
    if isinstance(annotation, type):
        return annotation.__name__
    text = str(annotation)
    for prefix in ("typing.",):
        text = text.replace(prefix, "")
    return text.rsplit(".", 1)[-1] if "." in text and " " not in text else text


def _json_value(value: Any) -> Any:
    """A one_of/default value as JSON: raw when it's a JSON scalar, else repr."""
    if value is None or isinstance(value, _SCALARS):
        return value
    return repr(value)


def _fields(guard: Guard, class_name: str) -> list[dict[str, Any]]:
    compiled = guard.classes[class_name]
    fields: list[dict[str, Any]] = []
    for field_name, info in compiled.cls.model_fields.items():
        if field_name == "id":
            continue
        inherited: str | None
        if field_name in compiled.relations:
            spec = compiled.relations[field_name]
            inherited = spec.owner if spec.owner != class_name else None
            fields.append(
                {
                    "kind": "relation",
                    "name": field_name,
                    "predicate": spec.predicate,
                    "target": spec.target,
                    "many": spec.many,
                    "required": info.is_required(),
                    "inheritedFrom": inherited,
                }
            )
            continue
        attr = compiled.attributes.get(field_name)
        if attr is None:
            continue  # not part of the compiled lore (shouldn't happen)
        inherited = attr.owner if attr.owner != class_name else None
        default = None
        if not info.is_required():
            raw_default = info.get_default(call_default_factory=True)
            default = None if raw_default is None else repr(raw_default)
        fields.append(
            {
                "kind": "attr",
                "name": field_name,
                "attr": attr.attr,
                "type": _render_type(info.annotation),
                "oneOf": None if attr.one_of is None else [_json_value(v) for v in attr.one_of],
                "severity": attr.severity,
                "required": info.is_required(),
                "default": default,
                "inheritedFrom": inherited,
            }
        )
    return fields


def _rule_entry(spec: Any, prefix: str) -> dict[str, Any]:
    # Mirrors to_context(): "{obj.x}" displays as "{x}", flag severity gets
    # the advisory suffix — the UI shows exactly what the prompt said.
    template = spec.message.replace("{obj.", "{")
    return {
        "name": spec.name,
        "message": spec.message,
        "plain": f"{prefix}{template}{_advisory(spec.severity)}",
        "doc": inspect.getdoc(spec.fn),
        "severity": spec.severity,
        "target": spec.target,
    }


def spec_json(guard: Guard) -> dict[str, Any]:
    """The World-tab payload for ``guard`` — JSON-safe, deterministic order
    (classes and fields in declaration order, rules and goals in registration
    order, same as ``to_context()``)."""
    inverse_pairs: list[list[str]] = []
    seen: set[frozenset[str]] = set()
    for predicate, inverse in guard.inverses.items():
        pair = frozenset((predicate, inverse))
        if pair in seen:
            continue
        seen.add(pair)
        inverse_pairs.append(sorted((predicate, inverse)))
    inverse_pairs.sort()

    return {
        "format": 1,
        "kind": "lore-spec",
        "lore": guard.name,
        "fingerprint": guard.fingerprint,
        "classes": [
            {
                "name": name,
                "parents": list(compiled.ancestors[1:]),
                "doc": compiled.cls.__dict__.get("__doc__"),
                "fields": _fields(guard, name),
            }
            for name, compiled in guard.classes.items()
        ],
        "relations": [
            {
                "predicate": spec.predicate,
                "owner": spec.owner,
                "field": spec.field,
                "target": spec.target,
                "many": spec.many,
                "maxPerTarget": spec.max_per_target,
                "severity": spec.severity,
                "transitive": spec.transitive,
                "symmetric": spec.symmetric,
                "asymmetric": spec.asymmetric,
                "irreflexive": spec.irreflexive,
                "inverse": guard.inverses.get(spec.predicate),
            }
            for spec in guard.relations.values()
        ],
        "inversePairs": inverse_pairs,
        "disjointPairs": [list(pair) for pair in guard.disjoint_pairs],
        "rules": [_rule_entry(rule, "Never: ") for rule in guard.rules],
        "goals": [_rule_entry(goal, "Never finish while: ") for goal in guard.goals],
    }
