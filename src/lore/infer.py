"""The inference pass: derived facts (mirrors, transitive closure) with provenance.

``derive`` runs inside every ``Session.propose`` over committed ∪ staged.
"Base" is every edge in that view — seed and asserted alike, so seed
validation derives too. Derived edges carry ``source="derived"`` and are
checked but never committed: ``build_context`` computes them locally per
proposal and hands them to the checks via :class:`CheckContext`, so a
rejected proposal's derivations vanish with it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from .store import EdgeFact, Fact, FactStore, InMemoryStore, LayeredView, TypeFact

if TYPE_CHECKING:
    from .compile import Guard

EdgeKey = tuple[str, str, str]  # (subject_id, predicate, object_id)


def _edge_key(edge: EdgeFact) -> EdgeKey:
    return (edge.subject_id, edge.predicate, edge.object_id)


@dataclass(frozen=True)
class Derivation:
    """One derived edge plus the flattened path of base facts that produced it.

    The first (= fewest base facts) path found is kept; paths are recomputed
    on every propose and never committed.
    """

    edge: EdgeFact
    base_path: tuple[EdgeFact, ...]
    kind: Literal["symmetric", "inverse", "transitive"]


@dataclass
class Derivations:
    """The derived-edge stores, split by origin, plus per-edge provenance.

    Three stores because checks see different unions: ``single_value`` counts
    symmetric mirrors but not inverse mirrors, cardinality counts both kinds
    of mirror, and only irreflexive/asymmetric see the closure.
    """

    sym_mirrors: InMemoryStore
    inv_mirrors: InMemoryStore
    closure: InMemoryStore
    provenance: dict[EdgeKey, Derivation]


def derive(guard: "Guard", view: FactStore) -> Derivations:
    """Compute all derived edges over ``view`` (committed ∪ staged).

    1. Mirrors — flip base edges of symmetric predicates (onto the same
       predicate) and inverse pairs (onto the inverse predicate). Mirrors and
       closure edges are never themselves mirrored.
    2. Transitive closure — per transitive predicate, join to fixpoint over
       base edges ∪ mirrors that landed on that predicate. Self-edges are
       emitted (they are what the irreflexive check catches).

    A derived edge that duplicates a base edge by content is dropped, so the
    three stores are disjoint from the view and from each other. A derived
    edge's ``step`` is the max of its base facts' steps — the commit at which
    it became true.
    """
    sym = InMemoryStore()
    inv = InMemoryStore()
    closure = InMemoryStore()
    provenance: dict[EdgeKey, Derivation] = {}

    base_keys = {
        _edge_key(e) for predicate in guard.relations for e in view.edges(predicate)
    }

    def mirror(base: EdgeFact, onto: str, kind: str, store: InMemoryStore) -> None:
        edge = EdgeFact(base.object_id, onto, base.subject_id, "derived", step=base.step)
        key = _edge_key(edge)
        if key in base_keys or key in provenance:
            return
        store.add([edge])
        provenance[key] = Derivation(edge, (base,), kind)  # type: ignore[arg-type]

    for predicate, spec in guard.relations.items():
        if spec.symmetric:
            for base in view.edges(predicate):
                mirror(base, predicate, "symmetric", sym)
        inverse = guard.inverses.get(predicate)
        if inverse is not None:
            for base in view.edges(predicate):
                mirror(base, inverse, "inverse", inv)

    for predicate, spec in guard.relations.items():
        if not spec.transitive:
            continue
        universe: list[tuple[EdgeFact, tuple[EdgeFact, ...]]] = [
            (e, (e,)) for e in view.edges(predicate)
        ]
        for store in (sym, inv):
            for e in store.edges(predicate):
                universe.append((e, provenance[_edge_key(e)].base_path))
        known: set[EdgeKey] = {_edge_key(e) for e, _ in universe}
        adjacency: dict[str, list[tuple[EdgeFact, tuple[EdgeFact, ...]]]] = {}
        for e, path in universe:
            adjacency.setdefault(e.subject_id, []).append((e, path))
        # Level-by-level right-extension by one universe edge: level N holds
        # exactly the N+1-base-fact composites, so the first path found per
        # edge is a shortest one.
        frontier = universe
        while frontier:
            next_frontier: list[tuple[EdgeFact, tuple[EdgeFact, ...]]] = []
            for e1, path1 in frontier:
                for e2, path2 in adjacency.get(e1.object_id, ()):
                    key = (e1.subject_id, predicate, e2.object_id)
                    if key in known:
                        continue
                    known.add(key)
                    path = path1 + path2
                    edge = EdgeFact(
                        e1.subject_id,
                        predicate,
                        e2.object_id,
                        "derived",
                        step=max(f.step for f in path),
                    )
                    provenance[key] = Derivation(edge, path, "transitive")
                    closure.add([edge])
                    next_frontier.append((edge, path))
            frontier = next_frontier

    return Derivations(sym, inv, closure, provenance)


@dataclass
class CheckContext:
    """Everything the checks need for one proposal.

    Built by :func:`build_context` inside ``Session.propose`` and passed
    through — never stored on the session, so ``Session.check``'s
    save/restore stays complete.
    """

    guard: "Guard"
    view: FactStore  # asserted facts only: committed ∪ staged
    # The pre-proposal world (committed only) — the baseline the rule check
    # compares against to decide whether a failure is *newly caused* by the
    # staged facts or predates them.
    base: FactStore
    staged: list[Fact]
    # Staged involvement by content key: asserted staged edges plus every
    # derived edge whose base path touches one.
    staged_edge_keys: set[EdgeKey]
    staged_nodes: set[str]  # nodes with a staged TypeFact
    staged_subjects: set[str]  # every node a staged fact asserts something about
    # Nodes whose rules must re-run: staged subjects plus every node one
    # asserted hop away in either direction — a staged fact on a neighbor (a
    # new incoming edge, a late-filled optional attribute, a subclass re-type)
    # can change what an aggregate rule on an otherwise-untouched committed
    # node sees via ``graph.incoming`` and one-hop ``graph.get`` hydration.
    rule_nodes: set[str]
    single_value_view: FactStore  # asserted + symmetric mirrors
    cardinality_view: FactStore  # asserted + symmetric and inverse mirrors
    relational_view: FactStore  # asserted + all mirrors + closure
    derivations: Derivations


def build_context(
    guard: "Guard", view: FactStore, staged: list[Fact], base: FactStore | None = None
) -> CheckContext:
    """``base`` is the committed-only store; ``None`` means everything in
    ``view`` is staged (seed validation), so the baseline is an empty world."""
    derivations = derive(guard, view)
    raw_staged = {_edge_key(f) for f in staged if isinstance(f, EdgeFact)}
    staged_edge_keys = set(raw_staged)
    for key, derivation in derivations.provenance.items():
        if any(_edge_key(base) in raw_staged for base in derivation.base_path):
            staged_edge_keys.add(key)
    single_value_view = LayeredView(view, derivations.sym_mirrors)
    cardinality_view = LayeredView(single_value_view, derivations.inv_mirrors)
    relational_view = LayeredView(cardinality_view, derivations.closure)
    staged_subjects = {
        f.node_id if isinstance(f, TypeFact) else f.subject_id for f in staged
    }
    # One asserted hop covers every read a one-hop aggregate rule can make;
    # staged edges are in the view, so their targets are included too.
    rule_nodes = set(staged_subjects)
    for predicate in guard.relations:
        for edge in view.edges(predicate):
            if edge.subject_id in staged_subjects:
                rule_nodes.add(edge.object_id)
            elif edge.object_id in staged_subjects:
                rule_nodes.add(edge.subject_id)
    return CheckContext(
        guard=guard,
        view=view,
        base=base if base is not None else InMemoryStore(),
        staged=staged,
        staged_edge_keys=staged_edge_keys,
        staged_nodes={f.node_id for f in staged if isinstance(f, TypeFact)},
        staged_subjects=staged_subjects,
        rule_nodes=rule_nodes,
        single_value_view=single_value_view,
        cardinality_view=cardinality_view,
        relational_view=relational_view,
        derivations=derivations,
    )
