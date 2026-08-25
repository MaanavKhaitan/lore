"""The validate → explain → retry → pass loop against a real LLM.

A live agent (Anthropic API, manual tool-use loop) works a queue of refund
requests with lore checking at two points:

  1. **before a tool call takes effect** — `issue_refund` proposes the Refund
     against the session; a rejection returns the repair prompt as an
     ``is_error`` tool result and nothing is written to the ledger;
  2. **on the final output** — the agent's structured summary is proposed
     against the session, so a summary that violates the lore or claims
     refunds that were never issued is bounced back with a repair prompt.

The lookup tool plays a deliberately stale "orders DB": it knows nothing about
refund history or account types. The lore session (the refunds ledger) does —
which is the point: the guard deterministically catches what the model cannot
see in its context, and the repair prompt tells it why.

The system prompt embeds the lore via ``guard.to_context()`` (prevention: the
model knows the rules) while the session still checks every tool call and the
final output (detection: violations are caught anyway). Pass ``--no-context``
to omit the rules from the prompt and run the detection-only variant.

Run:  python examples/commerce/live_agent.py [--no-context]
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
    sys.exit("this demo needs the anthropic SDK: pip install 'lore[anthropic]'")

if not os.environ.get("ANTHROPIC_API_KEY"):
    sys.exit("set ANTHROPIC_API_KEY (env var or repo-root .env) to run this demo")

from world import Customer, Order, Refund, SupportRep, guard

from lore.adapters.anthropic import guard_tool

MODEL = "claude-opus-5"
MAX_TURNS = 15

# The refunds ledger (the lore session) knows ord_1 was already refunded and
# that rep_1 is a SupportRep. The orders DB below does not — realistic drift
# between systems, and exactly the gap the deterministic guard closes.
session = guard.session(
    seed=[
        Customer(id="cust_1", name="Ada"),
        SupportRep(id="rep_1", name="Sam"),
        Order(id="ord_1", status="paid", total=100.0, placed_by="cust_1"),
        Order(id="ord_2", status="paid", total=250.0, placed_by="cust_1"),
        Order(id="ord_3", status="shipped", total=80.0, placed_by="cust_1"),
        Refund(id="ref_0", amount=100.0, refunds="ord_1", paid_to="cust_1"),
    ]
)

# What the lookup tool can see: no refund history, no account types.
ORDERS_DB = {
    "cust_1": {
        "customer": {"id": "cust_1", "name": "Ada"},
        "orders": [
            {"id": "ord_1", "status": "paid", "total": 100.0},
            {"id": "ord_2", "status": "paid", "total": 250.0},
            {"id": "ord_3", "status": "shipped", "total": 80.0},
        ],
    }
}

TOOLS = [
    {
        "name": "get_customer_context",
        "description": (
            "Look up a customer and their orders in the orders database. "
            "This system does not track refund history or account types."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"customer_id": {"type": "string"}},
            "required": ["customer_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "issue_refund",
        "description": (
            "Issue a refund for an order, paid out to an account id. Returns the "
            "ledger entry on success; on failure, the error explains which domain "
            "rule the refund would violate."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "amount": {"type": "number"},
                "payout_account_id": {"type": "string"},
            },
            "required": ["order_id", "amount", "payout_account_id"],
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
            "reply_to_customer": {"type": "string"},
            "refunds_issued": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "refund_id": {"type": "string"},
                        "order_id": {"type": "string"},
                        "amount": {"type": "number"},
                        "paid_to": {"type": "string"},
                    },
                    "required": ["refund_id", "order_id", "amount", "paid_to"],
                    "additionalProperties": False,
                },
            },
            "requests_declined": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "request": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["request", "reason"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["reply_to_customer", "refunds_issued", "requests_declined"],
        "additionalProperties": False,
    },
}

INSTRUCTIONS = """You are a refunds agent for a small store. Use the tools to look
up context and issue refunds. The payments system deterministically enforces the
store's ledger and account rules, so you may attempt any refund the customer
asks for and rely on its error messages: if it rejects a refund as impossible,
explain that to the customer instead of retrying the same thing; if it rejects
a detail of the refund (for example the payout account), correct that detail
and retry. Report only refunds the issue_refund tool actually confirmed."""

# Prevention + detection from one declaration: the same lore that checks every
# tool call and the final output is rendered into the prompt so the model
# knows the rules up front. --no-context drops the rendering (detection only).
INCLUDE_CONTEXT = "--no-context" not in sys.argv
SYSTEM = guard.to_context() + "\n\n" + INSTRUCTIONS if INCLUDE_CONTEXT else INSTRUCTIONS

REQUEST = """Three refund requests from customer cust_1 (Ada) came in today:

