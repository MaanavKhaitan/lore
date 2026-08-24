"""Table-driven per-axiom cases: one pass and one fail case per check, plus the
cross-turn, subclass, disjoint, atomicity, and severity scenarios."""

import pytest

from ontic import Entity, Ontology, Relation, one_of, relation
from ontic.store import EdgeFact

ont = Ontology("commerce-test")


@ont.entity
class Customer(Entity):
    pass


@ont.entity
class VIPCustomer(Customer):
    pass


@ont.entity
class SupportRep(Entity):
    ontic_disjoint_with = [Customer]


@ont.entity
class Order(Entity):
    status: str = one_of("paid", "shipped", "refunded")
    channel: str = one_of("web", "store", severity="flag")
    total: float
    placed_by: Relation[Customer]


@ont.entity
class Refund(Entity):
    amount: float
    refunds: Relation[Order] = relation(max_per_target=1)
    paid_to: Relation[Customer]


@ont.rule(message="Refund {obj.id} of {obj.amount} exceeds the total of order {obj.refunds}.")
def refund_within_total(refund: Refund, graph) -> bool:
    order = graph.get(refund.refunds)
    return order is None or refund.amount <= order.total


guard = ont.compile()


def order(oid: str, *, status="paid", channel="web", total=100.0, placed_by="cust_1") -> Order:
    return Order(id=oid, status=status, channel=channel, total=total, placed_by=placed_by)


def refund(rid: str, *, amount=10.0, refunds="ord_1", paid_to="cust_1") -> Refund:
    return Refund(id=rid, amount=amount, refunds=refunds, paid_to=paid_to)


def make_session(committed=()):
    session = guard.session(
        seed=[
            Customer(id="cust_1"),
            VIPCustomer(id="vip_1"),
            SupportRep(id="rep_1"),
            order("ord_1", total=100.0),
            order("ord_2", total=250.0),
        ]
    )
    for batch in committed:
        verdict = session.propose(*batch)
        assert verdict.ok, f"committed setup batch failed: {verdict.violations}"
        session.commit()
    return session


CASES = [
    # (name, committed batches, proposal, expected {(check, subjects)}, verdict.ok)
    ("valid-refund", [], [refund("ref_a")], set(), True),
    (
        "existence-fail",
        [],
        [refund("ref_a", refunds="ghost")],
        {("existence", ("ref_a", "ghost"))},
        False,
    ),
    (
        "range-fail-rep-payout",
        [],
        [refund("ref_a", paid_to="rep_1")],
        {("range", ("ref_a", "rep_1"))},
        False,
    ),
    ("range-subclass-pass", [], [refund("ref_a", paid_to="vip_1")], set(), True),
    (
        "max-per-target-same-proposal",
        [],
        [refund("ref_a"), refund("ref_b")],
        {("max_per_target", ("ord_1", "ref_a", "ref_b"))},
        False,
    ),
    (
        "max-per-target-cross-turn",
        [[refund("ref_a")]],
        [refund("ref_b")],
        {("max_per_target", ("ord_1", "ref_a", "ref_b"))},
        False,
    ),
    (
        "one-of-fail",
        [],
        [order("ord_x", status="cancelled")],
        {("one_of", ("ord_x",))},
        False,
    ),
    (
        "disjoint-fail",
        [],
        [Customer(id="rep_1")],
        {("disjoint", ("rep_1",))},
        False,
    ),
    (
        "multi-object-atomic-one-bad",
        [],
        [order("ord_9"), refund("ref_a", refunds="ghost")],
        {("existence", ("ref_a", "ghost"))},
        False,
    ),
    (
        "flag-severity-ok-but-flagged",
        [],
        [order("ord_x", channel="phone")],
        {("one_of", ("ord_x",))},
        True,
    ),
    (
        "rule-fail-amount-over-total",
        [],
        [refund("ref_a", amount=250.0)],
        {("rule:refund_within_total", ("ref_a",))},
        False,
    ),
    ("rule-pass", [], [refund("ref_a", amount=50.0)], set(), True),
    (
        # Committed values are immutable: changing ref_a's amount is a
        # single_value violation, and rules re-run against the proposed value.
        "single-value-attr-change-on-committed",
        [[refund("ref_a", amount=50.0)]],
        [refund("ref_a", amount=250.0)],
        {("single_value", ("ref_a",)), ("rule:refund_within_total", ("ref_a",))},
        False,
    ),
    (
        # Retargeting a committed refund at a second order would let one refund
        # pay out against two orders — scalar relations stay functional cross-turn.
        "single-value-edge-retarget-on-committed",
        [[refund("ref_a")]],
        [refund("ref_a", refunds="ord_2")],
        {("single_value", ("ref_a", "ord_1", "ord_2"))},
        False,
    ),
    (
        "single-value-same-proposal-id-collision",
        [],
        [refund("ref_a", amount=10.0), refund("ref_a", amount=20.0)],
        {("single_value", ("ref_a",))},
        False,
    ),
]


