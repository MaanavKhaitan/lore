"""The Anthropic tool-loop adapter — pure result shapes, no SDK required."""

import json

from lore import Entity, Lore, LoreViolation, Relation, relation
from lore.adapters.anthropic import guard_tool, violation_result
from lore.store import TypeFact

lore = Lore("anthropic-adapter-test")


@lore.entity
class Order(Entity):
    pass


@lore.entity
class Refund(Entity):
    refunds: Relation[Order] = relation(max_per_target=1)


guard = lore.compile()


def make_session():
    return guard.session(
        seed=[Order(id="ord_1"), Order(id="ord_2"), Refund(id="ref_1", refunds="ord_1")]
    )


def test_guard_tool_success_runs_effect_and_commits():
    session = make_session()
    ledger = []

    @guard_tool(session)
    def issue_refund(order_id: str):
        refund = Refund(id="ref_2", refunds=order_id)

        def issued():
            ledger.append(refund.id)
            return {"status": "issued", "refund_id": refund.id}

        return refund, issued

    content, is_error = issue_refund("ord_2")
    assert not is_error
    assert json.loads(content) == {"status": "issued", "refund_id": "ref_2"}
    assert ledger == ["ref_2"]
    assert TypeFact("ref_2", "Refund", "asserted") in session.facts


def test_guard_tool_rejection_is_error_with_zero_trace():
    session = make_session()
    ledger = []

    @guard_tool(session)
    def issue_refund(order_id: str):
        refund = Refund(id="ref_2", refunds=order_id)
        return refund, lambda: ledger.append(refund.id)

    before = session.facts
    content, is_error = issue_refund("ord_1")  # ord_1 is already refunded
    assert is_error
    assert "ord_1 has 2: ref_1 (already committed), ref_2 (proposed)" in content
    assert ledger == []  # the effect never ran
    assert session.facts == before and session.staged_facts == ()


def test_guard_tool_accepts_plain_payloads_and_entity_sequences():
    session = make_session()

    @guard_tool(session)
    def create_orders():
        return [Order(id="ord_3"), Order(id="ord_4")], "created"

    assert create_orders() == ("created", False)
    assert TypeFact("ord_3", "Order", "asserted") in session.facts
    assert TypeFact("ord_4", "Order", "asserted") in session.facts


def test_violation_result_shape():
    session = make_session()
    caught = None
    try:
        with session.guarded(Refund(id="ref_2", refunds="ord_1")):
            pass
    except LoreViolation as err:
        caught = err
    assert caught is not None
    assert violation_result("toolu_123", caught) == {
        "type": "tool_result",
        "tool_use_id": "toolu_123",
        "content": caught.repair_prompt,
        "is_error": True,
    }
