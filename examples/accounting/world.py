"""The accounting lore: a chart of accounts, journal entries, postings,
invoices, payments — and the books an agent-bookkeeper cannot corrupt.

All money is integer cents: the balance rule compares exact sums, so floats
would make "debits == credits" a rounding accident (integer cents is also
just correct accounting practice).

The atomic-proposal discipline: ``entry_balances`` sums an entry's postings,
so it is **non-monotone** — an entry that balances now can be unbalanced by
one more posting later. Propose a journal entry atomically with all of its
postings (``session.propose(entry, debit, credit)``, or return them as one
sequence through ``guard_tool``); real journal entries are atomic anyway.
The engine backstops the other direction: rules re-run on committed nodes a
staged edge points at, so a stray posting aimed at an already-committed entry
re-fires ``entry_balances`` on that entry and is rejected (demo.py, beat 11).
"""

import json
from pathlib import Path

from lore import Entity, Graph, Lore, Relation, one_of, relation

lore = Lore("accounting")


@lore.entity
class Account(Entity):
    name: str


# The five account types are a disjoint union over Account — an id can never
# be two of these at once. One-sided declarations cover each pair.
@lore.entity
class Asset(Account): ...


@lore.entity
class Liability(Account):
    lore_disjoint_with = [Asset]


@lore.entity
class Equity(Account):
    lore_disjoint_with = [Asset, Liability]


@lore.entity
class Revenue(Account):
    lore_disjoint_with = [Asset, Liability, Equity]


@lore.entity
class Expense(Account):
    lore_disjoint_with = [Asset, Liability, Equity, Revenue]


_ACCOUNT_TYPES: dict[str, type[Account]] = {
    cls.__name__: cls for cls in (Asset, Liability, Equity, Revenue, Expense)
}

_CHART_PATH = Path(__file__).with_name("chart_of_accounts.json")


def load_chart(path: Path = _CHART_PATH) -> list[Account]:
    """The seed chart of accounts: fixture rows → typed Account entities.

    Each row's ``"type"`` names one of the five registered subclasses; the
    same loader seeds demo.py and live_agent.py.
    """
    rows = json.loads(Path(path).read_text())
    return [_ACCOUNT_TYPES[row["type"]](id=row["id"], name=row["name"]) for row in rows]


@lore.entity
class Period(Entity):
    label: str  # e.g. "2026-08"
    status: str = one_of("open", "closed")


@lore.entity
class JournalEntry(Entity):
    memo: str
    period: Relation[Period]


@lore.entity
class Posting(Entity):
    side: str = one_of("debit", "credit")
    amount_cents: int
    account: Relation[Account]  # any Account subclass satisfies this (MRO)
    entry: Relation[JournalEntry]


@lore.entity
class Invoice(Entity):
    vendor: str
    total_cents: int
    # One-to-one with Payment via the inverse pair below; optional, so
    # invoices are constructible unpaid.
    settled_by: Relation["Payment"] | None = relation(
        inverse_of="reconciles", default=None
    )


@lore.entity
class Payment(Entity):
    amount_cents: int
    # max_per_target=1 on this side makes the pair one-to-one in BOTH
    # directions: inverse mirrors count toward max_per_target, so the limit
    # holds whichever direction the agent asserts the fact from (see the
    # relation() docstring in src/lore/schema.py).
    reconciles: Relation[Invoice] = relation(max_per_target=1, inverse_of="settled_by")


@lore.rule(
    message="Journal entry {obj.id} does not balance: total debits must equal total credits."
)
def entry_balances(entry: JournalEntry, graph: Graph) -> bool:
    """The aggregate rule: sum an entry's postings via graph.incoming.

    graph.incoming returns EdgeFacts; graph.get rehydrates each posting.
    """
    postings = [graph.get(e.subject_id) for e in graph.incoming(entry.id, "Posting.entry")]
    debits = sum(p.amount_cents for p in postings if p.side == "debit")
    credits = sum(p.amount_cents for p in postings if p.side == "credit")
    return debits == credits


@lore.rule(message="Journal entry {obj.id} posts to period {obj.period}, which is closed.")
def no_posting_to_closed_period(entry: JournalEntry, graph: Graph) -> bool:
    period = graph.get(entry.period)
    return period is None or period.status == "open"  # None → existence reports it


@lore.rule(message="Posting {obj.id} has a non-positive amount; direction belongs in 'side'.")
def posting_amount_positive(posting: Posting, graph: Graph) -> bool:
    return posting.amount_cents > 0


@lore.rule(
    message=(
        "Payment {obj.id} of {obj.amount_cents} cents does not settle "
        "invoice {obj.reconciles} in full."
    )
)
def payment_settles_in_full(payment: Payment, graph: Graph) -> bool:
    invoice = graph.get(payment.reconciles)
    return invoice is None or payment.amount_cents == invoice.total_cents


@lore.rule(
    message="Journal entry {obj.id} moves more than $10,000 — flagged for controller review.",
    severity="flag",
)
def large_entry_flagged(entry: JournalEntry, graph: Graph) -> bool:
    postings = [graph.get(e.subject_id) for e in graph.incoming(entry.id, "Posting.entry")]
    return sum(p.amount_cents for p in postings if p.side == "debit") <= 1_000_000


guard = lore.compile()
