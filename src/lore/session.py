"""Transactional sessions: propose → check → commit.

Facts are staged in an overlay and checked against committed ∪ staged; commit
is decided by the verdict, never by the writer. This prevents the three
failure modes of validate-after-ingest: retry pollution (a rejected attempt
leaves zero trace), side effects before judgment (proposals are checked as
hypotheticals), and partial writes (multi-object proposals land atomically).
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterable, Iterator

from .compile import Guard
from .engine import Graph, _most_specific, ground, run_checks
from .schema import Entity, LoreError
from .store import AttrFact, Fact, FactStore, InMemoryStore, LayeredView, TypeFact
from .verdict import LoreViolation, Verdict, Violation

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
            raise LoreError("nothing to commit: call propose() first")
        if not self._last_verdict.ok:
            rejects = len(self._last_verdict.rejects)
            raise LoreError(
                f"cannot commit: the last verdict has {rejects} reject-severity violation(s)"
            )
        self._committed.add(self._staged)
        self._clear_staged()

    def rollback(self) -> None:
        """Drop the staged facts (no-op if nothing is staged)."""
        self._clear_staged()

    # --- ergonomic wrappers over propose/commit/rollback ----------------------

    def check(self, *objs: Entity) -> Verdict:
        """Preflight — "can I do this?": validate ``objs`` with zero state change.

        The check runs against the committed world; the returned verdict
        carries the reasons. Afterwards the session is restored exactly as it
        was, so (unlike ``propose``) a pending staged proposal survives a
        ``check`` — it is safe to call anywhere.
        """
        saved = (self._staged, self._staged_store, self._last_verdict)
        try:
            return self.propose(*objs)
        finally:
            self._staged, self._staged_store, self._last_verdict = saved

    def try_commit(self, *objs: Entity) -> Verdict:
        """Propose ``objs``, commit if the verdict is ok, roll back otherwise.

        Never raises on violations — inspect the returned verdict. Flag-only
        verdicts are ok and commit (flags are surfaced, not blocking).
        """
        verdict = self.propose(*objs)
        if verdict.ok:
            self.commit()
        else:
            self.rollback()
        return verdict

    @contextmanager
    def guarded(self, *objs: Entity) -> Iterator[Verdict]:
        """Validate ``objs``, run the body for side effects, then commit::

            with session.guarded(refund) as verdict:
                ledger.append(entry)   # side effects go here

        The ordering guarantee for guarded tool calls: rejects raise
        :class:`~lore.verdict.LoreViolation` *before* the body runs (no
        side effects from invalid proposals); an exception in the body rolls
        the proposal back (no commit for failed effects); a clean exit
        commits. The yielded verdict exposes flag-severity violations. Don't
        call propose/commit/rollback inside the body (``check()`` is fine —
        it leaves no trace).
        """
        verdict = self.propose(*objs)
        if not verdict.ok:
            self.rollback()
            raise LoreViolation(verdict)
        snapshot = self._staged
        try:
            yield verdict
        except BaseException:
            self.rollback()
            raise
        if self._staged is not snapshot:
            self.rollback()
            raise LoreError(
                "the session was modified inside a guarded() block; "
                "propose/commit/rollback are not allowed there"
            )
        self.commit()

    # --- inspection ------------------------------------------------------------

    @property
    def graph(self) -> Graph:
        """Read-only view over committed ∪ staged — the same API rules receive.

        ``graph.get(id)`` rehydrates an entity; ``graph.incoming(id,
        "Class.field")`` lists the edges pointing at it.
        """
        return Graph(self._guard, LayeredView(self._committed, self._staged_store))

    def dump(self) -> str:
        """The session's world as readable text, grouped by entity::

            committed:
              cust_1: Customer (seed)  name='Ada'
              ref_1:  Refund  amount=40.0  refunds → ord_2  paid_to → cust_1
            staged:
              ref_2:  Refund  amount=40.0  refunds → ord_2  paid_to → cust_1
        """
        seed_ids = {
            f.node_id
            for f in self._committed.all_facts()
            if isinstance(f, TypeFact) and f.source == "seed"
        }
        lines = ["committed:"]
        lines += self._dump_section(self._committed.all_facts(), self._committed, seed_ids)
        if self._staged:
            view = LayeredView(self._committed, self._staged_store)
            lines.append("staged:")
            lines += self._dump_section(self._staged, view, seed_ids=set())
        return "\n".join(lines)

    def __repr__(self) -> str:
        committed = self._committed.all_facts()
        entities = len({f.node_id for f in committed if isinstance(f, TypeFact)})
        if self._last_verdict is None:
            verdict = "no verdict yet"
        elif self._last_verdict.violations:
            v = self._last_verdict
            verdict = f"last verdict: {len(v.rejects)} reject(s), {len(v.flags)} flag(s)"
        else:
            verdict = "last verdict: ok"
        return (
            f"<Session {self._guard.name!r}: {len(committed)} facts / "
            f"{entities} entities committed, {len(self._staged or ())} staged, {verdict}>"
        )

    def _dump_section(
        self, facts: Iterable[Fact], view: FactStore, seed_ids: set[str]
    ) -> list[str]:
        node_ids = list(
            dict.fromkeys(
                f.node_id if isinstance(f, TypeFact) else f.subject_id for f in facts
            )
        )
        if not node_ids:
            return ["  (empty)"]
        width = max(len(n) for n in node_ids) + 1
        return [
            f"  {node_id + ':':<{width}} {self._describe(node_id, view, seed_ids)}"
            for node_id in node_ids
        ]

    def _describe(self, node_id: str, view: FactStore, seed_ids: set[str]) -> str:
        compiled = _most_specific(self._guard, view.types_of(node_id))
        if compiled is None:
            return "(untyped)"
        parts = [compiled.cls.__name__ + (" (seed)" if node_id in seed_ids else "")]
        for field_name, attr in compiled.attributes.items():
            parts += [
                f"{field_name}={f.value!r}" for f in view.attrs(attr.attr) if f.subject_id == node_id
            ]
        for field_name, rel in compiled.relations.items():
            parts += [f"{field_name} → {e.object_id}" for e in view.edges_from(node_id, rel.predicate)]
        return "  ".join(parts)

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
