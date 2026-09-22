"""Custom rules: rechecks, aggregate dependencies, and preexisting failures."""

from lore import Entity, Lore, Relation, relation


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
