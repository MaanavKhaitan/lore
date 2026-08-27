"""The playbook-enforcement demo — no API key, no LLM, fully deterministic.

Seeds the approved clause library and one empty contract, then scripts the
beats of a drafting session: an agent proposes clauses one by one, and every
playbook deviation — invented clauses, undefined or silently-redefined terms,
an off-playbook forum, a duplicate governing-law clause, a sub-floor liability
cap, self-indemnification, circular sections — is caught deterministically,
with the repair prompt that would be fed back. Walk-away terms reject; the
MFN escalation commits with a flag; session.check_goals() checks at the
output boundary what no per-proposal rule can.

Run:  python examples/contracts/demo.py
      python examples/contracts/demo.py --html contracts.html   # + the viz export
      python examples/contracts/demo.py --json ui/fixtures  # + viewer payloads
"""

import argparse
import json
from pathlib import Path

from lore.viz import TraceRecorder, spec_json, to_html

from world import (
    Clause,
    ClauseTemplate,
    Contract,
    DefinedTerm,
    FeeClause,
    GoverningLawClause,
    IndemnityClause,
    LiabilityCapClause,
    MFNClause,
    Party,
    Section,
    guard,
)

parser = argparse.ArgumentParser(description="The playbook-enforcement demo.")
parser.add_argument("--html", metavar="PATH", help="export the run as a self-contained HTML viewer")
parser.add_argument("--json", metavar="DIR", help="write spec/trace/snapshot JSON payloads (ui fixtures)")
args = parser.parse_args()

recorder = TraceRecorder()
session = guard.session(
    recorder=recorder,
    seed=[
        Contract(id="contract_1", name="Acme–Vendor Master Services Agreement"),
        Party(id="party_acme", name="Acme Corp"),
        Party(id="party_vendor", name="Vendor Co"),
        # The approved clause library — the only language a clause may instantiate.
        ClauseTemplate(id="tpl_payment", title="Payment Terms"),
        ClauseTemplate(id="tpl_confidentiality", title="Confidentiality"),
        ClauseTemplate(id="tpl_governing_law", title="Governing Law"),
        ClauseTemplate(id="tpl_liability_cap", title="Limitation of Liability"),
        ClauseTemplate(id="tpl_indemnity", title="Indemnification"),
        ClauseTemplate(id="tpl_mfn", title="Most Favored Nation"),
        Section(id="sec_1", heading="General"),
    ]
)


def beat(n: int, title: str) -> None:
    print(f"--- Beat {n}: {title}")


def submit(*objs):
    """One drafting move: propose atomically, commit if ok, roll back otherwise
    (try_commit), printing what an agent harness would see."""
    for obj in objs:
        print(f"    {obj!r}")
    verdict = session.try_commit(*objs)
    if verdict.ok:
        print("    verdict: OK — committed")
        if verdict.flags:
            print(f"    verdict.flags ({len(verdict.flags)}):")
            for flag in verdict.flags:
                print(f"      - {flag.message}")
    else:
        print("    verdict: REJECTED")
        print("    repair prompt fed back to the agent:")
        for line in verdict.repair_prompt().splitlines():
            print(f"      {line}")
    print()
    return verdict


beat(1, "The playbook, rendered")
print("    guard.to_context() renders the playbook as prompt English — this same")
print("    declaration is the system prompt AND the enforcement below. The rules")
print("    and goals:")
context = guard.to_context()
for line in context[context.index("Rules:") :].splitlines():
    print(f"      {line}")
print()

beat(2, "A valid clause and its new defined term, proposed atomically")
submit(
    Clause(
        id="cl_conf",
        text="Each party shall protect the other's Confidential Information.",
        based_on="tpl_confidentiality",
        in_section="sec_1",
        uses_terms=["term_ci"],
    ),
    DefinedTerm(
        id="term_ci",
        term="Confidential Information",
        definition="non-public information disclosed under this Agreement",
    ),
)

beat(3, "An invented clause — hallucinated authority, the Air Canada move")
submit(
    Clause(
        id="cl_handshake",
        text="The parties may settle any dispute by handshake.",
        based_on="tpl_handshake_deal",  # not in the approved library
        in_section="sec_1",
    )
)

beat(4, "A two-term clause — one term defined, one nobody defined; then the fix")
# uses_terms grounds one edge per id, checked per element: term_ci (committed
# in beat 2) resolves, the undefined one is rejected on its own.
submit(
    Clause(
        id="cl_losses",
        text="Vendor shall indemnify Acme against Indemnified Losses arising "
        "from unauthorized disclosure of Confidential Information.",
        based_on="tpl_indemnity",
        in_section="sec_1",
        uses_terms=["term_ci", "term_indemnified_losses"],
    )
)
submit(
    Clause(
        id="cl_losses",
        text="Vendor shall indemnify Acme against Indemnified Losses arising "
        "from unauthorized disclosure of Confidential Information.",
        based_on="tpl_indemnity",
        in_section="sec_1",
        uses_terms=["term_ci", "term_indemnified_losses"],
    ),
    DefinedTerm(
        id="term_indemnified_losses",
        term="Indemnified Losses",
        definition="losses, damages, and costs arising from third-party claims",
    ),
)

