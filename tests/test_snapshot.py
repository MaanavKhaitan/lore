"""Durable sessions: snapshot/restore round-trips, the lore fingerprint, and
the refuse-loudly paths (pending proposal, changed lore, malformed blobs)."""

import json
from datetime import datetime, timezone

import pytest
from hypothesis import given
from hypothesis import strategies as st

from lore import Entity, Lore, LoreError, Relation, Session, one_of, relation

lore = Lore("snapshot-test")


@lore.entity
class Customer(Entity):
    name: str


@lore.entity
class Order(Entity):
    status: str = one_of("paid", "shipped", "refunded")
    placed_at: datetime
    placed_by: Relation[Customer]


@lore.entity
class Refund(Entity):
    amount: float
    refunds: Relation[Order] = relation(max_per_target=1)


guard = lore.compile()

PLACED_AT = datetime(2026, 8, 24, 12, 30, tzinfo=timezone.utc)


def seeded_session():
    return guard.session(
        seed=[
            Customer(id="cust_1", name="Ada"),
            Order(id="ord_1", status="paid", placed_at=PLACED_AT, placed_by="cust_1"),
        ]
    )


# --- round-trip fidelity --------------------------------------------------------


def test_round_trip_preserves_facts_sources_and_dump():
    session = seeded_session()
    assert session.try_commit(Refund(id="ref_1", amount=40.0, refunds="ord_1")).ok
    restored = Session.restore(guard, session.snapshot())
    assert restored.facts == session.facts  # same facts, same order, same sources
    assert "cust_1: Customer (seed)" in restored.dump()
    # restore is a fixed point: once values are canonicalized through their
    # annotations (e.g. tzinfo implementations), further round-trips are exact
    again = guard.restore(restored.snapshot())
    assert again.snapshot() == restored.snapshot()
    assert again.dump() == restored.dump()


def test_restored_session_enforces_cross_turn_rules():
    session = seeded_session()
    session.try_commit(Refund(id="ref_1", amount=40.0, refunds="ord_1"))
    restored = guard.restore(session.snapshot())
    verdict = restored.propose(Refund(id="ref_2", amount=40.0, refunds="ord_1"))
    assert not verdict.ok
    assert "ref_1 (already committed at step 1)" in verdict.rejects[0].message
    # the verdict is exactly what the pre-snapshot session would have produced
    assert verdict.violations == session.check(Refund(id="ref_2", amount=40.0, refunds="ord_1")).violations


def test_round_trip_preserves_commit_steps_and_resumes_the_counter():
    session = seeded_session()
    session.try_commit(Refund(id="ref_1", amount=40.0, refunds="ord_1"))
    restored = guard.restore(session.snapshot())
    # Fact equality ignores step (compare=False), so check the stamps directly.
    assert [f.step for f in restored.facts] == [f.step for f in session.facts]
    assert {f.step for f in restored.facts} == {0, 1}  # seeds at 0, the commit at 1
    assert restored._step == 1  # the next commit is step 2, not a reused step 1


def test_typed_attribute_values_survive_the_round_trip():
    session = seeded_session()
    session.propose(Refund(id="ref_1", amount=40.0, refunds="ord_1"))
    session.commit()
    restored = guard.restore(session.snapshot())
    order = restored.graph.get("ord_1")
    assert order.placed_at == PLACED_AT and isinstance(order.placed_at, datetime)
    assert restored.graph.get("ref_1").amount == 40.0


def test_empty_session_round_trips():
    restored = guard.restore(guard.session().snapshot())
    assert restored.facts == ()


def test_restore_accepts_bytes():
    session = seeded_session()
    restored = guard.restore(session.snapshot().encode())
    assert restored.facts == session.facts


def test_newest_wins_hydration_order_survives_restore():
    soft = Lore("snapshot-soft")

    @soft.entity
    class Node(Entity):
        pass

    @soft.entity
    class Pointer(Entity):
        to: Relation[Node] = relation(severity="flag")  # flags commit, so two values can land

    soft_guard = soft.compile()
    session = soft_guard.session(seed=[Node(id="n1"), Node(id="n2")])
    assert session.try_commit(Pointer(id="p1", to="n1")).ok
    assert session.try_commit(Pointer(id="p1", to="n2")).ok  # flagged, committed anyway
    restored = soft_guard.restore(session.snapshot())
    assert restored.graph.get("p1").to == session.graph.get("p1").to == "n2"


