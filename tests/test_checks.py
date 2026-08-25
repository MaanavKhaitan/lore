"""Table-driven per-axiom cases: one pass and one fail case per check, plus the
cross-turn, subclass, disjoint, atomicity, and severity scenarios."""

import pytest

from lore import Entity, Lore, Relation, one_of, relation
from lore.store import EdgeFact

lore = Lore("commerce-test")


@lore.entity
class Customer(Entity):
    # One-sided inverse declaration: completed against Order.placed_by. The
    # seeds give cust_1 several orders, so this also exercises "inverse
    # mirrors don't fire single_value on the many side".
    placed: Relation["Order"] | None = relation(inverse_of="placed_by", default=None)


@lore.entity
class VIPCustomer(Customer):
    pass


@lore.entity
class SupportRep(Entity):
    lore_disjoint_with = [Customer]


@lore.entity
class Order(Entity):
    status: str = one_of("paid", "shipped", "refunded")
    channel: str = one_of("web", "store", severity="flag")
    total: float
    placed_by: Relation[Customer]


@lore.entity
class Refund(Entity):
    amount: float
    refunds: Relation[Order] = relation(max_per_target=1)
    paid_to: Relation[Customer]


@lore.entity
class Employee(Entity):
    reports_to: Relation["Employee"] | None = relation(
        transitive=True, irreflexive=True, default=None
    )
    married_to: Relation["Employee"] | None = relation(symmetric=True, default=None)
    outranks: Relation["Employee"] | None = relation(asymmetric=True, default=None)
    badge: Relation["Badge"] | None = relation(
        max_per_target=1, inverse_of="holder", default=None
    )


@lore.entity
class Badge(Entity):
    holder: Relation[Employee] | None = relation(inverse_of="badge", default=None)


@lore.rule(message="Refund {obj.id} of {obj.amount} exceeds the total of order {obj.refunds}.")
def refund_within_total(refund: Refund, graph) -> bool:
    order = graph.get(refund.refunds)
    return order is None or refund.amount <= order.total


