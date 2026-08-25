"""Violations and verdicts — the checkable result of a proposal."""

from __future__ import annotations

from dataclasses import dataclass, field

from .schema import Severity
from .store import Fact


@dataclass(frozen=True)
class Violation:
    check: str  # "max_per_target", "range", "rule:paid_to_buyer", "goal:has_fee", ...
    severity: Severity
    message: str  # fully rendered English, names concrete ids
    subjects: tuple[str, ...] = ()  # node ids involved
    # For violations found on derived edges: the base facts that produced them
    # (the chain the message renders). Empty for direct violations.
    provenance: tuple[Fact, ...] = ()


@dataclass
class Verdict:
    """Outcome of one ``Session.propose`` (or ``check_goals``). Deterministically
    ordered and deduped."""

    violations: list[Violation] = field(default_factory=list)

    def __post_init__(self) -> None:
        unique = dict.fromkeys(self.violations)
        self.violations = sorted(unique, key=lambda v: (v.check, v.subjects, v.message))

    @property
    def ok(self) -> bool:
        """True iff there are no reject-severity violations (flags are committable)."""
        return not self.rejects

    @property
    def rejects(self) -> list[Violation]:
        return [v for v in self.violations if v.severity == "reject"]

    @property
    def flags(self) -> list[Violation]:
        return [v for v in self.violations if v.severity == "flag"]

    def __repr__(self) -> str:
        if not self.violations:
            return "<Verdict: ok>"
        return f"<Verdict: {len(self.rejects)} reject(s), {len(self.flags)} flag(s)>"

    def repair_prompt(self) -> str:
        """Natural-language repair prompt for the agent (reject-severity only)."""
        rejects = self.rejects
        if not rejects:
            return ""
        if all(v.check.startswith("goal:") for v in rejects):
            # A goal-only verdict comes from the output boundary: the world is
            # valid, just unfinished — ask for completion, not correction.
            lines = [f"The work is not finished: it violates {len(rejects)} goal(s) of this domain:"]
            lines += [f"  {i}. {v.message}" for i, v in enumerate(rejects, start=1)]
            lines.append("Complete the missing items, then finish.")
            return "\n".join(lines)
        lines = [f"Your output violates {len(rejects)} rule(s) of this domain:"]
        lines += [f"  {i}. {v.message}" for i, v in enumerate(rejects, start=1)]
        lines.append(
            "Produce a corrected output that satisfies every rule. If the requested "
            "action is impossible under these rules, say so instead of retrying it."
        )
        return "\n".join(lines)


class LoreViolation(Exception):
    """A proposal drew reject-severity violations.

    Raised by ``Session.guarded()``. This is the catch-and-convert seam for
    integrations: the Pydantic AI adapter turns it into ``ModelRetry``, the
    Anthropic helper into an ``is_error`` tool result, bare tool loops catch
    it themselves. ``str(exc)`` is the rendered repair prompt.
    """

    def __init__(self, verdict: Verdict) -> None:
        super().__init__(verdict.repair_prompt())
        self.verdict = verdict

    @property
    def repair_prompt(self) -> str:
        return self.verdict.repair_prompt()
