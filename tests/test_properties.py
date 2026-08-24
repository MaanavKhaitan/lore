"""Hypothesis property tests."""

from hypothesis import given
from hypothesis import strategies as st

from ontic import Entity, Ontology, Relation, relation
from ontic.engine import Graph, ground
from ontic.store import InMemoryStore

# --- (a) max_per_target=1 fires exactly once iff more than one subject --------

ont_a = Ontology("prop-max")


@ont_a.entity
class Order(Entity):
    pass


@ont_a.entity
class Refund(Entity):
    refunds: Relation[Order] = relation(max_per_target=1)


guard_a = ont_a.compile()

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

ont_b = Ontology("prop-roundtrip")


@ont_b.entity
class Other(Entity):
    pass


@ont_b.entity
class Thing(Entity):
    name: str
    count: int
    score: float
    linked: Relation[Other] | None = None


guard_b = ont_b.compile()

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
