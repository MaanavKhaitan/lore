"""Facts and fact stores.

Everything the engine checks is one of three fact shapes, produced by grounding
entity instances (see ``engine.ground``). Facts are immutable and append-only;
``source`` records where a fact came from ("seed" = trusted session setup,
"asserted" = claimed by the agent), not whether it is committed yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Literal, Protocol, Union

Source = Literal["seed", "asserted"]


@dataclass(frozen=True)
class TypeFact:
    """``node_id`` is an instance of the registered class ``type_name``."""

    node_id: str
    type_name: str
    source: Source


@dataclass(frozen=True)
class EdgeFact:
    """A relation edge. ``predicate`` is ``"DefiningClass.field_name"``."""

    subject_id: str
    predicate: str
    object_id: str
    source: Source


@dataclass(frozen=True)
class AttrFact:
    """A scalar attribute. ``attr`` is ``"DefiningClass.field_name"``."""

    subject_id: str
    attr: str
    value: Any
    source: Source


Fact = Union[TypeFact, EdgeFact, AttrFact]


class FactStore(Protocol):
    """The seam for future backends (graph DBs, live KGs). Keep it small."""

    def add(self, facts: Iterable[Fact]) -> None: ...
    def has_node(self, node_id: str) -> bool: ...
    def types_of(self, node_id: str) -> set[str]: ...
    def edges(self, predicate: str) -> list[EdgeFact]: ...
    def edges_from(self, subject_id: str, predicate: str) -> list[EdgeFact]: ...
    def attrs(self, attr: str) -> list[AttrFact]: ...


def _same_edge(a: EdgeFact, b: EdgeFact) -> bool:
    return a.subject_id == b.subject_id and a.object_id == b.object_id


class InMemoryStore:
    """Dict/set-backed ``FactStore``. Session graphs are tiny (10^2–10^3 facts).

    ``add`` is idempotent by fact *content* (ignoring ``source``): re-asserting
    a fact that is already present is a no-op, so duplicate assertions never
    double-count in cardinality checks.
    """

    def __init__(self) -> None:
        self._facts: list[Fact] = []
        self._types: dict[str, set[str]] = {}
        self._edges: dict[str, list[EdgeFact]] = {}
        self._attrs: dict[str, list[AttrFact]] = {}

    def add(self, facts: Iterable[Fact]) -> None:
        for fact in facts:
            if isinstance(fact, TypeFact):
                if fact.type_name in self._types.get(fact.node_id, ()):
                    continue
                self._types.setdefault(fact.node_id, set()).add(fact.type_name)
            elif isinstance(fact, EdgeFact):
                bucket = self._edges.setdefault(fact.predicate, [])
                if any(_same_edge(e, fact) for e in bucket):
                    continue
                bucket.append(fact)
            elif isinstance(fact, AttrFact):
                abucket = self._attrs.setdefault(fact.attr, [])
                if any(a.subject_id == fact.subject_id and a.value == fact.value for a in abucket):
                    continue
                abucket.append(fact)
            else:
                raise TypeError(f"not a fact: {fact!r}")
            self._facts.append(fact)

    def has_node(self, node_id: str) -> bool:
        return node_id in self._types

    def types_of(self, node_id: str) -> set[str]:
        return set(self._types.get(node_id, ()))

    def edges(self, predicate: str) -> list[EdgeFact]:
        return list(self._edges.get(predicate, ()))

    def edges_from(self, subject_id: str, predicate: str) -> list[EdgeFact]:
        return [e for e in self._edges.get(predicate, ()) if e.subject_id == subject_id]

    def attrs(self, attr: str) -> list[AttrFact]:
        return list(self._attrs.get(attr, ()))

    def all_facts(self) -> tuple[Fact, ...]:
        """Every fact in insertion order (not part of the FactStore protocol)."""
        return tuple(self._facts)


class LayeredView:
    """Read-only ``FactStore`` view of committed ∪ staged.

    This is how propose/check/commit works without copying: checks always run
    against ``LayeredView(committed, staged)``. Overlay facts that duplicate a
    base fact by content are suppressed, so union reads never double-count.
    """

    def __init__(self, base: FactStore, overlay: FactStore) -> None:
        self._base = base
        self._overlay = overlay

    def add(self, facts: Iterable[Fact]) -> None:
        raise TypeError("LayeredView is read-only")

    def has_node(self, node_id: str) -> bool:
        return self._base.has_node(node_id) or self._overlay.has_node(node_id)

    def types_of(self, node_id: str) -> set[str]:
        return self._base.types_of(node_id) | self._overlay.types_of(node_id)

    def edges(self, predicate: str) -> list[EdgeFact]:
        base = self._base.edges(predicate)
        return base + [e for e in self._overlay.edges(predicate) if not any(_same_edge(e, b) for b in base)]

    def edges_from(self, subject_id: str, predicate: str) -> list[EdgeFact]:
        return [e for e in self.edges(predicate) if e.subject_id == subject_id]

    def attrs(self, attr: str) -> list[AttrFact]:
        base = self._base.attrs(attr)
        return base + [
            a
            for a in self._overlay.attrs(attr)
            if not any(b.subject_id == a.subject_id and b.value == a.value for b in base)
        ]
