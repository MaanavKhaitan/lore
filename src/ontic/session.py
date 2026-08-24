"""Transactional sessions: propose → check → commit.

Facts are staged in an overlay and checked against committed ∪ staged; commit
is decided by the verdict, never by the writer. This prevents the three
failure modes of validate-after-ingest: retry pollution (a rejected attempt
leaves zero trace), side effects before judgment (proposals are checked as
hypotheticals), and partial writes (multi-object proposals land atomically).
"""

from __future__ import annotations

from typing import Iterable

from .compile import Guard
from .engine import ground, run_checks
from .schema import Entity, OntologyError
from .store import AttrFact, Fact, InMemoryStore, LayeredView, TypeFact
from .verdict import Verdict, Violation

__all__ = ["Session", "Verdict", "Violation"]


class Session:
    """One agent run's fact graph, validated proposal by proposal.

    Sessions are **single-threaded by design** — use one session per agent run
    and never share it across threads. The session is closed-world over its
    seed plus committed facts: what it doesn't contain doesn't exist.
    """

    def __init__(self, guard: Guard, seed: Iterable[Entity] = ()) -> None:
        self._guard = guard
        self._committed = InMemoryStore()
        for obj in seed:
            self._committed.add(ground(guard, obj, "seed"))
        self._staged: list[Fact] | None = None
        self._staged_store = InMemoryStore()
        self._last_verdict: Verdict | None = None

    def propose(self, *objs: Entity) -> Verdict:
        """Stage ``objs`` as hypothetical facts and check the combined graph.

        Any previously staged (uncommitted) facts are discarded first — an
        implicit rollback, which is exactly right for agent retry loops.
        """
        staged: list[Fact] = []
        for obj in objs:
            for fact in ground(self._guard, obj, "asserted"):
                # Re-asserting an already-committed fact adds no information:
                # keep it out of the staged set so it can't re-fire violations
                # against purely pre-existing state.
                if not self._already_committed(fact) and fact not in staged:
                    staged.append(fact)
        staged_store = InMemoryStore()
        staged_store.add(staged)
        view = LayeredView(self._committed, staged_store)
        verdict = Verdict(run_checks(self._guard, view, staged))
        self._staged = staged
        self._staged_store = staged_store
        self._last_verdict = verdict
        return verdict

    def commit(self) -> None:
        """Merge the staged facts into the committed graph (atomic).

        Flag-severity violations are committable; rejects are not.
        """
        if self._staged is None or self._last_verdict is None:
            raise OntologyError("nothing to commit: call propose() first")
        if not self._last_verdict.ok:
            rejects = len(self._last_verdict.rejects)
            raise OntologyError(
                f"cannot commit: the last verdict has {rejects} reject-severity violation(s)"
            )
        self._committed.add(self._staged)
        self._clear_staged()

    def rollback(self) -> None:
        """Drop the staged facts (no-op if nothing is staged)."""
        self._clear_staged()

    @property
    def facts(self) -> tuple[Fact, ...]:
        """The committed facts, for debugging and tests."""
        return self._committed.all_facts()

    @property
    def staged_facts(self) -> tuple[Fact, ...]:
        """The currently staged (proposed, uncommitted) facts."""
        return tuple(self._staged or ())

    @property
    def last_verdict(self) -> Verdict | None:
        return self._last_verdict

    def _clear_staged(self) -> None:
        self._staged = None
        self._staged_store = InMemoryStore()
        self._last_verdict = None

    def _already_committed(self, fact: Fact) -> bool:
        """Content-level membership in the committed store (ignores ``source``)."""
        if isinstance(fact, TypeFact):
            return fact.type_name in self._committed.types_of(fact.node_id)
        if isinstance(fact, AttrFact):
            return any(
                a.subject_id == fact.subject_id and a.value == fact.value
                for a in self._committed.attrs(fact.attr)
            )
        return any(
            e.object_id == fact.object_id
            for e in self._committed.edges_from(fact.subject_id, fact.predicate)
        )
