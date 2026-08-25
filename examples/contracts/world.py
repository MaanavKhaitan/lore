"""The contract-drafting lore: the negotiation playbook as a declaration.

Companies deploying contract-drafting agents write negotiation playbooks —
approved clause language, walk-away terms, escalation triggers — as prose in
a system prompt, and hope the model follows them. Here the playbook *is* the
lore, and one declaration serves both halves: ``guard.to_context()`` renders
it into the prompt (prevention), and the session enforces it deterministically
when the model ignores it (detection). The two severities have exact playbook
meanings: walk-away terms are ``severity="reject"`` (never commits), and
escalate-to-counsel terms are ``severity="flag"`` (commits, but surfaced for
review before signature). Completeness — what a *finished* MSA must
contain — is declared the same way, as ``@lore.goal``s checked by one
``session.check_goals()`` call at the output boundary.

Atomicity discipline: existence checks fire immediately, so a clause that
uses *new* defined terms must be proposed atomically with them —
``session.propose(clause, term)`` — or the dangling references are rejected.
"""

from lore import Entity, Graph, Lore, Relation, one_of, relation

lore = Lore("contracts")


@lore.entity
class Party(Entity):
    name: str


@lore.entity
class ClauseTemplate(Entity):
    """The approved clause library (seeded). Every drafted clause must
    instantiate one of these."""

    title: str


@lore.entity
class Contract(Entity):
    name: str


@lore.entity
class Section(Entity):
    heading: str
    # transitive + irreflexive: section nesting can never loop; a proposal
    # closing a cycle is rejected with the exact derived chain rendered.
    parent_section: Relation["Section"] | None = relation(
        transitive=True, irreflexive=True, default=None
    )


@lore.entity
class DefinedTerm(Entity):
    # Committed facts are immutable: re-proposing a term id with a different
    # definition is a single_value violation, zero code written for it. Two
    # conflicting definitions of "Confidential Information" is a classic real
    # contract bug — immutability *is* the check.
    term: str  # e.g. "Confidential Information"
    definition: str


@lore.entity
class Clause(Entity):
    text: str
    # "No invented clauses": every clause must instantiate a template from the
    # approved library, or the existence check rejects it — the drafting
    # analog of hallucinated cases (Mata v. Avianca) and invented policies
    # (the Air Canada chatbot).
    based_on: Relation[ClauseTemplate]
    in_section: Relation[Section]
    # Multi-valued: one edge per term id, each checked for existence.
    uses_terms: Relation[list[DefinedTerm]] = relation(default=[])
    references: Relation[Section] | None = relation(default=None)  # cross-refs resolve


# The subclasses carry the playbook-sensitive clause types; they inherit all
# of Clause's constraints via MRO type facts.


@lore.entity
class GoverningLawClause(Clause):
    law: str = one_of("DE", "NY")  # playbook: approved fora only
    # At most one governing-law clause per contract, enforced cross-turn.
    governs: Relation[Contract] = relation(max_per_target=1)


@lore.entity
class FeeClause(Clause):
    # One fee schedule per contract: a duplicate or "superseding" fee clause
    # is rejected instead of silently coexisting with the first.
    for_contract: Relation[Contract] = relation(max_per_target=1)
    monthly_fee_cents: int


@lore.entity
class LiabilityCapClause(Clause):
    # One limitation-of-liability per contract — two caps stating different
    # numbers is a real contract bug, and immutability means a committed cap
    # can never be "superseded" anyway.
    caps: Relation[Contract] = relation(max_per_target=1)
    cap_cents: int


@lore.entity
class IndemnityClause(Clause):
    indemnitor: Relation[Party]
    indemnitee: Relation[Party]


@lore.entity
class MFNClause(Clause):
    """Most-favored-nation commitment — no extra shape; the always-flag rule
    below is what makes it special."""


