"""The read-only session graph handed to rules and ``Session.graph``."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeVar, overload

from .schema import Entity
from .store import EdgeFact, FactStore

if TYPE_CHECKING:
    from .compile import CompiledClass, Guard

EntityT = TypeVar("EntityT", bound=Entity)


def _most_specific(guard: "Guard", type_names: set[str]) -> "CompiledClass | None":
    """The most specific registered class among ``type_names`` (most registered
    ancestors wins; class name breaks ties deterministically)."""
    candidates = [guard.classes[t] for t in type_names if t in guard.classes]
    if not candidates:
        return None
    candidates.sort(key=lambda c: (-len(c.ancestors), c.cls.__name__))
    return candidates[0]


class Graph:
    """Read-only view of the session graph.

    This is the object handed to ``@lore.rule`` functions and exposed as
    ``Session.graph`` — import it from ``lore`` to annotate rule signatures::

        @lore.rule(message="...")
        def my_rule(refund: Refund, graph: lore.Graph) -> bool: ...

    ``get``/``incoming`` read asserted facts only; ``reachable`` additionally
    sees derived (mirror and transitive-closure) edges when the graph carries
    them — that is how rules see inference without derived edges corrupting
    entity rehydration.
    """

    def __init__(
        self,
        guard: "Guard",
        view: FactStore,
        mirrors: FactStore | None = None,
        closure: FactStore | None = None,
    ) -> None:
        self._guard = guard
        self._view = view
        self._mirrors = mirrors
        self._closure = closure

    @overload
    def get(self, node_id: str) -> Entity | None: ...

    @overload
    def get(self, node_id: str, expected_type: type[EntityT]) -> EntityT | None: ...

    def get(self, node_id: str, expected_type: type[Entity] = Entity) -> Entity | None:
        """Rehydrate an entity from its asserted facts (``None`` if the node
        doesn't exist or is not an instance of ``expected_type``).

        Rules that follow relations should pass the expected target class
        and handle ``None``: proposals can contain missing or wrongly typed
        targets, which the existence/range checks report separately.
        """
        compiled = _most_specific(self._guard, self._view.types_of(node_id))
        if compiled is None or not issubclass(compiled.cls, expected_type):
            return None
        kwargs: dict[str, Any] = {"id": node_id}
        for field_name, rel in compiled.relations.items():
            edges = self._view.edges_from(node_id, rel.predicate)
            if rel.many:
                # The full list in view insertion order (committed before
                # staged) — always set, so a required many field with zero
                # edges hydrates as []. Order is deterministic but not
                # semantic; rules should treat the list as a set.
                kwargs[field_name] = [e.object_id for e in edges]
            elif edges:
                # Newest value wins for scalar fields: staged facts follow
                # committed ones in view order, so a proposal under check
                # hydrates with its own values.
                kwargs[field_name] = edges[-1].object_id
        for field_name, attr in compiled.attributes.items():
            for fact in self._view.attrs_of(node_id, attr.attr):
                kwargs[field_name] = fact.value  # no break: newest value wins
        return compiled.cls(**kwargs)

    def incoming(self, node_id: str, predicate: str) -> list[EdgeFact]:
        """All asserted edges pointing *at* ``node_id`` via ``"ClassName.field"``."""
        return self._view.edges_to(node_id, predicate)

    def reachable(self, node_id: str, predicate: str) -> set[str]:
        """Every node reachable from ``node_id`` via one or more ``predicate``
        edges — asserted, mirror, and transitive-closure edges alike.

        ``node_id`` itself is included only when it lies on a cycle. This is
        the traversal API for rules like "the approver must be somewhere in
        the filer's reports_to chain".
        """
        adjacency: dict[str, set[str]] = {}
        for store in (self._view, self._mirrors, self._closure):
            if store is None:
                continue
            for edge in store.edges(predicate):
                adjacency.setdefault(edge.subject_id, set()).add(edge.object_id)
        seen: set[str] = set()
        frontier = [node_id]
        while frontier:
            next_frontier = []
            for node in frontier:
                for target in adjacency.get(node, ()):
                    if target not in seen:
                        seen.add(target)
                        next_frontier.append(target)
            frontier = next_frontier
        return seen
