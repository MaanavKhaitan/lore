"""Table-driven per-axiom cases: one pass and one fail case per check, plus the
cross-turn, subclass, disjoint, atomicity, and severity scenarios."""

import pytest

from lore import Entity, Lore, Relation, one_of, relation
from lore.engine import ground
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


# --- rule re-fire one asserted hop from staged facts --------------------------
# Aggregate (non-monotone) rules read a committed node's *incoming* edges and
# hydrate their subjects, so they must re-run when a staged fact lands within
# one asserted hop: a stray edge pointing at the node, a late-filled optional
# attribute on a neighbor, or a subclass re-type of a neighbor.

def _sibling_lore() -> Lore:
    lore2 = Lore("sibling-test")

    @lore2.entity
    class Item(Entity):
        label: str

    @lore2.entity
    class AItem(Item):
        a_only: float

    @lore2.entity
    class BItem(Item):
        b_only: float

    @lore2.rule(message="BItem {obj.id} exceeds 10.")
    def b_within_limit(item: BItem, graph) -> bool:
        return item.b_only <= 10

    return lore2


def test_sibling_retype_is_rejected_as_incoherent():
    # Re-typing a committed id as a sibling subclass — with identical values
    # for every shared field, so nothing else conflicts — must reject: the
    # node would rehydrate as only one branch, silently bypassing the other
    # branch's rules forever (b_within_limit here would never see b_only=99).
    lore2 = _sibling_lore()
    AItem, BItem = lore2._classes["AItem"], lore2._classes["BItem"]
    session = lore2.compile().session()
    assert session.try_commit(AItem(id="x1", label="widget", a_only=1.0)).ok
    verdict = session.propose(BItem(id="x1", label="widget", b_only=99.0))
    assert {(v.check, v.subjects) for v in verdict.violations} == {
        ("type_coherence", ("x1",))
    }
    # With changed shared values, single_value fires alongside it.
    verdict = session.propose(BItem(id="x1", label="relabeled", b_only=99.0))
    assert {v.check for v in verdict.violations} == {"type_coherence", "single_value"}
    # A distinct id — the repair the message asks for — is clean.
    assert session.try_commit(BItem(id="x2", label="widget", b_only=5.0)).ok


def test_preexisting_incoherent_node_neither_crashes_nor_blocks():
    # A world seeded under two sibling branches (validate_seed=False) is
    # already lossy — rules on the unpicked branch skip instead of crashing,
    # and unrelated proposals are not blocked by the pre-existing state.
    lore2 = _sibling_lore()
    Item = lore2._classes["Item"]
    AItem, BItem = lore2._classes["AItem"], lore2._classes["BItem"]
    session = lore2.compile().session(
        seed=[AItem(id="x1", label="widget", a_only=1.0),
              BItem(id="x1", label="widget", b_only=99.0)],
        validate_seed=False,
    )
    verdict = session.try_commit(Item(id="y1", label="fresh"))
    assert verdict.ok and verdict.violations == []


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


def test_closure_change_beyond_one_hop_is_caught_differentially():
    # The deliberate boundary decision: a staged edge two hops away that flips
    # a committed node's rule from satisfied to violated is caught — committed
    # facts are immutable, so letting the flip commit would leave the world
    # unrepairable. The rejection names the committed node and says the
    # proposal, not the node, is at fault.
    chain = Lore("closure-boundary-test")

    @chain.entity
    class Node(Entity):
        next: Relation["Node"] | None = relation(transitive=True, default=None)

    @chain.rule(message="Node {obj.id} reaches the forbidden node.")
    def a_avoids_forbidden(node: Node, graph) -> bool:
        # Only "a" is constrained, so every node within one hop of the staged
        # edge below passes trivially — the failure can come only from the
        # differential re-check of the committed node "a", two hops upstream.
        return node.id != "a" or "bad" not in graph.reachable("a", "Node.next")

    session = chain.compile().session()
    assert session.try_commit(
        Node(id="bad"), Node(id="b"), Node(id="m", next="b"), Node(id="a", next="m")
    ).ok
    verdict = session.try_commit(Node(id="b", next="bad"))
    assert {(v.check, v.subjects) for v in verdict.violations} == {
        ("rule:a_avoids_forbidden", ("a",))
    }
    assert "This rule held before this proposal" in verdict.violations[0].message
    # The rejected edge left zero trace: a still cannot reach bad.
    assert "bad" not in session.graph.reachable("a", "Node.next")


