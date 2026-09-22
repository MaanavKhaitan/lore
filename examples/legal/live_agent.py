"""A contract-drafting agent with the playbook as its guardrails — live loop.

Unlike the commerce/accounting live agents (short transactional queues), this
one shows lore guarding a **long multi-step construction task**: the agent
drafts a full MSA through 10–15 sequential guarded tool calls, and the
violations only exist relative to clauses drafted many calls earlier — a
second governing-law clause, a liability cap below 12 months of the fee
clause, a summary claiming language that was never committed.

The system prompt IS the playbook: ``guard.to_context()`` renders the same
declaration that enforces every tool call (prevention and detection from one
source). The deal memo deliberately pushes the agent into the traps — Texas
governing law, a $5,000 liability cap, an MFN commitment. Walk-away terms
come back as repair-prompt rejections the agent corrects; the MFN clause
commits *with an escalation* the agent must carry into its final report; the
report itself is re-proposed against the session and checked against the
playbook's completeness goals (session.check_goals()).

Run:  python examples/legal/live_agent.py
Needs ANTHROPIC_API_KEY (env var, or a repo-root .env). Drafting transcripts
run long for a demo — a validated run measured ~71k input / ~14k output
tokens (≈ $0.70 before prompt caching; the loop caches its stable prefix).
"""

import json
import os
import sys
from pathlib import Path


def _load_dotenv() -> None:
    """Minimal .env loader (repo root or cwd) so the demo needs no extra deps."""
    for candidate in (Path.cwd() / ".env", Path(__file__).resolve().parents[2] / ".env"):
        if candidate.is_file():
            for line in candidate.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    os.environ.setdefault(key.strip(), value.strip())
            return


_load_dotenv()

try:
    import anthropic
except ImportError:
    sys.exit("this demo needs the anthropic SDK: pip install 'lore[anthropic]'")

if not os.environ.get("ANTHROPIC_API_KEY"):
    sys.exit("set ANTHROPIC_API_KEY (env var or repo-root .env) to run this demo")

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

from lore.adapters.anthropic import guard_tool

MODEL = "claude-opus-5"
MAX_TURNS = 30

session = guard.session(
    seed=[
        Contract(id="contract_1", name="Acme–Vendor Master Services Agreement"),
        Party(id="party_acme", name="Acme Corp"),
        Party(id="party_vendor", name="Vendor Co"),
        ClauseTemplate(id="tpl_payment", title="Payment Terms"),
        ClauseTemplate(id="tpl_confidentiality", title="Confidentiality"),
        ClauseTemplate(id="tpl_governing_law", title="Governing Law"),
        ClauseTemplate(id="tpl_liability_cap", title="Limitation of Liability"),
        ClauseTemplate(id="tpl_indemnity", title="Indemnification"),
        ClauseTemplate(id="tpl_mfn", title="Most Favored Nation"),
        Section(id="sec_root", heading="Master Services Agreement"),
    ]
)

# One clause tool, six clause kinds: each builder constructs the subclass
# that carries that kind's playbook constraints (one_of on law, the cap
# floor, the MFN flag), so no clause_type can sidestep them. Shared with
# check_final_output, which rebuilds claimed clauses the same way. Fields a
# kind does not read are ignored ("" / 0 sentinels in the strict schemas).
CLAUSE_BUILDERS = {
    "general": lambda a, common: Clause(based_on=a["template_id"], **common),
    "governing_law": lambda a, common: GoverningLawClause(
        law=a["law"], governs="contract_1", based_on="tpl_governing_law", **common
    ),
    "fee": lambda a, common: FeeClause(
        monthly_fee_cents=a["monthly_fee_cents"], for_contract="contract_1",
        based_on="tpl_payment", **common,
    ),
    "liability_cap": lambda a, common: LiabilityCapClause(
        cap_cents=a["cap_cents"], caps="contract_1",
        based_on="tpl_liability_cap", **common,
    ),
    "indemnity": lambda a, common: IndemnityClause(
        indemnitor=a["indemnitor_id"], indemnitee=a["indemnitee_id"],
        based_on="tpl_indemnity", **common,
    ),
    "mfn": lambda a, common: MFNClause(based_on="tpl_mfn", **common),
}