beat(5, "Silent redefinition of the beat-2 term — immutability is the check")
# No custom rule anywhere for this: committed facts are immutable, so a
# changed definition on the same id is a single_value violation for free.
submit(
    DefinedTerm(
        id="term_ci",
        term="Confidential Information",
        definition="only information marked confidential in writing",
    )
)

beat(6, "An off-playbook forum; then corrected to an approved one")
submit(
    GoverningLawClause(
        id="cl_govlaw",
        text="This Agreement is governed by the laws of Texas.",
        law="TX",
        based_on="tpl_governing_law",
        in_section="sec_1",
        governs="contract_1",
    )
)
submit(
    GoverningLawClause(
        id="cl_govlaw",
        text="This Agreement is governed by the laws of Delaware.",
        law="DE",
        based_on="tpl_governing_law",
        in_section="sec_1",
        governs="contract_1",
    )
)

beat(7, "A second governing-law clause for the same contract (cross-turn!)")
submit(
    GoverningLawClause(
        id="cl_govlaw_ny",
        text="This Agreement is governed by the laws of New York.",
        law="NY",
        based_on="tpl_governing_law",
        in_section="sec_1",
        governs="contract_1",
    )
)

# The draft as it stands here — governing law settled, no fees or cap yet —
# kept for beat 12's premature-finish counterfactual.
mid_draft = session.snapshot()

beat(8, "The fee clause; then a liability cap below the 12-month floor")
# Fee before cap: natural drafting order, and enforced — a cap proposed
# before any fee clause is rejected outright (liability_cap_follows_fee_schedule
# in world.py closes the fee-after-cap hole; beat 12 re-checks at the boundary).
submit(
    FeeClause(
        id="cl_fee",
        text="Acme shall pay Vendor $1,000 per month.",
        monthly_fee_cents=100_000,
        based_on="tpl_payment",
        in_section="sec_1",
        for_contract="contract_1",
    )
)
submit(
    LiabilityCapClause(
        id="cl_cap",
        text="Vendor's aggregate liability shall not exceed $5,000.",
        cap_cents=500_000,  # floor is 12 × 100_000 = 1_200_000
        based_on="tpl_liability_cap",
        in_section="sec_1",
        caps="contract_1",
    )
)
submit(
    LiabilityCapClause(
        id="cl_cap",
        text="Vendor's aggregate liability shall not exceed $15,000.",
        cap_cents=1_500_000,
        based_on="tpl_liability_cap",
        in_section="sec_1",
        caps="contract_1",
    )
)

beat(9, "The escalation: an MFN clause — commits, but flagged for counsel")
print("    The playbook's two severities: walk-away terms reject (beats 6–8);")
print("    escalate-to-counsel terms flag — committed, surfaced, not blocking.")
submit(
    MFNClause(
        id="cl_mfn",
        text="Vendor grants Acme pricing no less favorable than any other customer's.",
        based_on="tpl_mfn",
        in_section="sec_1",
    )
)

beat(10, "Self-indemnification: Acme indemnifying itself")
submit(
    IndemnityClause(
        id="cl_ind",
        text="Acme shall indemnify Acme against all claims.",
        indemnitor="party_acme",
        indemnitee="party_acme",
        based_on="tpl_indemnity",
        in_section="sec_1",
    )
)

beat(11, "Circular sections — the same machinery as HR's cycle detection")
submit(
    Section(id="sec_2", heading="Definitions", parent_section="sec_3"),
    Section(id="sec_3", heading="Term and Termination", parent_section="sec_2"),
)

beat(12, "Finalize — session.check_goals() at the output boundary")
# "At most one X" is a rule — violable by any single proposal. "At least one
# X" is only false when the agent claims to be done, so the four completeness
# goals live in world.py as @lore.goal: rendered into the beat-1 prompt
# (prevention) and checked here by one call (detection), zero state change.
verdict = session.check_goals()
print(f"    session.check_goals() on the finished draft: {verdict!r}")
print()
print("    Had the agent finished after beat 7 — no fee schedule, no cap —")
print("    the same call would have fed back:")
for line in guard.restore(mid_draft).check_goals().repair_prompt().splitlines():
    print(f"      {line}")

# --- optional viz exports (printed output above is unchanged without flags) -----
if args.json:
    out_dir = Path(args.json)
    out_dir.mkdir(parents=True, exist_ok=True)
    snapshot_blob = session.snapshot()
    (out_dir / "spec.json").write_text(json.dumps(spec_json(guard), indent=2))
    (out_dir / "trace.json").write_text(recorder.dumps())
    (out_dir / "snapshot.json").write_text(snapshot_blob)
    print(f"\nwrote spec.json, trace.json, snapshot.json to {out_dir}/")
if args.html:
    print(f"\nwrote {to_html(guard, trace=recorder, out=args.html)}")