# A sibling-aggregate lore: caps and fees both point at a plan, and the cap
# rule reads the fees through the shared target — so a staged fee is two hops
# from a committed cap, the exact shape the differential re-check exists for.
wedge = Lore("wedge-test")


@wedge.entity
class Plan(Entity):
    pass


@wedge.entity
class Fee(Entity):
    amount: float
    plan: Relation[Plan]


@wedge.entity
class Cap(Entity):
    limit: float
    plan: Relation[Plan]


@wedge.rule(message="Cap {obj.id} is below the total of its plan's fees.")
def cap_covers_fees(cap: Cap, graph) -> bool:
    fees = [graph.get(e.subject_id) for e in graph.incoming(cap.plan, "Fee.plan")]
    return cap.limit >= sum(f.amount for f in fees)


@wedge.rule(message="Cap {obj.id} is flagged for review above 50.", severity="flag")
def large_cap_flagged(cap: Cap, graph) -> bool:
    return cap.limit <= 50.0


wedge_guard = wedge.compile()


def test_proposal_that_newly_breaks_committed_two_hop_rule_is_rejected():
    # The wedge shape: the cap committed while it covered the fees; a later fee
    # would silently put it under water forever (committed facts are
    # immutable). The fee proposal is rejected instead, naming the cap.
    session = wedge_guard.session(seed=[Plan(id="plan_1")])
    assert session.try_commit(Fee(id="fee_1", amount=10.0, plan="plan_1")).ok
    assert session.try_commit(Cap(id="cap_1", limit=30.0, plan="plan_1")).ok
    verdict = session.try_commit(Fee(id="fee_2", amount=25.0, plan="plan_1"))
    assert {(v.check, v.subjects) for v in verdict.violations} == {
        ("rule:cap_covers_fees", ("cap_1",))
    }
    assert "This rule held before this proposal" in verdict.violations[0].message
    # A fee the cap still covers commits fine.
    assert session.try_commit(Fee(id="fee_3", amount=15.0, plan="plan_1")).ok


def test_preexisting_two_hop_rule_failure_stays_silent():
    # A world seeded already under water (validate_seed=False) must not block
    # unrelated proposals: the cap's failure predates them, so the
    # differential check reports nothing.
    session = wedge_guard.session(
        seed=[
            Plan(id="plan_1"),
            Fee(id="fee_1", amount=40.0, plan="plan_1"),
            Cap(id="cap_1", limit=30.0, plan="plan_1"),
            Plan(id="plan_2"),
        ],
        validate_seed=False,
    )
    verdict = session.try_commit(Fee(id="fee_2", amount=5.0, plan="plan_2"))
    assert verdict.ok and verdict.violations == []


def test_newly_broken_flag_rule_flags_and_commits():
    # Differential reporting respects severity: a staged fact that flips a
    # committed cap's flag rule surfaces the flag but does not block.
    session = wedge_guard.session(seed=[Plan(id="plan_1")])
    assert session.try_commit(Cap(id="cap_1", limit=40.0, plan="plan_1")).ok

    flip = Lore("flag-flip-test")

    @flip.entity
    class Account(Entity):
        pass

    @flip.entity
    class Hold(Entity):
        amount: float
        account: Relation[Account]

    @flip.entity
    class Limit(Entity):
        ceiling: float
        account: Relation[Account]

    @flip.rule(message="Limit {obj.id} is exceeded by holds.", severity="flag")
    def holds_within_limit(limit: Limit, graph) -> bool:
        holds = [graph.get(e.subject_id) for e in graph.incoming(limit.account, "Hold.account")]
        return sum(h.amount for h in holds) <= limit.ceiling

    session = flip.compile().session(seed=[Account(id="acct_1")])
    assert session.try_commit(Limit(id="lim_1", ceiling=20.0, account="acct_1")).ok
    verdict = session.try_commit(Hold(id="hold_1", amount=25.0, account="acct_1"))
    assert verdict.ok  # flags commit
    assert {(v.check, v.severity) for v in verdict.flags} == {
        ("rule:holds_within_limit", "flag")
    }