def test_snapshot_is_stamped_compact_json():
    session = seeded_session()
    session.propose(Refund(id="ref_1", amount=40.0, refunds="ord_1"))
    session.commit()
    data = json.loads(session.snapshot())
    assert data["format"] == 1
    assert data["lore"] == "snapshot-test"
    assert data["fingerprint"] == guard.fingerprint
    sources = {f["source"] for f in data["facts"]}
    assert sources == {"seed", "asserted"}


# --- refuse-loudly paths --------------------------------------------------------


def test_snapshot_with_pending_proposal_raises():
    session = seeded_session()
    session.propose(Refund(id="ref_1", amount=40.0, refunds="ord_1"))
    with pytest.raises(LoreError, match="pending proposal"):
        session.snapshot()
    session.rollback()
    session.snapshot()  # fine at a turn boundary


def test_restore_refuses_a_changed_lore():
    blob = seeded_session().snapshot()
    changed = Lore("snapshot-test")  # same name, one option differs

    @changed.entity
    class Customer(Entity):
        name: str

    @changed.entity
    class Order(Entity):
        status: str = one_of("paid", "shipped", "refunded")
        placed_at: datetime
        placed_by: Relation[Customer]

    @changed.entity
    class Refund(Entity):
        amount: float
        refunds: Relation[Order] = relation(max_per_target=2)

    with pytest.raises(LoreError, match="lore has changed"):
        changed.compile().restore(blob)


def test_restore_refuses_a_changed_field_type():
    # attr values round-trip through their annotations; without this refusal a
    # snapshotted datetime would silently restore as its ISO string
    blob = seeded_session().snapshot()
    changed = Lore("snapshot-test")

    @changed.entity
    class Customer(Entity):
        name: str

    @changed.entity
    class Order(Entity):
        status: str = one_of("paid", "shipped", "refunded")
        placed_at: str  # was datetime
        placed_by: Relation[Customer]

    @changed.entity
    class Refund(Entity):
        amount: float
        refunds: Relation[Order] = relation(max_per_target=1)

    with pytest.raises(LoreError, match="lore has changed"):
        changed.compile().restore(blob)


def test_restore_refuses_garbage():
    for blob in ["{not json", "[]", '{"format": 1}', '{"format": 99, "facts": []}']:
        with pytest.raises(LoreError):
            guard.restore(blob)


def test_restore_refuses_tampered_facts():
    session = seeded_session()
    data = json.loads(session.snapshot())
    for bad in [
        {"kind": "type", "node": "x", "class": "Ghost", "source": "seed"},
        {"kind": "edge", "subject": "x", "predicate": "Ghost.to", "object": "y", "source": "seed"},
        {"kind": "attr", "subject": "x", "attr": "Ghost.v", "value": 1, "source": "seed"},
        {"kind": "type", "node": "x", "class": "Customer", "source": "divine"},
        {"kind": "wormhole", "source": "seed"},
        "not-a-fact",
    ]:
        tampered = dict(data, facts=data["facts"] + [bad])
        with pytest.raises(LoreError):
            guard.restore(json.dumps(tampered))


# --- the fingerprint ------------------------------------------------------------


def _build_twin():
    twin = Lore("snapshot-test")

    @twin.entity
    class Customer(Entity):
        name: str

    @twin.entity
    class Order(Entity):
        status: str = one_of("paid", "shipped", "refunded")
        placed_at: datetime
        placed_by: Relation[Customer]

    @twin.entity
    class Refund(Entity):
        amount: float
        refunds: Relation[Order] = relation(max_per_target=1)

    return twin.compile()


def test_fingerprint_is_declarative_not_identity_based():
    # an identical lore defined in a fresh process must accept old snapshots
    twin = _build_twin()
    assert twin.fingerprint == guard.fingerprint
    session = seeded_session()
    session.try_commit(Refund(id="ref_1", amount=40.0, refunds="ord_1"))
    assert twin.restore(session.snapshot()).facts == session.facts


