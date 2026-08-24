"""Grounding and the deterministic checks.

Grounding is purely mechanical (typed instance → facts; no lookups, no LLM).
Checks are deliberately dumb scans over the full ``LayeredView`` on every
propose — session graphs are tiny, so no incremental scoping — but only
violations that involve at least one *staged* fact are reported, so
pre-existing seed inconsistencies don't re-fire on every proposal.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .schema import Entity, OntologyError
from .store import AttrFact, EdgeFact, Fact, FactStore, Source, TypeFact
from .verdict import Violation

if TYPE_CHECKING:
    from .compile import CompiledClass, Guard


def ground(guard: "Guard", obj: Entity, source: Source) -> list[Fact]:
    """Typed instance → facts. Purely mechanical; subclass propagation happens
    here (one TypeFact per registered class in the MRO), which is why this
    milestone needs no inference stage.
    """
    compiled = guard.classes.get(type(obj).__name__)
    if compiled is None or compiled.cls is not type(obj):
        raise OntologyError(
            f"{type(obj).__name__} is not registered with ontology {guard.name!r}"
        )
    facts: list[Fact] = [TypeFact(obj.id, name, source) for name in compiled.ancestors]
    for field_name, rel in compiled.relations.items():
        value = getattr(obj, field_name)
        if value is None:
            continue
        facts.append(EdgeFact(obj.id, rel.predicate, value, source))
    for field_name, attr in compiled.attributes.items():
        value = getattr(obj, field_name)
        if value is None:
            continue
        facts.append(AttrFact(obj.id, attr.attr, value, source))
    return facts


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

    This is the object handed to ``@ont.rule`` functions and exposed as
    ``Session.graph`` — import it from ``ontic`` to annotate rule signatures::

        @ont.rule(message="...")
        def my_rule(refund: Refund, graph: ontic.Graph) -> bool: ...
    """

    def __init__(self, guard: "Guard", view: FactStore) -> None:
        self._guard = guard
        self._view = view

    def get(self, node_id: str) -> Entity | None:
        """Rehydrate an entity from its facts (``None`` if the node doesn't exist)."""
        compiled = _most_specific(self._guard, self._view.types_of(node_id))
        if compiled is None:
            return None
        kwargs: dict[str, object] = {"id": node_id}
        for field_name, rel in compiled.relations.items():
            edges = self._view.edges_from(node_id, rel.predicate)
            if edges:
                # Newest value wins: staged facts follow committed ones in view
                # order, so a proposal under check hydrates with its own values.
                kwargs[field_name] = edges[-1].object_id
        for field_name, attr in compiled.attributes.items():
            for fact in self._view.attrs(attr.attr):
                if fact.subject_id == node_id:
                    kwargs[field_name] = fact.value  # no break: newest value wins
        return compiled.cls(**kwargs)

    def incoming(self, node_id: str, predicate: str) -> list[EdgeFact]:
        """All edges pointing *at* ``node_id`` via ``"ClassName.field"``."""
        return [e for e in self._view.edges(predicate) if e.object_id == node_id]


# --- staged-involvement helpers -------------------------------------------
# "Staged" membership is by fact content (Session.propose already drops staged
# facts that duplicate committed ones, so content keys are unambiguous).

def _edge_key(edge: EdgeFact) -> tuple[str, str, str]:
    return (edge.subject_id, edge.predicate, edge.object_id)


def _staged_edge_keys(staged: list[Fact]) -> set[tuple[str, str, str]]:
    return {_edge_key(f) for f in staged if isinstance(f, EdgeFact)}


def _staged_typed_nodes(staged: list[Fact]) -> set[str]:
    return {f.node_id for f in staged if isinstance(f, TypeFact)}


def _staged_subject_ids(staged: list[Fact]) -> set[str]:
    """Every node a staged fact directly asserts something about — includes
    committed nodes whose fields a proposal re-asserts (their TypeFacts dedupe
    away, so ``_staged_typed_nodes`` alone would miss them)."""
    return {f.node_id if isinstance(f, TypeFact) else f.subject_id for f in staged}


def _an(noun: str) -> str:
    return f"an {noun}" if noun[:1].lower() in "aeiou" else f"a {noun}"


# --- the checks -------------------------------------------------------------


def check_existence(guard: "Guard", view: FactStore, staged: list[Fact]) -> list[Violation]:
    """Every staged edge must point at a node that exists in the view.

    Committed edges cannot *newly* dangle (facts are append-only), so staged
    edges are exactly the ones worth checking.
    """
    out = []
    for fact in staged:
        if isinstance(fact, EdgeFact) and not view.has_node(fact.object_id):
            spec = guard.relations.get(fact.predicate)
            if spec is None:
                continue
            out.append(
                Violation(
                    check="existence",
                    severity=spec.severity,
                    message=(
                        f"{fact.subject_id} refers to {fact.object_id} via {fact.predicate}, "
                        "but no such entity exists."
                    ),
                    subjects=(fact.subject_id, fact.object_id),
                )
            )
    return out