def test_committed_always_failing_flag_does_not_refire_beyond_one_hop():
    # An always-flag rule fires when its subject is proposed; once committed it
    # must not re-flag on every unrelated proposal (baseline fails too, so the
    # failure is never "newly broken").
    session = wedge_guard.session(seed=[Plan(id="plan_1"), Plan(id="plan_2")])
    verdict = session.propose(Cap(id="cap_big", limit=80.0, plan="plan_1"))
    assert verdict.ok and len(verdict.flags) == 1  # flagged at proposal
    session.commit()
    verdict = session.try_commit(Fee(id="fee_1", amount=1.0, plan="plan_2"))
    assert verdict.ok and verdict.violations == []


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


# A staged fact on the subject of an already-committed edge changes the
# target's aggregate without staging anything about the target: re-asserting
# the entity filters the committed edge from the staged set, so only the
# one-hop re-fire boundary catches these.

pending = Lore("pending-ledger-test")


@pending.entity
class PendingEntry(Entity):
    memo: str


@pending.entity
class PendingPosting(Entity):
    amount: float | None = None  # pending postings get an amount later
    entry: Relation[PendingEntry]


@pending.rule(message="Journal entry {obj.id} is unbalanced.")
def pending_entry_balances(entry: PendingEntry, graph) -> bool:
    postings = graph.incoming(entry.id, "PendingPosting.entry")
    return sum(graph.get(e.subject_id).amount or 0.0 for e in postings) == 0


pending_guard = pending.compile()


def test_late_attr_fill_refires_rule_on_committed_edge_target():
    # The edge p_1 → e_1 is committed, so filling p_1's amount stages only an
    # AttrFact — yet e_1's balance changes and its rule must re-run.
    session = pending_guard.session()
    assert session.try_commit(
        PendingEntry(id="e_1", memo="opening"),
        PendingPosting(id="p_1", entry="e_1"),  # amount unknown → sums to 0
    ).ok
    verdict = session.propose(PendingPosting(id="p_1", amount=50.0, entry="e_1"))
    assert {(v.check, v.subjects) for v in verdict.violations} == {
        ("rule:pending_entry_balances", ("e_1",))
    }


def test_late_attr_fills_that_keep_balance_commit():
    session = pending_guard.session()
    assert session.try_commit(
        PendingEntry(id="e_1", memo="opening"),
        PendingPosting(id="p_1", entry="e_1"),
        PendingPosting(id="p_2", entry="e_1"),
    ).ok
    verdict = session.try_commit(
        PendingPosting(id="p_1", amount=25.0, entry="e_1"),
        PendingPosting(id="p_2", amount=-25.0, entry="e_1"),
    )
    assert verdict.ok and verdict.violations == []


tiers = Lore("tiered-budget-test")


@tiers.entity
class TieredBudget(Entity):
    premium_cap: float


@tiers.entity
class TierExpense(Entity):
    amount: float
    budget: Relation[TieredBudget]


@tiers.entity
class PremiumExpense(TierExpense):
    pass


@tiers.rule(message="Budget {obj.id} is over its premium cap.")
def premium_within_cap(budget: TieredBudget, graph) -> bool:
    expenses = (graph.get(e.subject_id) for e in graph.incoming(budget.id, "TierExpense.budget"))
    return sum(e.amount for e in expenses if isinstance(e, PremiumExpense)) <= budget.premium_cap


tiers_guard = tiers.compile()


def test_subclass_retype_refires_rule_on_committed_edge_target():
    # Re-typing ex_1 as PremiumExpense stages only a TypeFact (its amount and
    # edge are already committed), but bud_1's premium aggregate changes.
    session = tiers_guard.session()
    assert session.try_commit(TieredBudget(id="bud_1", premium_cap=50.0)).ok
    assert session.try_commit(TierExpense(id="ex_1", amount=60.0, budget="bud_1")).ok
    verdict = session.propose(PremiumExpense(id="ex_1", amount=60.0, budget="bud_1"))
    assert {(v.check, v.subjects) for v in verdict.violations} == {
        ("rule:premium_within_cap", ("bud_1",))
    }


caps = Lore("cap-fill-test")


@caps.entity
class CapBudget(Entity):
    cap: float | None = None  # cap may be set after expenses exist