1. "Please refund $40 of my order ord_2."
2. "I returned order ord_1 weeks ago and never got my money back. Refund the full $100."
3. "Refund $25 on order ord_3 — and send it to my other account, rep_1, please."

Handle all three."""


LEDGER: list[dict] = []
_refund_seq = 0


# Check point 1: guard_tool proposes the refund as a hypothetical BEFORE any
# side effect — the ledger write runs only if the guard passes, and a
# rejection becomes the (repair_prompt, is_error=True) tool result, zero trace.
@guard_tool(session)
def issue_refund(order_id: str, amount: float, payout_account_id: str):
    global _refund_seq
    _refund_seq += 1
    refund = Refund(
        id=f"ref_{_refund_seq}", amount=amount, refunds=order_id, paid_to=payout_account_id
    )
    entry = {
        "refund_id": refund.id,
        "order_id": order_id,
        "amount": amount,
        "paid_to": payout_account_id,
    }

    def issued():
        LEDGER.append(entry)
        return {"status": "issued", **entry}

    return refund, issued


def execute_tool(name: str, args: dict) -> tuple[str, bool]:
    """Run one tool call; returns (content, is_error)."""
    if name == "get_customer_context":
        record = ORDERS_DB.get(args["customer_id"])
        if record is None:
            return f"no customer with id {args['customer_id']!r}", True
        return json.dumps(record), False
    if name == "issue_refund":
        return issue_refund(**args)
    return f"unknown tool {name!r}", True


def check_final_output(report: dict) -> str | None:
    """Check point 2: the summary must be consistent with the committed ledger.

    Every claimed refund is re-proposed. Claims matching committed facts stage
    nothing; a claim that stages new facts was never actually issued, and a
    claim that draws violations contradicts the lore outright.
    """
    claims = [
        Refund(
            id=r["refund_id"],
            amount=r["amount"],
            refunds=r["order_id"],
            paid_to=r["paid_to"],
        )
        for r in report["refunds_issued"]
    ]
    verdict = session.propose(*claims)
    problems = [v.message for v in verdict.rejects]
    fabricated = sorted(
        {getattr(f, "subject_id", None) or f.node_id for f in session.staged_facts}
    )
    session.rollback()
    if fabricated:
        problems.append(
            f"Refund(s) {', '.join(fabricated)} do not match the refund ledger — "
            "report only refunds the issue_refund tool actually confirmed, with "
            "the exact refund_id, amount, and payout account it returned."
        )
    if not problems:
        return None
    lines = [f"Your summary is inconsistent with the refund ledger ({len(problems)} problem(s)):"]
    lines += [f"  {i}. {p}" for i, p in enumerate(problems, start=1)]
    lines.append("Produce a corrected summary.")
    return "\n".join(lines)


def indented(text: str, prefix: str = " " * 15) -> str:
    return "\n".join(prefix + line for line in text.splitlines())


def main() -> None:
    client = anthropic.Anthropic()
    messages: list[dict] = [{"role": "user", "content": REQUEST}]
    total_in = total_out = 0

    if INCLUDE_CONTEXT:
        print("system prompt: lore rules included (run with --no-context to omit them)")
    else:
        print("system prompt: instructions only (detection-only mode)")
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
                label = "[lore reject]" if is_error and block.name == "issue_refund" else "[tool result] "
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

        print("\n=== final output validated against the ledger — consistent ===")
        print(f"refunds committed this run: {json.dumps(LEDGER, indent=2)}")
        print(f"tokens: {total_in} in / {total_out} out")
        return

    sys.exit(f"agent did not converge within {MAX_TURNS} turns")


if __name__ == "__main__":
    main()
