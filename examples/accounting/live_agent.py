"""An AP clerk with the lore as its books — a live agent, manual tool-use loop.

The agent works a queue of vendor bills with lore checking at two points:

  1. **before a tool call takes effect** — `record_payment` and
     `post_journal_entry` propose their entities against the session (an entry
     atomically with its postings, one sequence through `guard_tool`); a
     rejection returns the repair prompt as an ``is_error`` tool result and
     nothing is written to the ledger;
  2. **on the final output** — the agent's structured summary is proposed
     against the session, so a summary that violates the lore or claims an
     entry or payment that was never posted is bounced back too.

The AP inbox is deliberately stale: it lists bills with suggested accounts
and knows nothing about what has already been posted or reconciled. The lore
session (the books) does — one bill is fine, one is for an invoice another
clerk already paid (cross-turn double payment), and one suggests an account
that does not exist. The guard deterministically catches all of it, and the
repair prompts tell the model why.

Run:  python examples/accounting/live_agent.py
Needs ANTHROPIC_API_KEY (env var, or a repo-root .env). Costs a few cents.
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
    sys.exit("this demo needs the anthropic SDK: pip install 'agent-lore[anthropic]'")

if not os.environ.get("ANTHROPIC_API_KEY"):
    sys.exit("set ANTHROPIC_API_KEY (env var or repo-root .env) to run this demo")

from world import Invoice, JournalEntry, Payment, Period, Posting, guard, load_chart

from lore.adapters.anthropic import guard_tool

MODEL = "claude-opus-5"
MAX_TURNS = 20

# The books (the lore session) know inv_102 was paid by another clerk last
# week; the AP inbox below does not — realistic drift between systems, and
# exactly the gap the deterministic guard closes.
CHART = load_chart()
session = guard.session(
    seed=[
        *CHART,
        Period(id="per_2026_07", label="2026-07", status="closed"),
        Period(id="per_2026_08", label="2026-08", status="open"),
        Invoice(id="inv_101", vendor="Acme Supply Co.", total_cents=23_500),
        Invoice(id="inv_102", vendor="Globex Property Mgmt", total_cents=88_000),
        Invoice(id="inv_103", vendor="Initech Interiors", total_cents=45_000),
        Payment(id="pay_900", amount_cents=88_000, reconciles="inv_102"),
    ]
)

# What the inbox can see: no payment history, no chart of accounts.
AP_INBOX = [
    {
        "bill_id": "bill_1",
        "vendor": "Acme Supply Co.",
        "invoice_id": "inv_101",
        "amount_cents": 23_500,
        "description": "printer paper and toner",
        "suggested_expense_account": "acct_supplies",
    },
    {
        "bill_id": "bill_2",
        "vendor": "Globex Property Mgmt",
        "invoice_id": "inv_102",
        "amount_cents": 88_000,
        "description": "August rent",
        "suggested_expense_account": "acct_rent",
    },
    {
        "bill_id": "bill_3",
        "vendor": "Initech Interiors",
        "invoice_id": "inv_103",
        "amount_cents": 45_000,
        "description": "workstation chairs",
        "suggested_expense_account": "acct_office_furniture",  # not in the chart
    },
]

TOOLS = [
    {
        "name": "get_open_bills",
        "description": (
            "List the bills sitting in the AP inbox, with each bill's amount, "
            "invoice id, and a suggested expense account. The inbox is not "
            "connected to the ledger: it does not know what has already been "
            "posted or which invoices are already reconciled."
        ),
        "strict": True,
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "record_payment",
        "description": (
            "Record a payment reconciling a vendor invoice, in integer cents. "
            "Returns the payment record on success; on failure, the error "
            "explains which domain rule the payment would violate."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string"},
                "amount_cents": {"type": "integer"},
            },
            "required": ["invoice_id", "amount_cents"],
            "additionalProperties": False,
        },
    },
    {
        "name": "post_journal_entry",
        "description": (
            "Post a journal entry with its postings (amounts in integer cents). "
            "The entry and its postings are validated together and land "
            "atomically: on failure nothing is written, and the error explains "
            "which domain rule the entry would violate."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "memo": {"type": "string"},
                "period_id": {"type": "string"},
                "postings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "side": {"type": "string", "enum": ["debit", "credit"]},
                            "amount_cents": {"type": "integer"},
                            "account_id": {"type": "string"},
                        },
                        "required": ["side", "amount_cents", "account_id"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["memo", "period_id", "postings"],
            "additionalProperties": False,
        },
    },
]

# The final response is constrained to this schema, so the "agent output"
# check point has something machine-readable to validate.
OUTPUT_FORMAT = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {
            "reply_to_controller": {"type": "string"},
            "payments_recorded": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "payment_id": {"type": "string"},
                        "invoice_id": {"type": "string"},
                        "amount_cents": {"type": "integer"},
                    },
                    "required": ["payment_id", "invoice_id", "amount_cents"],
                    "additionalProperties": False,
                },
            },
            "entries_posted": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "entry_id": {"type": "string"},
                        "memo": {"type": "string"},
                        "period_id": {"type": "string"},
                        "postings": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "posting_id": {"type": "string"},
                                    "side": {"type": "string"},
                                    "amount_cents": {"type": "integer"},
                                    "account_id": {"type": "string"},
                                },
                                "required": ["posting_id", "side", "amount_cents", "account_id"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["entry_id", "memo", "period_id", "postings"],
                    "additionalProperties": False,
                },
            },
            "bills_declined": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "bill_id": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["bill_id", "reason"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["reply_to_controller", "payments_recorded", "entries_posted", "bills_declined"],
        "additionalProperties": False,
    },
}

_chart_lines = "\n".join(f"- {a.id}: {a.name} ({type(a).__name__})" for a in CHART)

INSTRUCTIONS = f"""You are the accounts-payable clerk for a small company. Work
the AP inbox with the tools. All amounts are integer cents.