@caps.entity
class CapExpense(Entity):
    amount: float
    budget: Relation[CapBudget]


@caps.rule(message="Expense {obj.id} exceeds its budget's cap.")
def expense_within_cap(expense: CapExpense, graph) -> bool:
    budget = graph.get(expense.budget)
    return budget is None or budget.cap is None or expense.amount <= budget.cap


caps_guard = caps.compile()


def test_late_cap_fill_refires_rule_on_committed_edge_subject():
    # The other direction: setting bud_1's cap stages only an AttrFact on
    # bud_1, but the rule on the committed expense pointing at it newly fails.
    session = caps_guard.session()
    assert session.try_commit(CapBudget(id="bud_1")).ok
    assert session.try_commit(CapExpense(id="ex_1", amount=60.0, budget="bud_1")).ok
    verdict = session.propose(CapBudget(id="bud_1", cap=50.0))
    assert {(v.check, v.subjects) for v in verdict.violations} == {
        ("rule:expense_within_cap", ("ex_1",))
    }


# --- multi-valued relations (Relation[list[X]]): grounding, per-edge checks,
# accretion. Facts are append-only, so a many field accretes like a set —
# re-proposing an id whose list grew stages only the new edges, and
# single_value (scalar immutability) deliberately does not apply.

contracts = Lore("many-relation-test")


@contracts.entity
class DefinedTerm(Entity):
    pass


@contracts.entity
class Clause(Entity):
    # Required (no default): an empty list must be constructible and hydrate back.
    uses_terms: Relation[list[DefinedTerm]] = relation(max_per_target=2)


@contracts.rule(message="Clause {obj.id} uses more than three defined terms.")
def at_most_three_terms(clause: Clause, graph) -> bool:
    return len(clause.uses_terms) <= 3


many_guard = contracts.compile()


def many_session():
    return many_guard.session(seed=[DefinedTerm(id=f"t_{i}") for i in range(1, 6)])


def test_ground_emits_one_edge_per_distinct_list_element():
    facts = ground(many_guard, Clause(id="c_1", uses_terms=["t_1", "t_2", "t_1"]), "asserted")
    edges = [f for f in facts if isinstance(f, EdgeFact)]
    assert [(e.subject_id, e.predicate, e.object_id) for e in edges] == [
        ("c_1", "Clause.uses_terms", "t_1"),
        ("c_1", "Clause.uses_terms", "t_2"),
    ]


def test_ground_empty_list_emits_zero_edges():
    facts = ground(many_guard, Clause(id="c_1", uses_terms=[]), "asserted")
    assert not any(isinstance(f, EdgeFact) for f in facts)


def test_existence_fires_once_per_dangling_element():
    verdict = many_session().propose(Clause(id="c_1", uses_terms=["t_1", "ghost_a", "ghost_b"]))
    assert {(v.check, v.subjects) for v in verdict.violations} == {
        ("existence", ("c_1", "ghost_a")),
        ("existence", ("c_1", "ghost_b")),
    }


def test_max_per_target_counts_many_edges():
    verdict = many_session().propose(
        Clause(id="c_1", uses_terms=["t_1"]),
        Clause(id="c_2", uses_terms=["t_1"]),
        Clause(id="c_3", uses_terms=["t_1"]),
    )
    assert {(v.check, v.subjects) for v in verdict.violations} == {
        ("max_per_target", ("t_1", "c_1", "c_2", "c_3"))
    }


def test_single_value_stays_silent_on_multi_edge_many_subjects():
    verdict = many_session().propose(Clause(id="c_1", uses_terms=["t_1", "t_2", "t_3"]))
    assert verdict.ok and verdict.violations == []


def test_accretion_reproposal_stages_only_the_new_edges_and_commits():
    session = many_session()
    assert session.try_commit(Clause(id="c_1", uses_terms=["t_1", "t_2"])).ok
    verdict = session.propose(Clause(id="c_1", uses_terms=["t_1", "t_2", "t_3"]))
    assert verdict.ok
    assert session.staged_facts == (
        EdgeFact("c_1", "Clause.uses_terms", "t_3", "asserted"),
    )
    session.commit()
    assert session.graph.get("c_1").uses_terms == ["t_1", "t_2", "t_3"]


