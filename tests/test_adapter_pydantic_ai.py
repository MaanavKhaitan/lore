"""Offline repair loop through the Pydantic AI adapter (FunctionModel, no API key)."""

import pytest

pytest.importorskip("pydantic_ai")

from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, RetryPromptPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from lore import Entity, Lore, Relation, relation
from lore.adapters.pydantic_ai import validate_output
from lore.store import TypeFact

lore = Lore("adapter-test")


@lore.entity
class Customer(Entity):
    pass


@lore.entity
class Order(Entity):
    pass


@lore.entity
class Refund(Entity):
    refunds: Relation[Order] = relation(max_per_target=1)
    paid_to: Relation[Customer]


guard = lore.compile()

INVALID_ARGS = {"id": "ref_2", "refunds": "ord_1", "paid_to": "cust_1"}  # ord_1 already refunded
VALID_ARGS = {"id": "ref_2", "refunds": "ord_2", "paid_to": "cust_1"}


def scripted_model(messages, info: AgentInfo) -> ModelResponse:
    retrying = any(
        isinstance(part, RetryPromptPart)
        for message in messages
        for part in getattr(message, "parts", [])
    )
    return ModelResponse(
        parts=[
            ToolCallPart(
                tool_name=info.output_tools[0].name,
                args=VALID_ARGS if retrying else INVALID_ARGS,
            )
        ]
    )


def test_repair_loop_invalid_then_valid():
    session = guard.session(
        seed=[
            Customer(id="cust_1"),
            Order(id="ord_1"),
            Order(id="ord_2"),
            Refund(id="ref_1", refunds="ord_1", paid_to="cust_1"),
        ]
    )
    agent = Agent(FunctionModel(scripted_model), output_type=Refund)

    @agent.output_validator
    def check_against_lore(output: Refund) -> Refund:
        return validate_output(session, output)

    result = agent.run_sync("Issue the refund the customer asked for.")

    retries = [
        part
        for message in result.all_messages()
        for part in getattr(message, "parts", [])
        if isinstance(part, RetryPromptPart)
    ]
    assert len(retries) == 1
    assert "ord_1 has 2: ref_1 (seeded), ref_2 (proposed)" in retries[0].content

    assert result.output == Refund(**VALID_ARGS)
    assert TypeFact("ref_2", "Refund", "asserted") in session.facts  # committed on success
