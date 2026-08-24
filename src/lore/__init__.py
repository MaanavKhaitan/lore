"""lore — deterministic guardrails for agent outputs.

Pydantic validates the shape of one object; lore validates whether your
agent's outputs make sense in your world: cross-object, cross-turn, stateful
relational constraints, with violations rendered as natural-language repair
prompts for the agent.
"""

from .schema import Entity, Lore, LoreError, Relation, one_of, relation
from .session import Session
from .verdict import Verdict, Violation

__all__ = [
    "Entity",
    "Lore",
    "LoreError",
    "Relation",
    "Session",
    "Verdict",
    "Violation",
    "one_of",
    "relation",
]

__version__ = "0.1.0"