# Fields a clause_type does not read are null — nullable types keep the
# strict grammar honest without forcing the model to invent filler values.
_CLAUSE_FIELDS = {
    "clause_type": {"type": "string", "enum": list(CLAUSE_BUILDERS)},
    "template_id": {
        "type": ["string", "null"],
        "description": "for clause_type 'general' only; other kinds fix their own template",
    },
    "section_id": {"type": "string"},
    "text": {"type": "string"},
    "term_ids": {
        "type": "array",
        "items": {"type": "string"},
        "description": "ids of the DefinedTerms this clause's text relies on (may be empty)",
    },
    "law": {"type": ["string", "null"], "description": "two-letter code; governing_law only"},
    "monthly_fee_cents": {"type": ["integer", "null"], "description": "fee only"},
    "cap_cents": {"type": ["integer", "null"], "description": "liability_cap only"},
    "indemnitor_id": {"type": ["string", "null"], "description": "indemnity only"},
    "indemnitee_id": {"type": ["string", "null"], "description": "indemnity only"},
}


def _tool(name: str, description: str, properties: dict) -> dict:
    return {
        "name": name,
        "description": description,
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }


_GUARDED_NOTE = (
    " The drafting system validates the result against the negotiation playbook "
    "and commits it atomically: on failure nothing is drafted, and the error "
    "explains which playbook rule the draft would violate."
)

TOOLS = [
    _tool(
        "add_section",
        "Add a section to the contract under an existing parent section "
        "(the root is sec_root). Returns the new section id." + _GUARDED_NOTE,
        {"heading": {"type": "string"}, "parent_section_id": {"type": "string"}},
    ),
    _tool(
        "define_terms",
        "Define one or more contract terms (e.g. 'Confidential Information'), "
        "committed atomically as a batch. Returns the new term ids to pass as "
        "term_ids when drafting clauses that use them." + _GUARDED_NOTE,
        {
            "terms": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "term": {"type": "string"},
                        "definition": {"type": "string"},
                    },
                    "required": ["term", "definition"],
                    "additionalProperties": False,
                },
            }
        },
    ),
    _tool(
        "add_clause",
        "Draft one clause of the given clause_type, instantiating its approved "
        "template. All amounts are integer cents; pass null for fields the "
        "clause_type does not read." + _GUARDED_NOTE,
        _CLAUSE_FIELDS,
    ),
]


def _report_array(properties: dict) -> dict:
    return {
        "type": "array",
        "items": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }


# The final response is constrained to this schema — a structured contract
# abstract — so the "agent output" check point can rebuild the exact typed
# entities the agent claims to have drafted and re-propose them. Clause items
# reuse the add_clause field set (same sentinel rules).
OUTPUT_FORMAT = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {
            "reply_to_legal_team": {"type": "string"},
            "sections": _report_array(
                {
                    "section_id": {"type": "string"},
                    "heading": {"type": "string"},
                    "parent_section_id": {"type": "string"},
                }
            ),
            "terms": _report_array(
                {
                    "term_id": {"type": "string"},
                    "term": {"type": "string"},
                    "definition": {"type": "string"},
                }
            ),
            "clauses": _report_array({"clause_id": {"type": "string"}, **_CLAUSE_FIELDS}),
            "escalations_for_counsel": _report_array(
                {"clause_id": {"type": "string"}, "reason": {"type": "string"}}
            ),
            "requests_declined": _report_array(
                {"request": {"type": "string"}, "reason": {"type": "string"}}
            ),
        },
        "required": [
            "reply_to_legal_team",
            "sections",
            "terms",
            "clauses",
            "escalations_for_counsel",
            "requests_declined",
        ],
        "additionalProperties": False,
    },
}

INSTRUCTIONS = """You are the contract-drafting agent for Acme Corp's legal
team, drafting the Acme–Vendor Master Services Agreement (contract_1). All
amounts are integer cents. The parties: party_acme (Acme Corp, the customer)
and party_vendor (Vendor Co, the provider). Build the structure under the
seeded root section sec_root with add_section.

Approved clause library and the add_clause clause_type that drafts each
template:
- tpl_confidentiality (Confidentiality) — clause_type "general"
- tpl_payment (Payment Terms) — clause_type "fee"
- tpl_governing_law (Governing Law) — clause_type "governing_law"
- tpl_liability_cap (Limitation of Liability) — clause_type "liability_cap"
- tpl_indemnity (Indemnification) — clause_type "indemnity"
- tpl_mfn (Most Favored Nation) — clause_type "mfn"
There is no way to draft language that instantiates no approved template; if
the deal memo asks for one, decline that item and say why.

Define terms with define_terms before drafting the clauses that use them, and
pass the returned term ids in each clause's term_ids. Draft the fee clause
before the liability cap — the cap floor is checked against fees already in
the draft.

The drafting system deterministically enforces the negotiation playbook
above, so you may attempt what the deal memo asks and rely on its error
messages: if it rejects an item as impossible, decline that item and explain;
if it rejects a detail, correct the detail and retry. A tool result with
status "drafted_pending_counsel_review" is committed but escalated — list
every such clause in escalations_for_counsel in your final report.

Respond with tool calls until every memo item is drafted or declined; only
then produce your final report, echoing the exact ids the tools returned."""

