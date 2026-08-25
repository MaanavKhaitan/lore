"""The bookkeeping demo — no API key, no LLM, fully deterministic.

Seeds a session with a chart of accounts, two periods, two vendor invoices,
and one prior journal entry, then scripts the proposals an agent-bookkeeper
might make: balanced and unbalanced entries, invented accounts, closed
periods, a re-typed account, a large entry that is flagged but commits,
double reconciliation attempted from both directions of an inverse pair, a
snapshot/restore across a simulated process boundary, and a stray posting
aimed at committed history.

Run:  python examples/accounting/demo.py
"""

from world import (
    Expense,
    Invoice,
    JournalEntry,
    Payment,
    Period,
    Posting,
    guard,
    load_chart,
)

session = guard.session(
    seed=[
        *load_chart(),
        Period(id="per_2026_07", label="2026-07", status="closed"),
        Period(id="per_2026_08", label="2026-08", status="open"),
        Invoice(id="inv_1", vendor="Acme Supply Co.", total_cents=50_000),
        Invoice(id="inv_2", vendor="Initech", total_cents=120_000),
        # History: je_0 was posted while 2026-07 was open; the period has since
        # closed. Committed facts are immutable (a status flip would be a
        # single_value violation), so history that predates the current period
        # state is loaded as trusted — the same stance guard.restore() takes.
        JournalEntry(id="je_0", memo="July office rent", period="per_2026_07"),
        Posting(id="po_0a", side="debit", amount_cents=150_000, account="acct_rent", entry="je_0"),
        Posting(id="po_0b", side="credit", amount_cents=150_000, account="acct_cash", entry="je_0"),
    ],
    validate_seed=False,
)


def show(session, title, *objs):
    print(f"--- {title}")
    for obj in objs:
        print(f"    {obj!r}")
    verdict = session.try_commit(*objs)
    if verdict.ok:
        print("    verdict: OK — committed")
        for flag in verdict.flags:
            print("    flagged for review (severity='flag' surfaces, never blocks):")
            print(f"      {flag.message}")
    else:
        print("    verdict: REJECTED")
        print("    repair prompt fed back to the agent:")
        for line in verdict.repair_prompt().splitlines():
            print(f"      {line}")
    print()


print("--- 1. The policy manual: the same declaration, rendered as prompt English")
lines = guard.to_context().splitlines()
print(f"    {lines[0]}")
print("    [... entity shapes and account-type disjointness elided ...]")
for line in lines[-8:]:  # cardinality, the inverse pair, existence, the five rules
    print(f"    {line}")
print()

# Journal entries are proposed atomically with all their postings —
# entry_balances is non-monotone, so the entry and its legs are one verdict
# and one commit (see the world.py docstring).
show(
    session,
    "2. A balanced entry: office supplies paid in cash",
    JournalEntry(id="je_1", memo="Printer paper and toner", period="per_2026_08"),
    Posting(id="po_1", side="debit", amount_cents=12_000, account="acct_supplies", entry="je_1"),
    Posting(id="po_2", side="credit", amount_cents=12_000, account="acct_cash", entry="je_1"),
)

show(
    session,
    "3. An unbalanced entry: the debit and credit legs disagree",
    JournalEntry(id="je_2", memo="Printer paper and toner", period="per_2026_08"),
    Posting(id="po_3", side="debit", amount_cents=12_000, account="acct_supplies", entry="je_2"),
    Posting(id="po_4", side="credit", amount_cents=11_000, account="acct_cash", entry="je_2"),
)

show(
    session,
    "4. A posting to an account that does not exist — no invented accounts",
    JournalEntry(id="je_3", memo="Client dinner", period="per_2026_08"),
    Posting(id="po_5", side="debit", amount_cents=40_000, account="acct_slush_fund", entry="je_3"),
    Posting(id="po_6", side="credit", amount_cents=40_000, account="acct_cash", entry="je_3"),
)

show(
    session,
    "5. An entry posted into the closed July period",
    JournalEntry(id="je_4", memo="Backdated cleaning invoice", period="per_2026_07"),
    Posting(id="po_7", side="debit", amount_cents=90_000, account="acct_rent", entry="je_4"),
    Posting(id="po_8", side="credit", amount_cents=90_000, account="acct_cash", entry="je_4"),
)

# The committed Asset id re-typed as an Expense: subclasses of Account are a
# disjoint union, so the staged Expense type fact clashes with the seeded Asset.
show(
    session,
    "6. The cash account re-typed as an Expense",
    Expense(id="acct_cash", name="Cash"),
)

show(
    session,
    "7. A large but valid entry: six months of rent prepaid — flagged, not blocked",
    JournalEntry(id="je_5", memo="Rent prepayment Sep 2026 - Feb 2027", period="per_2026_08"),
    Posting(id="po_9", side="debit", amount_cents=2_000_000, account="acct_prepaid_rent", entry="je_5"),
    Posting(id="po_10", side="credit", amount_cents=2_000_000, account="acct_cash", entry="je_5"),
)

show(
    session,
    "8a. Reconciliation: pay_1 settles inv_1 in full",
    Payment(id="pay_1", amount_cents=50_000, reconciles="inv_1"),
)

show(
    session,
    "8b. The classic double payment (cross-turn: pay_1 committed turns ago)",
    Payment(id="pay_2", amount_cents=50_000, reconciles="inv_1"),
)

# The inverse_of showcase. The agent asserts the fact from the *invoice* side:
# pay_3 legitimately pays inv_2, but the re-asserted inv_1 claims settled_by
# pay_3. The check that fires is max_per_target on Payment.reconciles — the
# staged Invoice.settled_by edge derives its mirror onto the paired predicate,
# so inv_1's rejection lists pay_3 as "(proposed)" pointing at it via
# 'reconciles', a field the agent never touched.
show(
    session,
    "9. The same double settlement, asserted from the invoice side",
    Payment(id="pay_3", amount_cents=120_000, reconciles="inv_2"),
    Invoice(id="inv_1", vendor="Acme Supply Co.", total_cents=50_000, settled_by="pay_3"),
)

print("--- 10. Day 2: the next agent turn lands on a different worker")
blob = session.snapshot()
print(f"    session.snapshot() → {len(blob)} bytes of JSON, stamped {guard.fingerprint[:15]}…")
session = guard.restore(blob)  # a fresh process rehydrates the committed books
print("    guard.restore(blob) → committed facts, order, and step numbers reproduced")
print()

show(
    session,
    "10 (cont.) The double payment retried against the restored session",
    Payment(id="pay_2", amount_cents=50_000, reconciles="inv_1"),
)

# A single posting aimed at the entry committed in beat 2: rules re-run on
# committed nodes that a staged edge points at, so entry_balances re-fires on
# je_1 and rejects the posting that would silently unbalance history.
show(
    session,
    "11. A stray posting aimed at the committed entry je_1",
    Posting(id="po_11", side="debit", amount_cents=5_000, account="acct_supplies", entry="je_1"),
)
