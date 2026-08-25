"""Facts and fact stores.

Everything the engine checks is one of three fact shapes, produced by grounding
entity instances (see ``engine.ground``). Facts are immutable and append-only;
``source`` records where a fact came from ("seed" = trusted session setup,
"asserted" = claimed by the agent, "derived" = inferred by ``lore.infer``),
not whether it is committed yet. ``step`` is the commit that made the fact
true (0 = seeded; sessions stamp it at commit time). It is ``compare=False``
so hash/eq ignore it: a stamped fact still equals — and content-dedups
against — its unstamped original.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Protocol, Union

Source = Literal["seed", "asserted", "derived"]


@dataclass(frozen=True)
class TypeFact:
    """``node_id`` is an instance of the registered class ``type_name``."""

    node_id: str
    type_name: str
    source: Source
    step: int = field(default=0, compare=False)


@dataclass(frozen=True)
class EdgeFact:
    """A relation edge. ``predicate`` is ``"DefiningClass.field_name"``."""

    subject_id: str
    predicate: str
    object_id: str
    source: Source
    step: int = field(default=0, compare=False)


@dataclass(frozen=True)
class AttrFact:
    """A scalar attribute. ``attr`` is ``"DefiningClass.field_name"``."""

    subject_id: str
    attr: str
    value: Any
    source: Source
    step: int = field(default=0, compare=False)


Fact = Union[TypeFact, EdgeFact, AttrFact]


class FactStore(Protocol):
    """The seam for future backends (graph DBs, live KGs). Keep it small."""

    def add(self, facts: Iterable[Fact]) -> None: ...
    def has_node(self, node_id: str) -> bool: ...
    def types_of(self, node_id: str) -> set[str]: ...
    def nodes_of(self, type_name: str) -> set[str]: ...
    def edges(self, predicate: str) -> list[EdgeFact]: ...
    def edges_from(self, subject_id: str, predicate: str) -> list[EdgeFact]: ...
    def edges_to(self, object_id: str, predicate: str) -> list[EdgeFact]: ...
    def attrs(self, attr: str) -> list[AttrFact]: ...
    def attrs_of(self, subject_id: str, attr: str) -> list[AttrFact]: ...


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
        # Secondary indexes keyed by (predicate, node) / (attr, node): entity
        # rehydration and incoming-edge reads are per-node, and rules run them
        # per target instance — full-list scans would make each propose
        # quadratic in world size.
        self._edges_by_subject: dict[tuple[str, str], list[EdgeFact]] = {}
        self._edges_by_object: dict[tuple[str, str], list[EdgeFact]] = {}
        self._attrs: dict[str, list[AttrFact]] = {}
        self._attrs_by_subject: dict[tuple[str, str], list[AttrFact]] = {}

    def add(self, facts: Iterable[Fact]) -> None:
        for fact in facts:
            if isinstance(fact, TypeFact):
                if fact.type_name in self._types.get(fact.node_id, ()):
                    continue
                self._types.setdefault(fact.node_id, set()).add(fact.type_name)
            elif isinstance(fact, EdgeFact):
                bucket = self._edges_by_subject.setdefault(
                    (fact.predicate, fact.subject_id), []
                )
                if any(e.object_id == fact.object_id for e in bucket):
                    continue
                bucket.append(fact)
                self._edges.setdefault(fact.predicate, []).append(fact)
                self._edges_by_object.setdefault(
                    (fact.predicate, fact.object_id), []
                ).append(fact)
            elif isinstance(fact, AttrFact):
                abucket = self._attrs_by_subject.setdefault(
                    (fact.attr, fact.subject_id), []
                )
                if any(a.value == fact.value for a in abucket):
                    continue
                abucket.append(fact)
                self._attrs.setdefault(fact.attr, []).append(fact)
            else:
                raise TypeError(f"not a fact: {fact!r}")
            self._facts.append(fact)

    def has_node(self, node_id: str) -> bool:
        return node_id in self._types

    def types_of(self, node_id: str) -> set[str]:
        return set(self._types.get(node_id, ()))

    def nodes_of(self, type_name: str) -> set[str]:
        return {n for n, types in self._types.items() if type_name in types}

    def edges(self, predicate: str) -> list[EdgeFact]:
        return list(self._edges.get(predicate, ()))

    def edges_from(self, subject_id: str, predicate: str) -> list[EdgeFact]:
        return list(self._edges_by_subject.get((predicate, subject_id), ()))

    def edges_to(self, object_id: str, predicate: str) -> list[EdgeFact]:
        return list(self._edges_by_object.get((predicate, object_id), ()))

    def attrs(self, attr: str) -> list[AttrFact]:
        return list(self._attrs.get(attr, ()))

    def attrs_of(self, subject_id: str, attr: str) -> list[AttrFact]:
        return list(self._attrs_by_subject.get((attr, subject_id), ()))

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

    def nodes_of(self, type_name: str) -> set[str]:
        return self._base.nodes_of(type_name) | self._overlay.nodes_of(type_name)

    def edges(self, predicate: str) -> list[EdgeFact]:
        base = self._base.edges(predicate)
        return base + [e for e in self._overlay.edges(predicate) if not any(_same_edge(e, b) for b in base)]

    def edges_from(self, subject_id: str, predicate: str) -> list[EdgeFact]:
        base = self._base.edges_from(subject_id, predicate)
        return base + [
            e
            for e in self._overlay.edges_from(subject_id, predicate)
            if not any(_same_edge(e, b) for b in base)
        ]

    def edges_to(self, object_id: str, predicate: str) -> list[EdgeFact]:
        base = self._base.edges_to(object_id, predicate)
        return base + [
            e
            for e in self._overlay.edges_to(object_id, predicate)
            if not any(_same_edge(e, b) for b in base)
        ]

    def attrs(self, attr: str) -> list[AttrFact]:
        base = self._base.attrs(attr)
        return base + [
            a
            for a in self._overlay.attrs(attr)
            if not any(b.subject_id == a.subject_id and b.value == a.value for b in base)
        ]

    def attrs_of(self, subject_id: str, attr: str) -> list[AttrFact]:
        base = self._base.attrs_of(subject_id, attr)
        return base + [
            a
            for a in self._overlay.attrs_of(subject_id, attr)
            if not any(b.value == a.value for b in base)
        ]