def test_accretion_never_removes_a_committed_reference():
    session = many_session()
    assert session.try_commit(Clause(id="c_1", uses_terms=["t_1", "t_2"])).ok
    assert session.try_commit(Clause(id="c_1", uses_terms=["t_1"])).ok  # subset: no new facts
    assert session.graph.get("c_1").uses_terms == ["t_1", "t_2"]


def test_rule_receives_the_full_accreted_list():
    session = many_session()
    assert session.try_commit(Clause(id="c_1", uses_terms=["t_1", "t_2", "t_3"])).ok
    # The staged edge alone is fine; the rule must see committed ∪ staged.
    verdict = session.propose(Clause(id="c_1", uses_terms=["t_1", "t_2", "t_3", "t_4"]))
    assert {(v.check, v.subjects) for v in verdict.violations} == {
        ("rule:at_most_three_terms", ("c_1",))
    }


# --- multi-valued relations meet inference: edges are edges, so the
# characteristics and inverse pairing work unchanged per element.

tree = Lore("many-tree-test")


@tree.entity
class Section(Entity):
    contains: Relation[list["Section"]] = relation(transitive=True, irreflexive=True, default=[])


tree_guard = tree.compile()


def tree_session():
    session = tree_guard.session(
        seed=[Section(id="root"), Section(id="a"), Section(id="b"), Section(id="c")]
    )
    verdict = session.try_commit(
        Section(id="root", contains=["a", "b"]), Section(id="a", contains=["c"])
    )
    assert verdict.ok, f"tree setup failed: {verdict.violations}"
    return session


def test_transitive_many_reachable_over_branches():
    assert tree_session().graph.reachable("root", "Section.contains") == {"a", "b", "c"}


def test_transitive_many_cycle_rejected_with_rendered_chain():
    verdict = tree_session().propose(Section(id="c", contains=["root"]))
    (violation,) = verdict.violations
    assert violation.message == (
        "root cannot reach itself via 'contains', but this proposal creates a "
        "cycle: root → a (already committed at step 1) → c (already committed at "
        "step 1) → root (proposed)."
    )


def test_symmetric_many_mirrors_without_single_value():
    pals = Lore("many-symmetric-test")

    @pals.entity
    class Person(Entity):
        friends: Relation[list["Person"]] = relation(symmetric=True, default=[])

    session = pals.compile().session(
        seed=[Person(id="p_1"), Person(id="p_2"), Person(id="p_3")]
    )
    assert session.try_commit(Person(id="p_1", friends=["p_2"])).ok
    # p_2 ends up befriending both p_1 and p_3, each fact asserted from the
    # other side — visible only through symmetric mirrors, and fine on a many
    # field (the scalar equivalent fires single_value).
    verdict = session.try_commit(Person(id="p_3", friends=["p_2"]))
    assert verdict.ok and verdict.violations == []
    assert session.graph.reachable("p_2", "Person.friends") == {"p_1", "p_2", "p_3"}


squads = Lore("many-inverse-test")


@squads.entity
class SquadMember(Entity):
    member_of: Relation["Squad"] | None = relation(default=None)


@squads.entity
class Squad(Entity):
    # One-to-many pair: a member belongs to at most one squad, enforced
    # whichever direction asserts the fact.
    members: Relation[list[SquadMember]] = relation(
        inverse_of="member_of", max_per_target=1, default=[]
    )


squads_guard = squads.compile()


def test_inverse_pairing_many_with_scalar_enforces_max_per_target_cross_direction():
    session = squads_guard.session(
        seed=[SquadMember(id="m_1"), SquadMember(id="m_2"), Squad(id="s_1"), Squad(id="s_2")]
    )
    assert session.try_commit(Squad(id="s_1", members=["m_1", "m_2"])).ok
    verdict = session.propose(SquadMember(id="m_1", member_of="s_2"))
    assert {(v.check, v.subjects) for v in verdict.violations} == {
        ("max_per_target", ("m_1", "s_1", "s_2"))
    }
    (violation,) = verdict.violations
    assert "m_1 has 2: s_1 (already committed at step 1), s_2 (proposed)" in violation.message
    # Re-asserting a committed membership from the scalar side is redundant, not
    # a violation: the inverse mirror duplicates the asserted edge and is dropped.
    session.rollback()
    assert session.try_commit(SquadMember(id="m_1", member_of="s_1")).ok