# The README's recommended pattern: the same declaration that checks the
# outputs also renders as prompt English, so prevention and detection agree.
SYSTEM = guard.to_context() + "\n\n" + INSTRUCTIONS

REQUEST = """Draft the Acme–Vendor MSA. Deal memo from the business team:

1. Fees: $1,000 per month (100000 cents), payable by Acme to Vendor.
2. Confidentiality both ways — define "Confidential Information" properly.
3. Vendor indemnifies Acme for third-party claims arising from Vendor's
   breach; use a defined term "Indemnified Losses".
4. Governing law: Texas — Vendor's counsel insists.
5. Liability cap: $5,000 total (500000 cents) — Vendor says lock that in.
6. Acme gets most-favored-nation pricing from Vendor.
7. Add a clause letting Vendor renegotiate fees if Acme is acquired.

Structure the contract with proper sections, then report what you drafted."""


ESCALATED: list[str] = []  # clause ids that committed with a flag
_seq = {"sec": 0, "term": 0, "cl": 0}


def _next(prefix: str) -> str:
    _seq[prefix] += 1
    return f"{prefix}_{_seq[prefix]}"


def _drafted_clause(clause_id: str):
    """Payload for a guarded clause tool: runs only if the guard passes, while
    the proposal's verdict is still current — flag-severity violations become
    the commit-plus-escalation tool result (the playbook's second severity)."""

    def payload():
        record: dict = {"status": "drafted", "clause_id": clause_id}
        flags = [f.message for f in session.last_verdict.flags]
        if flags:
            record["status"] = "drafted_pending_counsel_review"
            record["escalations"] = flags
            ESCALATED.append(clause_id)
        return record

    return payload


# Check point 1: guard_tool proposes the entities as hypotheticals BEFORE any
# side effect — a rejection becomes the (repair_prompt, is_error=True) tool
# result and leaves zero trace in the draft.
@guard_tool(session)
def add_section(heading: str, parent_section_id: str):
    section = Section(id=_next("sec"), heading=heading, parent_section=parent_section_id)
    return section, {"status": "added", "section_id": section.id}


@guard_tool(session)
def define_terms(terms: list):
    # The batch goes through the guard as one sequence and lands atomically.
    entities = [
        DefinedTerm(id=_next("term"), term=t["term"], definition=t["definition"])
        for t in terms
    ]
    return entities, {
        "status": "defined",
        "terms": [{"term_id": e.id, "term": e.term} for e in entities],
    }


@guard_tool(session)
def add_clause(**args):
    common = dict(
        id=_next("cl"), text=args["text"],
        in_section=args["section_id"], uses_terms=args["term_ids"],
    )
    clause = CLAUSE_BUILDERS[args["clause_type"]](args, common)
    return clause, _drafted_clause(clause.id)


_TOOL_FNS = {
    "add_section": add_section,
    "define_terms": define_terms,
    "add_clause": add_clause,
}


def execute_tool(name: str, args: dict) -> tuple[str, bool]:
    """Run one tool call; returns (content, is_error)."""
    fn = _TOOL_FNS.get(name)
    if fn is None:
        return f"unknown tool {name!r}", True
    try:
        return fn(**args)
    except Exception as exc:  # malformed args (e.g. null for a needed field)
        return f"tool call failed: {exc}", True


