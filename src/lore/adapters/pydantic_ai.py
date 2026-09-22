"""Pydantic AI adapter: plug a Session into an output validator or tool body."""

from __future__ import annotations

from typing import Sequence, TypeVar

from ..schema import Entity
from ..session import Session
from . import as_entities

OutputT = TypeVar("OutputT", bound="Entity | Sequence[Entity]")


def validate_output(session: Session, output: OutputT) -> OutputT:
    """Validate an agent output against the session's lore.

    Call inside an ``@agent.output_validator`` (or a tool body). If the verdict
    is ok the proposal is committed and ``output`` is returned; otherwise the
    proposal is rolled back (zero trace) and ``pydantic_ai.ModelRetry`` is
    raised with the rendered repair prompt.
    """
    try:
        from pydantic_ai import ModelRetry
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise ImportError(
            "the pydantic-ai adapter needs pydantic_ai installed: "
            "pip install 'agent-lore[pydantic-ai]'"
        ) from exc

    verdict = session.try_commit(*as_entities(output))
    if verdict.ok:
        return output
    raise ModelRetry(verdict.repair_prompt())