def check_range(guard: "Guard", view: FactStore, staged: list[Fact]) -> list[Violation]:
    """An edge's object must be typed as the relation's target class (subclass
    instances qualify automatically: grounding emits ancestor TypeFacts)."""
    staged_edges = _staged_edge_keys(staged)
    staged_nodes = _staged_typed_nodes(staged)
    out = []
    for predicate, spec in guard.relations.items():
        for edge in view.edges(predicate):
            if _edge_key(edge) not in staged_edges and edge.object_id not in staged_nodes:
                continue
            types = view.types_of(edge.object_id)
            if not types or spec.target in types:
                continue  # untyped object = existence's job, not range's
            out.append(
                Violation(
                    check="range",
                    severity=spec.severity,
                    message=(
                        f"{predicate} must point to {_an(spec.target)}, but {edge.object_id} "
                        f"is {_an(', '.join(sorted(types)))}."
                    ),
                    subjects=(edge.subject_id, edge.object_id),
                )
            )
    return out


def check_domain(guard: "Guard", view: FactStore, staged: list[Fact]) -> list[Violation]:
    """An edge's subject must be typed as the class that declares the field.

    Grounding always types subjects correctly, so this is only violable via
    seeded raw facts — cheap, keep it.
    """
    staged_edges = _staged_edge_keys(staged)
    staged_nodes = _staged_typed_nodes(staged)
    out = []
    for predicate, spec in guard.relations.items():
        for edge in view.edges(predicate):
            if _edge_key(edge) not in staged_edges and edge.subject_id not in staged_nodes:
                continue
            types = view.types_of(edge.subject_id)
            if not types or spec.owner in types:
                continue
            out.append(
                Violation(
                    check="domain",
                    severity=spec.severity,
                    message=(
                        f"{predicate} can only be asserted by {_an(spec.owner)}, but "
                        f"{edge.subject_id} is {_an(', '.join(sorted(types)))}."
                    ),
                    subjects=(edge.subject_id,),
                )
            )
    return out


def check_max_per_target(guard: "Guard", view: FactStore, staged: list[Fact]) -> list[Violation]:
    """No target node may be pointed at by more than ``max_per_target`` subjects
    via the relation. The message names every subject involved, marking which
    were already committed — that concreteness is what makes repair prompts work.
    """
    staged_edges = _staged_edge_keys(staged)
    out = []
    for predicate, spec in guard.relations.items():
        if spec.max_per_target is None:
            continue
        groups: dict[str, list[EdgeFact]] = {}
        for edge in view.edges(predicate):
            groups.setdefault(edge.object_id, []).append(edge)
        for object_id, edges in groups.items():
            if len(edges) <= spec.max_per_target:
                continue
            if not any(_edge_key(e) in staged_edges for e in edges):
                continue  # pre-existing overrun, nothing staged made it worse
            prior = sorted(e.subject_id for e in edges if _edge_key(e) not in staged_edges)
            proposed = sorted(e.subject_id for e in edges if _edge_key(e) in staged_edges)
            listing = ", ".join(
                [f"{s} (already committed)" for s in prior] + [f"{s} (proposed)" for s in proposed]
            )
            article = _an(spec.target)
            out.append(
                Violation(
                    check="max_per_target",
                    severity=spec.severity,
                    message=(
                        f"{article[0].upper()}{article[1:]} can have at most "
                        f"{spec.max_per_target} {spec.owner} pointing at it via "
                        f"'{spec.field}', but {object_id} has {len(edges)}: {listing}."
                    ),
                    subjects=(object_id, *sorted(e.subject_id for e in edges)),
                )
            )
    return out