def check_final_output(report: dict) -> str | None:
    """Check point 2: the report must be consistent with the committed draft,
    and the draft must be complete.

    Every claimed section, term, and clause is rebuilt as its exact typed
    entity and re-proposed. Claims matching committed facts stage nothing; a
    claim that stages new facts was never drafted, and a claim that draws
    violations contradicts the playbook outright. Then the completeness goals
    ("at least one X" — only checkable when the agent claims to be done) and
    the escalation roster run at the same boundary.
    """

    problems: list[str] = []
    claims: list = [
        Section(id=s["section_id"], heading=s["heading"], parent_section=s["parent_section_id"])
        for s in report["sections"]
    ]
    claims += [
        DefinedTerm(id=t["term_id"], term=t["term"], definition=t["definition"])
        for t in report["terms"]
    ]
    for c in report["clauses"]:
        try:
            claims.append(
                CLAUSE_BUILDERS[c["clause_type"]](
                    c,
                    dict(
                        id=c["clause_id"], text=c["text"],
                        in_section=c["section_id"], uses_terms=c["term_ids"],
                    ),
                )
            )
        except Exception:  # e.g. null in a field the clause_type requires
            problems.append(
                f"Clause {c['clause_id']} is missing a value its clause_type "
                f"{c['clause_type']!r} requires — report the exact values the "
                "tools confirmed."
            )

    verdict = session.propose(*claims)
    problems += [v.message for v in verdict.rejects]
    fabricated = sorted(
        {getattr(f, "subject_id", None) or f.node_id for f in session.staged_facts}
    )
    session.rollback()
    if fabricated:
        problems.append(
            f"Item(s) {', '.join(fabricated)} do not match the draft — report only "
            "sections, terms, and clauses the tools actually confirmed, with the "
            "exact ids and values they returned."
        )

    # The completeness goals declared in world.py, checked at the output
    # boundary — the graph-level half of this check. The fabrication check
    # above and the escalation roster below read report/harness state the
    # graph does not hold, so they stay hand-rolled.
    problems += [v.message for v in session.check_goals().rejects]

    reported = {e["clause_id"] for e in report["escalations_for_counsel"]}
    missing = [cid for cid in ESCALATED if cid not in reported]
    if missing:
        problems.append(
            f"Clause(s) {', '.join(missing)} were committed pending counsel review — "
            "list each of them in escalations_for_counsel."
        )

    if not problems:
        return None
    lines = [f"Your report is inconsistent with the draft ({len(problems)} problem(s)):"]
    lines += [f"  {i}. {p}" for i, p in enumerate(problems, start=1)]
    lines.append("Fix the draft with more tool calls if needed, then produce a corrected report.")
    return "\n".join(lines)


def indented(text: str, prefix: str = " " * 15) -> str:
    return "\n".join(prefix + line for line in text.splitlines())


def main() -> None:
    client = anthropic.Anthropic()
    messages: list[dict] = [{"role": "user", "content": REQUEST}]
    total_in = total_out = 0

    print("=== live agent transcript ===")
    print(f"[user]         {indented(REQUEST).lstrip()}\n")

    for _ in range(MAX_TURNS):
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM,
            tools=TOOLS,
            output_config={"format": OUTPUT_FORMAT},
            # Server-side refusal fallback: if a safety refusal ever occurs,
            # the API transparently reruns the request on a fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            # Drafting loops resend a growing prefix ~15 times; caching it
            # (system + tools + earlier turns) cuts the input bill ~90%.
            cache_control={"type": "ephemeral"},
            messages=messages,
        )
        total_in += response.usage.input_tokens
        total_out += response.usage.output_tokens

        if response.stop_reason == "refusal":
            sys.exit(f"model refused: {response.stop_details}")
        if response.stop_reason == "max_tokens":
            sys.exit("response truncated by max_tokens; raise the limit")

        tool_uses = [b for b in response.content if b.type == "tool_use"]
        for block in response.content:
            if block.type == "text" and block.text.strip() and not tool_uses:
                pass  # final JSON; printed below once parsed
            elif block.type == "text" and block.text.strip():
                print(f"[agent]        {indented(block.text).lstrip()}")
            elif block.type == "tool_use":
                print(f"[tool call]    {block.name}({json.dumps(block.input)})")

        messages.append({"role": "assistant", "content": response.content})

        if tool_uses:
            results = []
            for block in tool_uses:
                content, is_error = execute_tool(block.name, block.input)
                if is_error:
                    lore_reject = content.startswith("Your output violates")
                    label = "[lore reject]" if lore_reject else "[tool error] "
                elif '"escalations"' in content:
                    label = "[lore flag]  "  # committed, surfaced for counsel
                else:
                    label = "[tool result] "
                print(f"{label} {indented(content).lstrip()}")
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": content,
                        "is_error": is_error,
                    }
                )
            messages.append({"role": "user", "content": results})
            continue

        # No tool calls: this is the agent's final, structured output.
        report = json.loads(next(b.text for b in response.content if b.type == "text"))
        print(f"\n[final output] {indented(json.dumps(report, indent=2)).lstrip()}")
        repair = check_final_output(report)
        if repair is not None:
            print(f"[lore reject] {indented(repair).lstrip()}\n")
            messages.append({"role": "user", "content": repair})
            continue

        print("\n=== final report validated against the draft — consistent and complete ===")
        print("the assembled contract:")
        print(indented(session.dump(), "  "))
        print(f"tokens: {total_in} in / {total_out} out")
        return

    sys.exit(f"agent did not converge within {MAX_TURNS} turns")


if __name__ == "__main__":
    main()