Chart of accounts (the only valid account ids):
{_chart_lines}

Periods: per_2026_07 (July 2026, closed), per_2026_08 (August 2026, open).

For each bill, in order: first record_payment against the bill's invoice id;
only if the payment is accepted, post_journal_entry for the expense in the
open period — debit the bill's expense account, credit acct_cash. The ledger
deterministically enforces the books' rules, so you may attempt what a bill
suggests and rely on its error messages: if it rejects an action as
impossible, decline that bill and say why; if it rejects a detail (an account,
a period), correct the detail and retry — pick the closest account from the
chart. Respond with tool calls (starting with get_open_bills) until every bill
is handled; only then produce your reply, the final report to the controller.
Report only entries and payments the tools actually confirmed, echoing their
exact ids."""

# The README's recommended pattern: the same declaration that checks the
# outputs also renders as prompt English, so prevention and detection agree.
SYSTEM = guard.to_context() + "\n\n" + INSTRUCTIONS

REQUEST = """Work today's AP inbox: fetch the open bills and handle each one.
When you are done, summarize what you paid, what you posted, and anything you
declined."""


GL: list[dict] = []  # the general ledger the guarded payloads write
_seq = {"pay": 0, "je": 0, "po": 0}


def _next(prefix: str) -> str:
    _seq[prefix] += 1
    return f"{prefix}_{_seq[prefix]}"


# Check point 1: guard_tool proposes the entities as hypotheticals BEFORE any
# side effect — the ledger write runs only if the guard passes, and a
# rejection becomes the (repair_prompt, is_error=True) tool result, zero trace.
@guard_tool(session)
def record_payment(invoice_id: str, amount_cents: int):
    payment = Payment(id=_next("pay"), amount_cents=amount_cents, reconciles=invoice_id)
    record = {"payment_id": payment.id, "invoice_id": invoice_id, "amount_cents": amount_cents}

    def recorded():
        GL.append({"kind": "payment", **record})
        return {"status": "recorded", **record}

    return payment, recorded


@guard_tool(session)
def post_journal_entry(memo: str, period_id: str, postings: list):
    entry = JournalEntry(id=_next("je"), memo=memo, period=period_id)
    lines = [
        Posting(
            id=_next("po"),
            side=p["side"],
            amount_cents=p["amount_cents"],
            account=p["account_id"],
            entry=entry.id,
        )
        for p in postings
    ]
    record = {
        "entry_id": entry.id,
        "memo": memo,
        "period_id": period_id,
        "postings": [
            {"posting_id": po.id, "side": po.side, "amount_cents": po.amount_cents,
             "account_id": po.account}
            for po in lines
        ],
    }

    def posted():
        GL.append({"kind": "journal_entry", **record})
        return {"status": "posted", **record}

    # The entry and its postings go through the guard as one sequence — the
    # atomic-proposal discipline entry_balances requires (see world.py).
    return [entry, *lines], posted


def execute_tool(name: str, args: dict) -> tuple[str, bool]:
    """Run one tool call; returns (content, is_error)."""
    if name == "get_open_bills":
        return json.dumps(AP_INBOX), False
    if name == "record_payment":
        return record_payment(**args)
    if name == "post_journal_entry":
        return post_journal_entry(**args)
    return f"unknown tool {name!r}", True


def check_final_output(report: dict) -> str | None:
    """Check point 2: the summary must be consistent with the committed books.

    Every claimed payment, entry, and posting is re-proposed. Claims matching
    committed facts stage nothing; a claim that stages new facts was never
    actually written, and a claim that draws violations contradicts the lore
    outright.
    """
    claims: list = [
        Payment(id=p["payment_id"], amount_cents=p["amount_cents"], reconciles=p["invoice_id"])
        for p in report["payments_recorded"]
    ]
    for e in report["entries_posted"]:
        claims.append(JournalEntry(id=e["entry_id"], memo=e["memo"], period=e["period_id"]))
        claims += [
            Posting(
                id=p["posting_id"],
                side=p["side"],
                amount_cents=p["amount_cents"],
                account=p["account_id"],
                entry=e["entry_id"],
            )
            for p in e["postings"]
        ]
    verdict = session.propose(*claims)
    problems = [v.message for v in verdict.rejects]
    fabricated = sorted(
        {getattr(f, "subject_id", None) or f.node_id for f in session.staged_facts}
    )
    session.rollback()
    if fabricated:
        problems.append(
            f"Record(s) {', '.join(fabricated)} do not match the ledger — report "
            "only payments and entries the tools actually confirmed, with the "
            "exact ids, amounts, accounts, and period they returned."
        )
    paid = {p["invoice_id"] for p in report["payments_recorded"]}
    declined = {b["bill_id"] for b in report["bills_declined"]}
    unworked = [
        b["bill_id"]
        for b in AP_INBOX
        if b["invoice_id"] not in paid and b["bill_id"] not in declined
    ]
    if unworked:
        problems.append(
            f"Bill(s) {', '.join(unworked)} from the AP inbox are neither paid "
            "nor declined — work every bill before reporting."
        )
    if not problems:
        return None
    lines = [f"Your summary is inconsistent with the ledger ({len(problems)} problem(s)):"]
    lines += [f"  {i}. {p}" for i, p in enumerate(problems, start=1)]
    lines.append("Produce a corrected summary.")
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
                guarded = block.name in ("record_payment", "post_journal_entry")
                label = "[lore reject]" if is_error and guarded else "[tool result] "
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

        print("\n=== final output validated against the books — consistent ===")
        print(f"ledger writes this run: {json.dumps(GL, indent=2)}")
        print(f"tokens: {total_in} in / {total_out} out")
        return

    sys.exit(f"agent did not converge within {MAX_TURNS} turns")


if __name__ == "__main__":
    main()
