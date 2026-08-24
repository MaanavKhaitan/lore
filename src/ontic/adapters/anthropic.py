"""Anthropic tool-use adapter: guard a tool function with an ontic session.

Nothing here imports the anthropic SDK — the helpers only produce the
``(content, is_error)`` and ``tool_result`` shapes a Messages-API tool loop
feeds back to the model, so they work with any hand-rolled loop.
"""

from __future__ import annotations

import functools
import json
from typing import Any, Callable, Sequence

from ..schema import Entity
from ..session import Session
from ..verdict import OntologyViolation

ToolResult = tuple[str, bool]  # (content, is_error) for a tool_result block


def guard_tool(session: Session) -> Callable[[Callable[..., Any]], Callable[..., ToolResult]]:
    """Decorator: the wrapped tool returns ``(entities, payload)``; the wrapper
    proposes the entities against ``session`` and turns the verdict into a
    tool result::

        @guard_tool(session)
        def issue_refund(order_id: str, amount: float, payout_account_id: str):
            refund = Refund(id=..., amount=amount, refunds=order_id,
                            paid_to=payout_account_id)
            entry = {"refund_id": refund.id, "order_id": order_id, "amount": amount}

            def issued():                        # runs only if the guard passes;
                LEDGER.append(entry)             # the proposal is rolled back if it raises
                return {"status": "issued", **entry}

            return refund, issued

        content, is_error = issue_refund(**tool_use.input)

    ``entities`` is one :class:`Entity` or a sequence. A rejection returns
    ``(repair_prompt, is_error=True)`` and leaves zero trace — side effects
    belong in a callable payload, which runs inside the guarded block (after
    validation, before commit). The payload (or its return value) is passed
    through if it is a string and JSON-encoded otherwise.
    """

    def decorate(fn: Callable[..., Any]) -> Callable[..., ToolResult]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> ToolResult:
            entities, payload = fn(*args, **kwargs)
            objs: Sequence[Entity] = [entities] if isinstance(entities, Entity) else list(entities)
            try:
                with session.guarded(*objs):
                    content = payload() if callable(payload) else payload
            except OntologyViolation as err:
                return err.repair_prompt, True
            return content if isinstance(content, str) else json.dumps(content), False

        return wrapper

    return decorate


def violation_result(tool_use_id: str, err: OntologyViolation) -> dict[str, Any]:
    """A ``tool_result`` content block carrying the repair prompt as an error —
    for loops that catch :class:`OntologyViolation` themselves."""
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": err.repair_prompt,
        "is_error": True,
    }
