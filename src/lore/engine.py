"""Grounding and the deterministic checks.

Grounding is purely mechanical (typed instance → facts; no lookups, no LLM).
Checks are deliberately dumb scans over composed fact views on every propose —
session graphs are tiny (one agent run, 10²–10³ facts), so no incremental
scoping — but only violations *caused* by staged facts are reported: for the
axiom checks, violations involving a staged fact directly or through a
derived edge; for rules, failures on the proposal's neighborhood plus
committed nodes whose rule the proposal newly flips from satisfied to
violated (see ``check_rules``). Pre-existing seed inconsistencies therefore
don't re-fire on every proposal.

Each check receives a :class:`~lore.infer.CheckContext` and reads the view
matching its semantics: shape checks see asserted facts only, cardinality
checks additionally see mirror edges, and irreflexive/asymmetric see the full
derived layer including the transitive closure.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .graph import Graph, _most_specific
from .infer import CheckContext, EdgeKey, _edge_key, derive
from .schema import Entity, LoreError
from .store import AttrFact, EdgeFact, Fact, LayeredView, Source, TypeFact
from .verdict import Violation

if TYPE_CHECKING:
    from .compile import Guard

__all__ = ["ALL_CHECKS", "Graph", "ground", "run_checks"]


def ground(guard: "Guard", obj: Entity, source: Source) -> list[Fact]:
    """Typed instance → facts. Purely mechanical; subclass propagation happens
    here (one TypeFact per registered class in the MRO)."""
    compiled = guard.classes.get(type(obj).__name__)
    if compiled is None or compiled.cls is not type(obj):
        raise LoreError(
            f"{type(obj).__name__} is not registered with lore {guard.name!r}"
        )
    facts: list[Fact] = [TypeFact(obj.id, name, source) for name in compiled.ancestors]
    for field_name, rel in compiled.relations.items():
        value = getattr(obj, field_name)
        if value is None:
            continue
        if rel.many:
            # One edge per element, order-preserving dedupe so staged_facts
            # reads clean (the store would dedupe anyway); [] → zero edges.
            facts.extend(
                EdgeFact(obj.id, rel.predicate, object_id, source)
                for object_id in dict.fromkeys(value)
            )
        else:
            facts.append(EdgeFact(obj.id, rel.predicate, value, source))
    for field_name, attr in compiled.attributes.items():
        value = getattr(obj, field_name)
        if value is None:
            continue
        facts.append(AttrFact(obj.id, attr.attr, value, source))
    return facts


def _an(noun: str) -> str:
    return f"an {noun}" if noun[:1].lower() in "aeiou" else f"a {noun}"


def _mark(fact: Fact, staged: bool) -> str:
    """The status suffix a fact renders with in violation messages."""
    if staged:
        return "proposed"
    if fact.step == 0:
        return "seeded"
    return f"already committed at step {fact.step}"


def _base_path(ctx: CheckContext, edge: EdgeFact) -> tuple[EdgeFact, ...]:
    """The base facts behind ``edge`` — itself, unless it is derived."""
    derivation = ctx.derivations.provenance.get(_edge_key(edge))
    return derivation.base_path if derivation is not None else (edge,)


def _chain_nodes(start: str, path: tuple[EdgeFact, ...]) -> list[str]:
    """The nodes a base path visits, walking from ``start`` and orienting each
    base fact against the current node — a base fact asserted on the inverse
    predicate is stored flipped relative to the traversal direction."""
    nodes = [start]
    for edge in path:
        nodes.append(edge.object_id if edge.subject_id == nodes[-1] else edge.subject_id)
    return nodes


def _render_chain(start: str, path: tuple[EdgeFact, ...], staged_keys: set[EdgeKey]) -> str:
    """``a → b (already committed at step 1) → a (proposed)`` for a base path."""
    parts = [start]
    for node, edge in zip(_chain_nodes(start, path)[1:], path):
        parts.append(f"→ {node} ({_mark(edge, _edge_key(edge) in staged_keys)})")
    return " ".join(parts)


# --- the checks -------------------------------------------------------------


def check_existence(ctx: CheckContext) -> list[Violation]:
    """Every staged edge must point at a node that exists in the view.

    Committed edges cannot *newly* dangle (facts are append-only), so staged
    edges are exactly the ones worth checking.
    """
    out = []
    for fact in ctx.staged:
        if isinstance(fact, EdgeFact) and not ctx.view.has_node(fact.object_id):
            spec = ctx.guard.relations.get(fact.predicate)
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


def check_range(ctx: CheckContext) -> list[Violation]:
    """An edge's object must be typed as the relation's target class (subclass
    instances qualify automatically: grounding emits ancestor TypeFacts)."""
    out = []
    for predicate, spec in ctx.guard.relations.items():
        for edge in ctx.view.edges(predicate):
            if (
                _edge_key(edge) not in ctx.staged_edge_keys
                and edge.object_id not in ctx.staged_nodes
            ):
                continue
            types = ctx.view.types_of(edge.object_id)
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


def check_domain(ctx: CheckContext) -> list[Violation]:
    """An edge's subject must be typed as the class that declares the field.

    Grounding always types subjects correctly, so this is only violable via
    seeded raw facts — cheap, keep it.
    """
    out = []
    for predicate, spec in ctx.guard.relations.items():
        for edge in ctx.view.edges(predicate):
            if (
                _edge_key(edge) not in ctx.staged_edge_keys
                and edge.subject_id not in ctx.staged_nodes
            ):
                continue
            types = ctx.view.types_of(edge.subject_id)
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


def check_max_per_target(ctx: CheckContext) -> list[Violation]:
    """No target node may be pointed at by more than ``max_per_target`` subjects
    via the relation — mirror edges included, so an inverse pair is enforced
    whichever direction asserts the fact. The message names every subject
    involved and when it landed; that concreteness is what makes repair
    prompts work.
    """
    out = []
    for predicate, spec in ctx.guard.relations.items():
        if spec.max_per_target is None:
            continue
        groups: dict[str, list[EdgeFact]] = {}
        for edge in ctx.cardinality_view.edges(predicate):
            groups.setdefault(edge.object_id, []).append(edge)
        for object_id, edges in groups.items():
            if len(edges) <= spec.max_per_target:
                continue
            if not any(_edge_key(e) in ctx.staged_edge_keys for e in edges):
                continue  # pre-existing overrun, nothing staged made it worse
            prior = sorted(
                (e for e in edges if _edge_key(e) not in ctx.staged_edge_keys),
                key=lambda e: e.subject_id,
            )
            proposed = sorted(
                e.subject_id for e in edges if _edge_key(e) in ctx.staged_edge_keys
            )
            listing = ", ".join(
                [f"{e.subject_id} ({_mark(e, False)})" for e in prior]
                + [f"{s} (proposed)" for s in proposed]
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


def check_single_value(ctx: CheckContext) -> list[Violation]:
    """Scalar fields hold one value: a node holds at most one value per scalar
    relation and per attribute, and committed values are immutable. Fires when
    a proposal re-asserts a committed entity id with a changed value, or
    asserts two values for one id in a single proposal. For scalar relation
    fields this is what enforces OWL-functional ("at most one object per
    subject") across turns. Many (``Relation[list[X]]``) predicates are
    skipped: their facts accrete as a set, so multiple edges per subject are
    the point, not a violation.

    Symmetric mirrors count (so "B married to both A and C" is caught even
    when both facts were asserted from the other side); inverse mirrors do
    not — they are already counted on their home predicate, and counting them
    here would break one-to-many inverse pairs.
    """
    staged_attrs = [
        (f.subject_id, f.attr, f.value) for f in ctx.staged if isinstance(f, AttrFact)
    ]
    out = []
    for predicate, spec in ctx.guard.relations.items():
        if spec.many:
            continue
        by_subject: dict[str, list[EdgeFact]] = {}
        for edge in ctx.single_value_view.edges(predicate):
            by_subject.setdefault(edge.subject_id, []).append(edge)
        for subject_id, edges in by_subject.items():
            if len(edges) <= 1 or not any(
                _edge_key(e) in ctx.staged_edge_keys for e in edges
            ):
                continue
            prior = sorted(
                (e for e in edges if _edge_key(e) not in ctx.staged_edge_keys),
                key=lambda e: e.object_id,
            )
            proposed = sorted(
                e.object_id for e in edges if _edge_key(e) in ctx.staged_edge_keys
            )
            listing = ", ".join(
                [f"{e.object_id} ({_mark(e, False)})" for e in prior]
                + [f"{o} (proposed)" for o in proposed]
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
    for attr, spec in ctx.guard.attributes.items():
        by_node: dict[str, list[AttrFact]] = {}
        for fact in ctx.view.attrs(attr):
            by_node.setdefault(fact.subject_id, []).append(fact)
        for subject_id, facts in by_node.items():
            staged_mask = [(f.subject_id, f.attr, f.value) in staged_attrs for f in facts]
            if len(facts) <= 1 or not any(staged_mask):
                continue
            listing = ", ".join(
                [f"{f.value!r} ({_mark(f, False)})" for f, s in zip(facts, staged_mask) if not s]
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


def check_one_of(ctx: CheckContext) -> list[Violation]:
    """Staged attribute values must be in the field's allowed set."""
    out = []
    for fact in ctx.staged:
        if not isinstance(fact, AttrFact):
            continue
        spec = ctx.guard.attributes.get(fact.attr)
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


def check_disjoint(ctx: CheckContext) -> list[Violation]:
    """No node may be typed as both classes of a disjoint pair. Only nodes that
    gained a staged TypeFact can newly violate this."""
    out = []
    for node_id in sorted(ctx.staged_nodes):
        types = ctx.view.types_of(node_id)
        for a, b in ctx.guard.disjoint_pairs:
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


def check_type_coherence(ctx: CheckContext) -> list[Violation]:
    """A node's types must form one ancestry chain. Entities rehydrate as a
    single most-specific class, so an id typed under two *incomparable*
    branches — sibling subclasses, or unrelated classes — is a state the
    entity layer cannot represent, and a rule targeting the unpicked branch
    could never run against it (a silent enforcement bypass). Reject the
    proposal instead; the agent's repair is a distinct id or the right class.
    Declared-disjoint pairs are the disjoint check's job. Only nodes that
    gained a staged TypeFact can newly violate this.
    """
    out = []
    for node_id in sorted(ctx.staged_nodes):
        types = ctx.view.types_of(node_id)
        if len(types) < 2:
            continue
        maximal = [
            t
            for t in sorted(types)
            if not any(u != t and t in ctx.guard.classes[u].ancestors for u in types)
        ]
        for i, a in enumerate(maximal):
            for b in maximal[i + 1 :]:
                if (a, b) in ctx.guard.disjoint_pairs:
                    continue
                out.append(
                    Violation(
                        check="type_coherence",
                        severity="reject",
                        message=(
                            f"{node_id} cannot be both {_an(a)} and {_an(b)}: neither "
                            "is a kind of the other, so no single entity can carry "
                            "both — use a distinct id."
                        ),
                        subjects=(node_id,),
                    )
                )
    return out


def check_irreflexive(ctx: CheckContext) -> list[Violation]:
    """No staged-involved self-edge — asserted or derived — on an irreflexive
    predicate. On a transitive predicate this is cycle detection: a proposed
    edge that closes a loop derives one self-edge per node on the cycle, so
    self-edges are deduped by their base-fact set (one cycle = one violation)
    and the message renders the exact chain.
    """
    out = []
    for predicate, spec in ctx.guard.relations.items():
        if not spec.irreflexive:
            continue
        groups: dict[frozenset[EdgeKey], list[tuple[EdgeFact, tuple[EdgeFact, ...]]]] = {}
        for edge in ctx.relational_view.edges(predicate):
            if edge.subject_id != edge.object_id:
                continue
            if _edge_key(edge) not in ctx.staged_edge_keys:
                continue
            path = _base_path(ctx, edge)
            groups.setdefault(frozenset(_edge_key(b) for b in path), []).append((edge, path))
        for candidates in groups.values():
            # Prefer the rotation whose chain ends on a staged edge ("… → X
            # (proposed)" reads as the proposal closing the loop); node id
            # breaks ties deterministically.
            candidates.sort(
                key=lambda item: (
                    _edge_key(item[1][-1]) not in ctx.staged_edge_keys,
                    item[0].subject_id,
                )
            )
            edge, path = candidates[0]
            if len(path) == 1:
                message = f"{edge.subject_id} cannot point at itself via '{spec.field}'."
                subjects: tuple[str, ...] = (edge.subject_id,)
            else:
                chain = _render_chain(edge.subject_id, path, ctx.staged_edge_keys)
                message = (
                    f"{edge.subject_id} cannot reach itself via '{spec.field}', but "
                    f"this proposal creates a cycle: {chain}."
                )
                subjects = tuple(_chain_nodes(edge.subject_id, path)[:-1])
            out.append(
                Violation(
                    check="irreflexive",
                    severity=spec.severity,
                    message=message,
                    subjects=subjects,
                    provenance=tuple(path),
                )
            )
    return out


def check_asymmetric(ctx: CheckContext) -> list[Violation]:
    """A staged-involved edge A→B on an asymmetric predicate where B→A is also
    visible (either may be asserted or derived). Canonical pair ordering, so
    one violation per pair; self-edges are the irreflexive check's job
    (``asymmetric`` implies ``irreflexive`` at compile)."""
    out = []
    for predicate, spec in ctx.guard.relations.items():
        if not spec.asymmetric:
            continue
        edges = {
            (e.subject_id, e.object_id): e for e in ctx.relational_view.edges(predicate)
        }
        for (a, b), forward in edges.items():
            if a >= b:  # canonical ordering also skips self-edges
                continue
            reverse = edges.get((b, a))
            if reverse is None:
                continue
            if (
                _edge_key(forward) not in ctx.staged_edge_keys
                and _edge_key(reverse) not in ctx.staged_edge_keys
            ):
                continue
            forward_mark = _mark(forward, _edge_key(forward) in ctx.staged_edge_keys)
            reverse_mark = _mark(reverse, _edge_key(reverse) in ctx.staged_edge_keys)
            out.append(
                Violation(
                    check="asymmetric",
                    severity=spec.severity,
                    message=(
                        f"'{spec.field}' cannot go both ways, but {a} → {b} "
                        f"({forward_mark}) and {b} → {a} ({reverse_mark}) are "
                        "both present."
                    ),
                    subjects=(a, b),
                    provenance=tuple(_base_path(ctx, forward) + _base_path(ctx, reverse)),
                )
            )
    return out


def check_rules(ctx: CheckContext) -> list[Violation]:
    """Run ``@lore.rule`` predicates on every instance of each rule's target
    class in the composed view, in two reporting regimes:

    - Nodes in ``ctx.rule_nodes`` — the subjects of staged facts (re-asserting
      a committed id with changed fields must re-run its rules) plus every
      node one asserted hop away in either direction — report every failure:
      the proposal asserts something about them or their direct neighborhood,
      so a failure is the proposal's business even if it predates it.
    - Every other committed instance is checked **differentially**: a failure
      is reported only if the rule passed on the pre-proposal baseline
      (``ctx.base``) — i.e. the staged facts newly broke it. Committed facts
      are immutable, so silently letting a satisfied rule flip would leave the
      world unrepairable; the flip is rejected at proposal time instead, and
      pre-existing failures (e.g. a world seeded with ``validate_seed=False``)
      never re-fire against unrelated proposals. This also removes the old
      monotonicity requirement on rules that read beyond one hop.

    The graph handed to rules rehydrates from asserted facts only, but
    ``graph.reachable()`` sees mirrors and the transitive closure; the
    baseline graph gets its own derivations over the committed store.
    """
    derivations = ctx.derivations
    graph = Graph(
        ctx.guard,
        ctx.view,
        mirrors=LayeredView(derivations.sym_mirrors, derivations.inv_mirrors),
        closure=derivations.closure,
    )
    baseline: Graph | None = None

    def baseline_graph() -> Graph:
        # Built lazily: only needed when a node outside the proposal's
        # neighborhood fails, which is the rare path.
        nonlocal baseline
        if baseline is None:
            derived = derive(ctx.guard, ctx.base)
            baseline = Graph(
                ctx.guard,
                ctx.base,
                mirrors=LayeredView(derived.sym_mirrors, derived.inv_mirrors),
                closure=derived.closure,
            )
        return baseline

    out = []
    for rule in ctx.guard.rules:
        target_cls = ctx.guard.classes[rule.target].cls
        for node_id in sorted(ctx.view.nodes_of(rule.target)):
            obj = graph.get(node_id)
            # A node typed under two incomparable branches rehydrates as only
            # one of them; a rule targeting the other branch would receive an
            # object missing its fields. Proposals that create such a node are
            # rejected by check_type_coherence — this guard only keeps rules
            # from crashing on worlds that already contain one (seeded with
            # validate_seed=False, or restored from a snapshot).
            if obj is None or not isinstance(obj, target_cls):
                continue
            if rule.fn(obj, graph):
                continue
            if node_id not in ctx.rule_nodes:
                base_obj = baseline_graph().get(node_id)
                if (
                    base_obj is None
                    or not isinstance(base_obj, target_cls)
                    or not rule.fn(base_obj, baseline_graph())
                ):
                    continue  # failing before the proposal too — not newly broken
                message = (
                    rule.message.format(obj=obj)
                    + " This rule held before this proposal — the proposed facts"
                    " would break it."
                )
            else:
                message = rule.message.format(obj=obj)
            out.append(
                Violation(
                    check=f"rule:{rule.name}",
                    severity=rule.severity,
                    message=message,
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
    check_type_coherence,
    check_irreflexive,
    check_asymmetric,
    check_rules,
)


def run_checks(ctx: CheckContext) -> list[Violation]:
    violations: list[Violation] = []
    for check in ALL_CHECKS:
        violations.extend(check(ctx))
    return violations