def check_single_value(guard: "Guard", view: FactStore, staged: list[Fact]) -> list[Violation]:
    """Every field is scalar: a node holds at most one value per relation and
    per attribute, and committed values are immutable. Fires when a proposal
    re-asserts a committed entity id with a changed value, or asserts two
    values for one id in a single proposal. For relation fields this is what
    enforces OWL-functional ("at most one object per subject") across turns.
    """
    staged_edges = _staged_edge_keys(staged)
    staged_attrs = [(f.subject_id, f.attr, f.value) for f in staged if isinstance(f, AttrFact)]
    out = []
    for predicate, spec in guard.relations.items():
        by_subject: dict[str, list[EdgeFact]] = {}
        for edge in view.edges(predicate):
            by_subject.setdefault(edge.subject_id, []).append(edge)
        for subject_id, edges in by_subject.items():
            if len(edges) <= 1 or not any(_edge_key(e) in staged_edges for e in edges):
                continue
            prior = sorted(e.object_id for e in edges if _edge_key(e) not in staged_edges)
            proposed = sorted(e.object_id for e in edges if _edge_key(e) in staged_edges)
            listing = ", ".join(
                [f"{o} (already committed)" for o in prior] + [f"{o} (proposed)" for o in proposed]
            )
            article = _an(spec.owner)
            out.append(
                Violation(
                    check="single_value",
                    severity=spec.severity,
                    message=(
                        f"{article[0].upper()}{article[1:]} can point at only one "
                        f"{spec.target} via '{spec.field}', but {subject_id} points at "
                        f"{len(edges)}: {listing}."
                    ),
                    subjects=(subject_id, *sorted(e.object_id for e in edges)),
                )
            )
    for attr, spec in guard.attributes.items():
        by_node: dict[str, list[AttrFact]] = {}
        for fact in view.attrs(attr):
            by_node.setdefault(fact.subject_id, []).append(fact)
        for subject_id, facts in by_node.items():
            staged_mask = [(f.subject_id, f.attr, f.value) in staged_attrs for f in facts]
            if len(facts) <= 1 or not any(staged_mask):
                continue
            listing = ", ".join(
                [f"{f.value!r} (already committed)" for f, s in zip(facts, staged_mask) if not s]
                + [f"{f.value!r} (proposed)" for f, s in zip(facts, staged_mask) if s]
            )
            out.append(
                Violation(
                    check="single_value",
                    severity=spec.severity,
                    message=(
                        f"{attr} can hold only one value, but {subject_id} has "
                        f"{len(facts)}: {listing}."
                    ),
                    subjects=(subject_id,),
                )
            )
    return out


def check_one_of(guard: "Guard", view: FactStore, staged: list[Fact]) -> list[Violation]:
    """Staged attribute values must be in the field's allowed set."""
    out = []
    for fact in staged:
        if not isinstance(fact, AttrFact):
            continue
        spec = guard.attributes.get(fact.attr)
        if spec is None or spec.one_of is None or fact.value in spec.one_of:
            continue
        allowed = ", ".join(repr(v) for v in spec.one_of)
        out.append(
            Violation(
                check="one_of",
                severity=spec.severity,
                message=f"{fact.attr} must be one of {allowed}, got {fact.value!r}.",
                subjects=(fact.subject_id,),
            )
        )
    return out


def check_disjoint(guard: "Guard", view: FactStore, staged: list[Fact]) -> list[Violation]:
    """No node may be typed as both classes of a disjoint pair. Only nodes that
    gained a staged TypeFact can newly violate this."""
    out = []
    for node_id in sorted(_staged_typed_nodes(staged)):
        types = view.types_of(node_id)
        for a, b in guard.disjoint_pairs:
            if a in types and b in types:
                out.append(
                    Violation(
                        check="disjoint",
                        severity="reject",
                        message=(
                            f"{node_id} cannot be both {_an(a)} and {_an(b)} — these are "
                            "disjoint classes."
                        ),
                        subjects=(node_id,),
                    )
                )
    return out


def check_rules(guard: "Guard", view: FactStore, staged: list[Fact]) -> list[Violation]:
    """Run ``@ont.rule`` predicates for every node a staged fact touches (not
    just newly typed nodes: re-asserting a committed id with changed fields
    must re-run its rules) that is typed with (a subclass of) the rule's
    target class."""
    graph = Graph(guard, view)
    out = []
    for node_id in sorted(_staged_subject_ids(staged)):
        types = view.types_of(node_id)
        for rule in guard.rules:
            if rule.target not in types:
                continue
            obj = graph.get(node_id)
            if obj is None or rule.fn(obj, graph):
                continue
            out.append(
                Violation(
                    check=f"rule:{rule.name}",
                    severity=rule.severity,
                    message=rule.message.format(obj=obj),
                    subjects=(node_id,),
                )
            )
    return out


ALL_CHECKS = (
    check_existence,
    check_range,
    check_domain,
    check_max_per_target,
    check_single_value,
    check_one_of,
    check_disjoint,
    check_rules,
)


def run_checks(guard: "Guard", view: FactStore, staged: list[Fact]) -> list[Violation]:
    violations: list[Violation] = []
    for check in ALL_CHECKS:
        violations.extend(check(guard, view, staged))
    return violations
