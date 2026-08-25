"""Hypothesis property tests."""

from hypothesis import given
from hypothesis import strategies as st

from lore import Entity, Lore, Relation, relation
from lore.engine import Graph, ground
from lore.store import InMemoryStore

# --- (a) max_per_target=1 fires exactly once iff more than one subject --------

lore_a = Lore("prop-max")


@lore_a.entity
class Order(Entity):
    pass


@lore_a.entity
class Refund(Entity):
    refunds: Relation[Order] = relation(max_per_target=1)


guard_a = lore_a.compile()

refund_ids = st.lists(
    st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=8),
    min_size=1,
    max_size=6,
    unique=True,
)


@given(refund_ids)
def test_max_per_target_fires_exactly_once_iff_multiple_subjects(ids):
    session = guard_a.session(seed=[Order(id="ord_1")])
    verdict = session.propose(*[Refund(id=rid, refunds="ord_1") for rid in ids])
    max_violations = [v for v in verdict.violations if v.check == "max_per_target"]
    if len(ids) > 1:
        (violation,) = max_violations
        assert violation.subjects == ("ord_1", *sorted(ids))
        assert "ord_1" in violation.message
    else:
        assert verdict.violations == []


# --- (b) ground → hydrate round-trips field values ----------------------------

lore_b = Lore("prop-roundtrip")


@lore_b.entity
class Other(Entity):
    pass


@lore_b.entity
class Thing(Entity):
    name: str
    count: int
    score: float
    linked: Relation[Other] | None = None


guard_b = lore_b.compile()

things = st.builds(
    Thing,
    id=st.text(min_size=1),
    name=st.text(),
    count=st.integers(),
    score=st.floats(allow_nan=False),
    linked=st.none() | st.text(min_size=1),
)


@given(things)
def test_ground_then_hydrate_round_trips(thing):
    store = InMemoryStore()
    store.add(ground(guard_b, thing, "asserted"))
    rebuilt = Graph(guard_b, store).get(thing.id)
    assert rebuilt == thing


# --- (c) transitive+irreflexive on out-degree ≤ 1 graphs (what a scalar field
#         produces anyway): acyclic ⇒ no violation; one back-edge ⇒ exactly one

lore_c = Lore("prop-cycles")


@lore_c.entity
class Emp(Entity):
    boss: Relation["Emp"] | None = relation(transitive=True, irreflexive=True, default=None)


guard_c = lore_c.compile()

# parent_choices[i-1] % i ∈ [0, i-1]: every chain strictly descends to e0.
parent_choices = st.lists(st.integers(min_value=0, max_value=100), min_size=1, max_size=12)


def functional_forest(choices):
    emps = [Emp(id="e0")]
    for i, choice in enumerate(choices, start=1):
        emps.append(Emp(id=f"e{i}", boss=f"e{choice % i}"))
    return emps


@given(parent_choices)
def test_closure_of_functional_dag_has_no_irreflexive_violation(choices):
    verdict = guard_c.session().propose(*functional_forest(choices))
    assert verdict.ok and verdict.violations == []


@given(parent_choices, st.integers(min_value=0, max_value=100))
def test_one_back_edge_yields_exactly_one_irreflexive_violation(choices, back):
    emps = functional_forest(choices)
    # Point the root at any node: every chain reaches e0, so this closes
    # exactly one cycle (back == 0 degenerates to a direct self-edge).
    emps[0] = Emp(id="e0", boss=f"e{back % len(emps)}")
    verdict = guard_c.session().propose(*emps)
    assert [v.check for v in verdict.violations] == ["irreflexive"]


# --- (d) inverse pairs are direction-agnostic: flipping which side asserts
#         each edge leaves the (check, subjects) violation set invariant, up to
#         the scalar-field check that only the asserting side can trip

lore_d = Lore("prop-inverse")


@lore_d.entity
class Worker(Entity):
    badge: Relation["Tag"] | None = relation(max_per_target=1, inverse_of="holder", default=None)


@lore_d.entity
class Tag(Entity):
    holder: Relation[Worker] | None = relation(inverse_of="badge", default=None)


guard_d = lore_d.compile()


@given(st.lists(st.booleans(), min_size=2, max_size=7))
def test_inverse_pair_direction_agnosticism(from_tag_side):
    # A perfect matching w_i—t_i plus one conflicting pair (w_n, t_0); each
    # pair is asserted from whichever side its boolean picks.
    n = len(from_tag_side) - 1
    pairs = [(f"w_{i}", f"t_{i}") for i in range(n)] + [(f"w_{n}", "t_0")]
    session = guard_d.session(
        seed=[Worker(id=f"w_{i}") for i in range(n + 1)] + [Tag(id=f"t_{i}") for i in range(n)]
    )
    verdict = session.propose(
        *[
            Tag(id=t, holder=w) if tag_side else Worker(id=w, badge=t)
            for (w, t), tag_side in zip(pairs, from_tag_side)
        ]
    )
    expected = {("max_per_target", ("t_0", "w_0", f"w_{n}"))}
    if from_tag_side[0] and from_tag_side[n]:
        # Both conflicting facts asserted from the tag side: t_0's scalar
        # 'holder' field genuinely holds two values — direction-sensitive by
        # design, on top of the direction-agnostic cardinality catch.
        expected.add(("single_value", ("t_0", "w_0", f"w_{n}")))
    assert {(v.check, v.subjects) for v in verdict.violations} == expected


# --- (e) many-relation ground → hydrate: order-preserving set semantics --------

lore_e = Lore("prop-many")


@lore_e.entity
class Label(Entity):
    pass


@lore_e.entity
class Doc(Entity):
    tags: Relation[list[Label]] = relation(default=[])


guard_e = lore_e.compile()


@given(st.lists(st.text(min_size=1, max_size=6), max_size=10))
def test_many_ground_then_hydrate_dedupes_preserving_order(tags):
    store = InMemoryStore()
    store.add(ground(guard_e, Doc(id="d", tags=tags), "asserted"))
    rebuilt = Graph(guard_e, store).get("d")
    assert rebuilt.tags == list(dict.fromkeys(tags))