def test_fingerprint_changes_with_semantics():
    def base():
        v = Lore("snapshot-test")

        @v.entity
        class Customer(Entity):
            name: str

        return v, Customer

    fingerprints = []

    v, _ = base()  # the baseline
    fingerprints.append(v.compile().fingerprint)

    v, _ = base()  # a new class

    @v.entity
    class Extra(Entity):
        pass

    fingerprints.append(v.compile().fingerprint)

    v, Customer = base()  # a new rule registration

    @v.rule(message="never {obj.id}")
    def always_fine(obj: Customer, graph) -> bool:
        return True

    fingerprints.append(v.compile().fingerprint)

    v = Lore("snapshot-test")  # an attribute option + severity change

    @v.entity
    class Customer(Entity):
        name: str = one_of("Ada", severity="flag")

    fingerprints.append(v.compile().fingerprint)

    v = Lore("snapshot-test")  # a field type change — values round-trip
    # through the annotation, so this must invalidate old snapshots

    @v.entity
    class Customer(Entity):
        name: int

    fingerprints.append(v.compile().fingerprint)

    v = Lore("another-name")  # the lore's name itself

    @v.entity
    class Customer(Entity):
        name: str

    fingerprints.append(v.compile().fingerprint)

    assert len(set(fingerprints)) == len(fingerprints)


# --- property: arbitrary scalars round-trip exactly ------------------------------

prop = Lore("snapshot-prop")


@prop.entity
class Note(Entity):
    value: str | int | float | bool | None = None


prop_guard = prop.compile()

scalars = st.one_of(
    st.text(),
    st.integers(),
    st.floats(allow_nan=False, allow_infinity=False),
    st.booleans(),
    st.none(),
)


@given(st.dictionaries(st.text(min_size=1), scalars, max_size=8))
def test_scalar_values_round_trip_exactly(values):
    session = prop_guard.session()
    notes = [Note(id=node_id, value=value) for node_id, value in values.items()]
    if notes:
        assert session.try_commit(*notes).ok
    restored = prop_guard.restore(session.snapshot())
    assert restored.facts == session.facts
    assert restored.dump() == session.dump()


# --- multi-valued relations: EdgeFacts serialize individually, so a many-edged
# session must round-trip with no codec changes.

many = Lore("snapshot-many")


@many.entity
class Tag(Entity):
    pass


@many.entity
class Doc(Entity):
    tags: Relation[list[Tag]] = relation(default=[])


many_guard = many.compile()


def test_many_edged_session_round_trips_identically():
    session = many_guard.session(seed=[Tag(id="t1"), Tag(id="t2"), Tag(id="t3")])
    assert session.try_commit(Doc(id="d1", tags=["t2", "t1"])).ok
    assert session.try_commit(Doc(id="d1", tags=["t2", "t1", "t3"])).ok  # accretion at step 2
    restored = many_guard.restore(session.snapshot())
    assert restored.facts == session.facts
    assert [f.step for f in restored.facts] == [f.step for f in session.facts]
    assert restored.dump() == session.dump()
    assert restored.graph.get("d1").tags == session.graph.get("d1").tags == ["t2", "t1", "t3"]


# --- revalidate=True: the safety net for edited rule bodies --------------------
# Rule bodies are not fingerprinted, so a snapshot taken under one
# implementation restores silently under another. revalidate=True re-runs
# every check over the restored world under the *current* rules.


def _cap_lore(max_amount: float) -> Lore:
    lore2 = Lore("reval-test")

    @lore2.entity
    class Payment(Entity):
        amount: float

    @lore2.rule(message="Payment {obj.id} of {obj.amount} exceeds the cap.")
    def within_cap(payment: Payment, graph) -> bool:
        return payment.amount <= max_amount

    return lore2


def test_restore_revalidates_under_current_rules():
    loose = _cap_lore(100.0).compile()
    session = loose.session()
    session.try_commit(loose.classes["Payment"].cls(id="p1", amount=50.0))
    blob = session.snapshot()

    strict = _cap_lore(10.0).compile()
    assert strict.fingerprint == loose.fingerprint  # bodies aren't hashed
    strict.restore(blob)  # default: trusted as-is
    with pytest.raises(LoreError, match="invalid under the current rules"):
        strict.restore(blob, revalidate=True)


def test_restore_revalidate_passes_on_a_consistent_world():
    guard2 = _cap_lore(100.0).compile()
    session = guard2.session()
    session.try_commit(guard2.classes["Payment"].cls(id="p1", amount=50.0))
    restored = guard2.restore(session.snapshot(), revalidate=True)
    assert restored.facts == session.facts
