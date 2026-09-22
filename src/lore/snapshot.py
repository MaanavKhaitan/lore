"""Snapshot JSON codec, shared by durable sessions and visualization traces."""

from __future__ import annotations

import json
from typing import Any, Iterable

from pydantic import TypeAdapter

from .compile import Guard
from .schema import LoreError
from .store import AttrFact, EdgeFact, Fact, TypeFact


def encode_snapshot(guard: Guard, facts: Iterable[Fact]) -> str:
    """Encode committed facts without changing their order or provenance."""
    adapters: dict[str, TypeAdapter[Any]] = {}
    payload = {
        "format": _SNAPSHOT_FORMAT,
        "lore": guard.name,
        "fingerprint": guard.fingerprint,
        "facts": [encode_fact(guard, fact, adapters) for fact in facts],
    }
    return json.dumps(payload, separators=(",", ":"))


def decode_snapshot(guard: Guard, blob: str | bytes) -> list[Fact]:
    """Validate the envelope and decode facts; policy checks are separate."""
    try:
        data = json.loads(blob)
    except (ValueError, TypeError, UnicodeDecodeError) as exc:
        raise LoreError(f"not a lore session snapshot: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("facts"), list):
        raise LoreError("not a lore session snapshot (no fact list)")
    if data.get("format") != _SNAPSHOT_FORMAT:
        raise LoreError(
            f"unsupported snapshot format {data.get('format')!r} "
            f"(this lore version reads format {_SNAPSHOT_FORMAT})"
        )
    if data.get("fingerprint") != guard.fingerprint:
        raise LoreError(
            f"snapshot was taken under lore {data.get('lore')!r} with fingerprint "
            f"{data.get('fingerprint')!r}, but restoring under {guard.name!r} with "
            f"{guard.fingerprint!r} — the lore has changed since this snapshot was "
            "taken; restore with the original definition or start a fresh session"
        )
    adapters: dict[str, TypeAdapter[Any]] = {}
    return [_decode_fact(guard, raw, adapters) for raw in data["facts"]]


# --- snapshot codec -----------------------------------------------------------
# Attribute values round-trip through the owning field's Pydantic annotation
# (datetime, Decimal, enums, ... come back as ``==``-equal values of the types
# the rules saw before the snapshot, though pydantic may canonicalize e.g.
# tzinfo implementations); a value the annotation cannot serialize or
# re-validate raises instead of degrading silently. Ids/sources are plain str.

_SNAPSHOT_FORMAT = 1
_SOURCES = ("seed", "asserted")


def _attr_adapter(guard: Guard, attr: str, adapters: dict[str, TypeAdapter[Any]]) -> TypeAdapter[Any]:
    adapter = adapters.get(attr)
    if adapter is None:
        spec = guard.attributes.get(attr)
        if spec is None:
            raise LoreError(
                f"snapshot refers to attribute {attr!r}, which is not in lore {guard.name!r}"
            )
        annotation = guard.classes[spec.owner].cls.model_fields[spec.field].annotation
        adapter = adapters[attr] = TypeAdapter(annotation)
    return adapter


def encode_fact(guard: Guard, fact: Fact, adapters: dict[str, TypeAdapter[Any]]) -> dict[str, Any]:
    """Encode one fact as the snapshot codec's JSON dict (shared with lore.viz)."""
    if isinstance(fact, TypeFact):
        return {"kind": "type", "node": fact.node_id, "class": fact.type_name, "source": fact.source,
                "step": fact.step}
    if isinstance(fact, EdgeFact):
        return {
            "kind": "edge",
            "subject": fact.subject_id,
            "predicate": fact.predicate,
            "object": fact.object_id,
            "source": fact.source,
            "step": fact.step,
        }
    try:
        value = _attr_adapter(guard, fact.attr, adapters).dump_python(
            fact.value, mode="json", warnings="error"
        )
    except LoreError:
        raise
    except Exception as exc:
        raise LoreError(
            f"cannot snapshot {fact.attr}={fact.value!r}: the value does not serialize "
            f"through the field's annotation ({exc})"
        ) from exc
    return {"kind": "attr", "subject": fact.subject_id, "attr": fact.attr, "value": value,
            "source": fact.source, "step": fact.step}


def _decode_fact(guard: Guard, raw: Any, adapters: dict[str, TypeAdapter[Any]]) -> Fact:
    try:
        kind, source = raw["kind"], raw["source"]
        if source not in _SOURCES:
            raise LoreError(f"malformed snapshot fact (bad source): {raw!r}")
        step = raw.get("step", 0)
        if not isinstance(step, int) or isinstance(step, bool) or step < 0:
            raise LoreError(f"malformed snapshot fact (bad step): {raw!r}")
        if kind == "type":
            if raw["class"] not in guard.classes:
                raise LoreError(
                    f"snapshot types {raw['node']!r} as {raw['class']!r}, which is not "
                    f"a class in lore {guard.name!r}"
                )
            return TypeFact(raw["node"], raw["class"], source, step=step)
        if kind == "edge":
            if raw["predicate"] not in guard.relations:
                raise LoreError(
                    f"snapshot refers to relation {raw['predicate']!r}, which is not "
                    f"in lore {guard.name!r}"
                )
            return EdgeFact(raw["subject"], raw["predicate"], raw["object"], source, step=step)
        if kind == "attr":
            adapter = _attr_adapter(guard, raw["attr"], adapters)
            try:
                value = adapter.validate_python(raw["value"])
            except Exception as exc:
                raise LoreError(
                    f"cannot restore {raw['attr']}={raw['value']!r} from a snapshot: {exc}"
                ) from exc
            return AttrFact(raw["subject"], raw["attr"], value, source, step=step)
    except (KeyError, TypeError) as exc:
        raise LoreError(f"malformed snapshot fact: {raw!r}") from exc
    raise LoreError(f"malformed snapshot fact (unknown kind): {raw!r}")
