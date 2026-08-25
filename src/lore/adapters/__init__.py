"""Framework adapters: deliver a session verdict through a framework's own
retry channel — a raised exception (Pydantic AI), an error-flagged tool
result (Anthropic), and so on.

The core imports no agent framework; adapters stay thin (<50 lines) —
anything bigger belongs in core. Writing one? See CONTRIBUTING.md.
"""

from __future__ import annotations

from typing import Sequence

from ..schema import Entity

__all__ = ["as_entities"]


def as_entities(output: Entity | Sequence[Entity]) -> list[Entity]:
    """Normalize an adapter's input — one entity or a sequence — to a list."""
    return [output] if isinstance(output, Entity) else list(output)