guard = lore.compile()


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
            Employee(id="emp_1"),
            Employee(id="emp_2"),
            Employee(id="emp_3"),
            Badge(id="b_1"),
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
    # --- inference-backed checks (transitive / symmetric / asymmetric / inverse)
    (
        "irreflexive-direct-self-edge",
        [],
        [Employee(id="emp_x", reports_to="emp_x")],
        {("irreflexive", ("emp_x",))},
        False,
    ),
    (
        "transitive-chain-without-cycle-passes",
        [],
        [Employee(id="emp_a", reports_to="emp_1"), Employee(id="emp_b", reports_to="emp_a")],
        set(),
        True,
    ),
    (
        # A 2-cycle derives a self-edge per node; dedupe by base-fact set means
        # one cycle = one violation.
        "irreflexive-cycle-one-violation",
        [[Employee(id="emp_b"), Employee(id="emp_a", reports_to="emp_b")]],
        [Employee(id="emp_b", reports_to="emp_a")],
        {("irreflexive", ("emp_a", "emp_b"))},
        False,
    ),
    (
        "irreflexive-three-node-cycle-one-violation",
        [
            [
                Employee(id="emp_c"),
                Employee(id="emp_b", reports_to="emp_c"),
                Employee(id="emp_a", reports_to="emp_b"),
            ]
        ],
        [Employee(id="emp_c", reports_to="emp_a")],
        {("irreflexive", ("emp_a", "emp_b", "emp_c"))},
        False,
    ),
    (
        "asymmetric-cross-turn",
        [[Employee(id="emp_1", outranks="emp_2")]],
        [Employee(id="emp_2", outranks="emp_1")],
        {("asymmetric", ("emp_1", "emp_2"))},
        False,
    ),
    (
        # asymmetric implies irreflexive at compile; the self-edge is the
        # irreflexive check's job and asymmetric must not double-fire.
        "asymmetric-self-edge-fires-irreflexive",
        [],
        [Employee(id="emp_x", outranks="emp_x")],
        {("irreflexive", ("emp_x",))},
        False,
    ),
    (
        # "emp_2 married to both emp_1 and emp_3", both facts asserted from the
        # other side — only visible through symmetric mirrors.
        "symmetric-single-value-cross-direction",
        [[Employee(id="emp_1", married_to="emp_2")]],
        [Employee(id="emp_3", married_to="emp_2")],
        {("single_value", ("emp_2", "emp_1", "emp_3"))},
        False,
    ),
    (
        # Asserting both directions of one symmetric fact is redundant, not a
        # violation: mirrors that duplicate asserted edges are suppressed.
        "symmetric-both-directions-asserted-ok",
        [[Employee(id="emp_1", married_to="emp_2")]],
        [Employee(id="emp_2", married_to="emp_1")],
        set(),
        True,
    ),
    ("inverse-pair-valid-assertion", [], [Employee(id="emp_1", badge="b_1")], set(), True),
    (
        # One-to-one inverse pair enforced cross-direction: the committed edge
        # was asserted as Employee.badge, the conflicting one as Badge.holder —
        # the only staged-involved edge in the overrun group is a mirror.
        "inverse-one-to-one-cross-direction-max-per-target",
        [[Employee(id="emp_1", badge="b_1")]],
        [Badge(id="b_1", holder="emp_2")],
        {("max_per_target", ("b_1", "emp_1", "emp_2"))},
        False,
    ),
    (
        # Inverse mirrors are NOT counted by single_value: cust_1's seeded
        # orders mirror onto Customer.placed, and a third must not trip the
        # scalar-field check on the many side.
        "inverse-one-to-many-no-false-single-value",
        [],
        [order("ord_x")],
        set(),
        True,
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
    assert "ref_a (already committed at step 1)" in violation.message
    assert "ref_b (proposed)" in violation.message


def test_single_value_message_names_committed_and_proposed_values():
    session = make_session(committed=[[refund("ref_a", amount=50.0)]])
    verdict = session.propose(refund("ref_a", amount=250.0))
    [violation] = [v for v in verdict.violations if v.check == "single_value"]
    assert "Refund.amount" in violation.message
    assert "50.0 (already committed at step 1)" in violation.message
    assert "250.0 (proposed)" in violation.message


def test_irreflexive_cycle_message_renders_provenance_chain_with_steps():
    session = make_session(
        committed=[[Employee(id="emp_b"), Employee(id="emp_a", reports_to="emp_b")]]
    )
    verdict = session.propose(Employee(id="emp_b", reports_to="emp_a"))
    (violation,) = verdict.violations
    assert violation.message == (
        "emp_a cannot reach itself via 'reports_to', but this proposal creates a "
        "cycle: emp_a → emp_b (already committed at step 1) → emp_a (proposed)."
    )
    # Provenance carries the base facts of the cycle, in chain order.
    assert [(f.subject_id, f.object_id) for f in violation.provenance] == [
        ("emp_a", "emp_b"),
        ("emp_b", "emp_a"),
    ]


def test_irreflexive_cycle_chain_orients_inverse_asserted_edges():
    # A cycle closed from the inverse side: 'b manages a' is stored in its
    # asserted orientation (b→a on Emp.manages) but the boss chain traverses
    # it a→b — the rendered chain must still close the loop back to b.
    hr = Lore("irreflexive-inverse-chain")

    @hr.entity
    class Emp(Entity):
        boss: Relation["Emp"] | None = relation(transitive=True, irreflexive=True, default=None)
        manages: Relation["Emp"] | None = relation(inverse_of="boss", default=None)

    session = hr.compile().session(seed=[Emp(id="a")])
    session.try_commit(Emp(id="b", boss="a"))
    verdict = session.propose(Emp(id="b", manages="a"))
    (violation,) = verdict.violations
    assert violation.message == (
        "b cannot reach itself via 'boss', but this proposal creates a "
        "cycle: b → a (already committed at step 1) → b (proposed)."
    )
    assert violation.subjects == ("b", "a")


def test_inverse_cross_direction_message_names_both_subjects_with_steps():
    session = make_session(committed=[[Employee(id="emp_1", badge="b_1")]])
    verdict = session.propose(Badge(id="b_1", holder="emp_2"))
    (violation,) = verdict.violations
    assert "b_1 has 2: emp_1 (already committed at step 1), emp_2 (proposed)" in (
        violation.message
    )


def test_rules_rerun_when_staged_facts_touch_committed_nodes():
    # Setting a previously-unset field on a committed entity stages no TypeFact
    # (those dedupe away), but the entity's rules must still re-run.
    lore2 = Lore("rerun-test")

    @lore2.entity
    class Doc(Entity):
        state: str | None = None

    @lore2.rule(message="Doc {obj.id} may not be archived.")
    def not_archived(doc: Doc, graph) -> bool:
        return doc.state != "archived"

    session = lore2.compile().session()
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


# --- rule re-fire on direct targets of staged edges --------------------------
# Aggregate (non-monotone) rules read a committed node's *incoming* edges, so
# they must re-run when a proposal stages an edge pointing at that node.

ledger = Lore("ledger-test")


@ledger.entity
class JournalEntry(Entity):
    memo: str


@ledger.entity
class Posting(Entity):
    amount: float  # signed: debits positive, credits negative
    entry: Relation[JournalEntry]


@ledger.rule(message="Journal entry {obj.id} is unbalanced.")
def entry_balances(entry: JournalEntry, graph) -> bool:
    postings = graph.incoming(entry.id, "Posting.entry")
    return sum(graph.get(e.subject_id).amount for e in postings) == 0


@ledger.entity
class Budget(Entity):
    cap: float


@ledger.entity
class Expense(Entity):
    amount: float
    budget: Relation[Budget]


@ledger.rule(message="Budget {obj.id} is over its cap.")
def budget_within_cap(budget: Budget, graph) -> bool:
    expenses = graph.incoming(budget.id, "Expense.budget")
    return sum(graph.get(e.subject_id).amount for e in expenses) <= budget.cap


ledger_guard = ledger.compile()


def ledger_session():
    session = ledger_guard.session()
    verdict = session.try_commit(
        JournalEntry(id="je_1", memo="opening"),
        Posting(id="p_1", amount=100.0, entry="je_1"),
        Posting(id="p_2", amount=-100.0, entry="je_1"),
    )
    assert verdict.ok, f"balanced setup batch failed: {verdict.violations}"
    return session


def test_stray_edge_refires_rule_on_committed_target():
    # The regression: a stray posting pointing at a committed entry stages no
    # fact *about* the entry, but its rule must re-run and catch the imbalance.
    session = ledger_session()
    verdict = session.propose(Posting(id="p_stray", amount=50.0, entry="je_1"))
    assert verdict.ok is False
    assert {(v.check, v.subjects) for v in verdict.violations} == {
        ("rule:entry_balances", ("je_1",))
    }
    (violation,) = verdict.violations
    assert "je_1" in violation.message


def test_rejected_stray_edge_leaves_zero_trace():
    session = ledger_session()
    assert not session.propose(Posting(id="p_stray", amount=50.0, entry="je_1")).ok
    session.rollback()
    assert "p_stray" not in session.dump()
    assert session.graph.get("p_stray") is None
    verdict = session.try_commit(
        JournalEntry(id="je_2", memo="closing"),
        Posting(id="p_3", amount=25.0, entry="je_2"),
        Posting(id="p_4", amount=-25.0, entry="je_2"),
    )
    assert verdict.ok and verdict.violations == []


def test_valid_incoming_edge_still_commits():
    # Re-firing must not make incoming edges over-strict: an edge that keeps
    # the aggregate within bounds commits; the one that breaks it is rejected.
    session = ledger_guard.session()
    assert session.try_commit(Budget(id="bud_1", cap=100.0)).ok
    assert session.try_commit(Expense(id="ex_1", amount=60.0, budget="bud_1")).ok
    verdict = session.try_commit(Expense(id="ex_2", amount=30.0, budget="bud_1"))
    assert verdict.ok and verdict.violations == []
    verdict = session.try_commit(Expense(id="ex_3", amount=20.0, budget="bud_1"))
    assert {(v.check, v.subjects) for v in verdict.violations} == {
        ("rule:budget_within_cap", ("bud_1",))
    }


def test_dangling_edge_target_reports_existence_without_rule_crash():
    session = ledger_guard.session()
    verdict = session.propose(Posting(id="p_x", amount=50.0, entry="nope"))
    assert {(v.check, v.subjects) for v in verdict.violations} == {
        ("existence", ("p_x", "nope"))
    }


def test_derived_only_closure_change_does_not_refire_rules():
    # Boundary lock-in: re-fire is one hop over *asserted* staged edges only.
    # A staged edge elsewhere that changes a committed node's transitive
    # closure — without any asserted fact touching it — must not re-run its
    # rules. Changing this boundary should be a deliberate decision.
    chain = Lore("closure-boundary-test")

    @chain.entity
    class Node(Entity):
        next: Relation["Node"] | None = relation(transitive=True, default=None)

    @chain.rule(message="Node {obj.id} reaches the forbidden node.")
    def a_avoids_forbidden(node: Node, graph) -> bool:
        # Only "a" is constrained, so the staged subjects of the proposal
        # below pass trivially — a failure could come only from re-running
        # the rule on the untouched committed node "a".
        return node.id != "a" or "bad" not in graph.reachable("a", "Node.next")

    session = chain.compile().session()
    assert session.try_commit(Node(id="bad"), Node(id="b"), Node(id="a", next="b")).ok
    verdict = session.try_commit(Node(id="b", next="bad"))
    assert verdict.ok and verdict.violations == []
    # The closure did change — a now reaches bad — but a's rule stayed silent.
    assert "bad" in session.graph.reachable("a", "Node.next")


def test_target_that_is_also_staged_subject_runs_rules_once():
    session = ledger_session()
    verdict = session.propose(
        JournalEntry(id="je_1", memo="amended"),
        Posting(id="p_stray", amount=50.0, entry="je_1"),
    )
    # je_1 is both a staged subject (changed memo → single_value) and the
    # target of a staged edge; its balance rule fires once, not twice.
    assert {(v.check, v.subjects) for v in verdict.violations} == {
        ("single_value", ("je_1",)),
        ("rule:entry_balances", ("je_1",)),
    }
    assert len(verdict.violations) == 2


def test_stray_edge_repair_prompt_renders_rule_message():
    session = ledger_session()
    verdict = session.propose(Posting(id="p_stray", amount=50.0, entry="je_1"))
    assert "Journal entry je_1 is unbalanced." in verdict.repair_prompt()