@pytest.mark.parametrize("name,committed,proposal,expected,ok", CASES, ids=[c[0] for c in CASES])
def test_check(name, committed, proposal, expected, ok):
    session = make_session(committed)
    verdict = session.propose(*proposal)
    assert {(v.check, v.subjects) for v in verdict.violations} == expected
    assert verdict.ok is ok


def test_max_per_target_message_names_prior_committed_subject():
    session = make_session(committed=[[refund("ref_a")]])
    verdict = session.propose(refund("ref_b"))
    (violation,) = verdict.violations
    assert "ord_1" in violation.message
    assert "ref_a (already committed)" in violation.message
    assert "ref_b (proposed)" in violation.message


def test_single_value_message_names_committed_and_proposed_values():
    session = make_session(committed=[[refund("ref_a", amount=50.0)]])
    verdict = session.propose(refund("ref_a", amount=250.0))
    [violation] = [v for v in verdict.violations if v.check == "single_value"]
    assert "Refund.amount" in violation.message
    assert "50.0 (already committed)" in violation.message
    assert "250.0 (proposed)" in violation.message


def test_rules_rerun_when_staged_facts_touch_committed_nodes():
    # Setting a previously-unset field on a committed entity stages no TypeFact
    # (those dedupe away), but the entity's rules must still re-run.
    ont2 = Ontology("rerun-test")

    @ont2.entity
    class Doc(Entity):
        state: str | None = None

    @ont2.rule(message="Doc {obj.id} may not be archived.")
    def not_archived(doc: Doc, graph) -> bool:
        return doc.state != "archived"

    session = ont2.compile().session()
    assert session.propose(Doc(id="d1")).ok
    session.commit()
    verdict = session.propose(Doc(id="d1", state="archived"))
    assert {(v.check, v.subjects) for v in verdict.violations} == {("rule:not_archived", ("d1",))}


def test_flag_severity_lands_in_flags_not_rejects():
    session = make_session()
    verdict = session.propose(order("ord_x", channel="phone"))
    assert verdict.ok
    assert [v.check for v in verdict.flags] == ["one_of"]
    assert verdict.rejects == []
    session.commit()  # flags are committable


def test_domain_violation_via_seeded_raw_facts():
    # Grounding always types edge subjects correctly, so domain is only violable
    # by seeding a raw edge whose subject later gets typed as something else.
    session = make_session()
    session._committed.add([EdgeFact("imposter", "Refund.refunds", "ord_1", "seed")])
    verdict = session.propose(Customer(id="imposter"))
    assert {(v.check, v.subjects) for v in verdict.violations} == {("domain", ("imposter",))}


def test_seed_inconsistencies_do_not_refire_on_unrelated_proposals():
    # A pre-existing (seeded) dangling edge must not pollute later verdicts.
    session = make_session()
    session._committed.add([EdgeFact("ref_seeded", "Refund.refunds", "ghost", "seed")])
    verdict = session.propose(refund("ref_a", refunds="ord_2"))
    assert verdict.ok and verdict.violations == []