@lore.rule(
    message="Liability cap in {obj.id} is below the playbook floor of 12 months of fees."
)
def liability_cap_meets_floor(cap: LiabilityCapClause, graph: Graph) -> bool:
    """The walk-away floor: the cap must cover 12 months of fees.

    Ordering: at proposal time this rule sees only fee clauses already in
    the graph, so a cap drafted against an empty fee schedule passes
    vacuously — and committed facts are immutable, so it could never be
    repaired (a live agent found this wedge). Two layers close it: the
    engine re-checks committed caps differentially (a later fee that would
    flip this rule is itself rejected), and the companion rule below rejects
    the cap-before-fees ordering outright — rejecting the actual mistake
    beats rejecting the fee the deal memo requires. The caps_cover_fees goal
    below re-checks the floor at the output boundary as a belt-and-suspenders.
    """
    # graph.incoming returns EdgeFacts; rehydrate the subjects via graph.get.
    fees = [
        graph.get(e.subject_id)
        for e in graph.incoming(cap.caps, "FeeClause.for_contract")
    ]
    return all(cap.cap_cents >= 12 * fee.monthly_fee_cents for fee in fees)


@lore.rule(
    message="Liability cap {obj.id} was drafted before any fee clause — draft the "
    "fee schedule first, then cap liability against it."
)
def liability_cap_follows_fee_schedule(cap: LiabilityCapClause, graph: Graph) -> bool:
    # Vacuous floors are the trap: with no fees committed, any cap passes the
    # floor rule above and immutability makes it permanent. Reject instead —
    # a rejection is recoverable, a committed-too-low cap is not.
    return bool(graph.incoming(cap.caps, "FeeClause.for_contract"))


@lore.rule(message="Fee clause {obj.id} has a non-positive monthly fee.")
def fee_is_positive(fee: FeeClause, graph: Graph) -> bool:
    return fee.monthly_fee_cents > 0


@lore.rule(
    message="MFN clause {obj.id} is allowed but requires counsel review before signature.",
    severity="flag",
)
def mfn_needs_counsel_review(clause: MFNClause, graph: Graph) -> bool:
    # The always-flag pattern: every MFN clause commits + surfaces — the
    # escalate-to-counsel severity, next to the walk-away rejects above.
    return False


@lore.rule(
    message="Indemnity clause {obj.id} names {obj.indemnitor} as both indemnitor and indemnitee."
)
def indemnitor_is_not_indemnitee(clause: IndemnityClause, graph: Graph) -> bool:
    return clause.indemnitor != clause.indemnitee


# --- completeness goals: what a *finished* MSA must contain -------------------
# "At most one governing-law clause" is a rule — violable by a single proposal.
# "At least one" is only false when the agent claims to be done, so these run
# via session.check_goals() at the output boundary, never per proposal, and
# render into to_context() as "Goals — checked when you finish".


@lore.goal(message="Contract {obj.id} has no governing-law clause.")
def has_governing_law(contract: Contract, graph: Graph) -> bool:
    # max_per_target=1 on 'governs' already rejects a second clause at proposal
    # time, so at the boundary "exactly one" can only fail as zero.
    return len(graph.incoming(contract.id, "GoverningLawClause.governs")) == 1


@lore.goal(message="Contract {obj.id} has no fee clause.")
def has_fee_schedule(contract: Contract, graph: Graph) -> bool:
    return len(graph.incoming(contract.id, "FeeClause.for_contract")) >= 1


@lore.goal(message="Contract {obj.id} has no limitation-of-liability clause.")
def has_liability_cap(contract: Contract, graph: Graph) -> bool:
    return len(graph.incoming(contract.id, "LiabilityCapClause.caps")) >= 1


@lore.goal(
    message="A liability cap on {obj.id} is below the playbook floor of 12 months of fees."
)
def caps_cover_fees(contract: Contract, graph: Graph) -> bool:
    # Belt-and-suspenders: the differential rule re-check already rejects any
    # proposal that would flip liability_cap_meets_floor on a committed cap, so
    # this goal is unreachable — kept so the output boundary re-states the
    # walk-away floor over the final graph.
    fees = [
        graph.get(e.subject_id)
        for e in graph.incoming(contract.id, "FeeClause.for_contract")
    ]
    caps = [
        graph.get(e.subject_id)
        for e in graph.incoming(contract.id, "LiabilityCapClause.caps")
    ]
    return all(cap.cap_cents >= 12 * fee.monthly_fee_cents for cap in caps for fee in fees)


guard = lore.compile()
