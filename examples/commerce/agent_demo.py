"""The full validate → explain → retry → pass loop, offline.

A scripted pydantic_ai FunctionModel plays the agent: its first output is a
double refund, the lore rejects it with a repair prompt, and its retry is
valid. No API key needed; the run is fully deterministic.

Run:  python examples/commerce/agent_demo.py
"""

import sys

try:
    from pydantic_ai import Agent
    from pydantic_ai.messages import (
        ModelResponse,
        RetryPromptPart,
        ToolCallPart,
        UserPromptPart,
    )
    from pydantic_ai.models.function import AgentInfo, FunctionModel
except ImportError:
    sys.exit("this demo needs pydantic_ai: pip install 'lore[pydantic-ai]'")

from world import Customer, Order, Refund, SupportRep, guard

from lore.adapters.pydantic_ai import validate_output

session = guard.session(
    seed=[
        Customer(id="cust_1", name="Ada"),
        SupportRep(id="rep_1", name="Sam"),
        Order(id="ord_1", status="refunded", total=100.0, placed_by="cust_1"),
        Order(id="ord_2", status="paid", total=250.0, placed_by="cust_1"),
        Refund(id="ref_0", amount=100.0, refunds="ord_1", paid_to="cust_1"),
    ]
)

FIRST_ATTEMPT = {"id": "ref_1", "amount": 40.0, "refunds": "ord_1", "paid_to": "cust_1"}
RETRY_ATTEMPT = {"id": "ref_1", "amount": 40.0, "refunds": "ord_2", "paid_to": "cust_1"}


def scripted_model(messages, info: AgentInfo) -> ModelResponse:
    """Stands in for the LLM: double-refunds first, corrects itself on retry."""
    retrying = any(
        isinstance(part, RetryPromptPart)
        for message in messages
        for part in getattr(message, "parts", [])
    )
    args = RETRY_ATTEMPT if retrying else FIRST_ATTEMPT
    return ModelResponse(parts=[ToolCallPart(tool_name=info.output_tools[0].name, args=args)])


agent = Agent(FunctionModel(scripted_model), output_type=Refund)


@agent.output_validator
def check_against_lore(output: Refund) -> Refund:
    return validate_output(session, output)


result = agent.run_sync("Ada wants a refund of $40 on her order.")

print("=== message exchange ===")
for message in result.all_messages():
    for part in message.parts:
        if isinstance(part, UserPromptPart):
            print(f"[user]            {part.content}")
        elif isinstance(part, ToolCallPart):
            print(f"[agent output]    {part.args}")
        elif isinstance(part, RetryPromptPart):
            print("[lore repair]")
            for line in part.content.splitlines():
                print(f"                  {line}")

print("\n=== final, committed output ===")
print(result.output)
