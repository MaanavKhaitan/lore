"""Transactional sessions: propose → check → commit.

Facts are staged in an overlay and checked against committed ∪ staged; commit
is decided by the verdict, never by the writer. This prevents the three
failure modes of validate-after-ingest: retry pollution (a rejected attempt
leaves zero trace), side effects before judgment (proposals are checked as
hypotheticals), and partial writes (multi-object proposals land atomically).

Sessions are durable across processes: ``snapshot()`` serializes the committed
graph to a JSON blob and ``restore()`` rehydrates it, so stateless deployments
(one worker per agent turn) persist the blob wherever they already keep state.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from typing import Any, Iterable, Iterator, Protocol

from .compile import Guard
from .engine import _safe, explicit_nones, ground, run_checks, run_goals
from .graph import Graph, _most_specific
from .infer import build_context, derive
from .schema import Entity, LoreError
from .snapshot import decode_snapshot, encode_snapshot
from .store import AttrFact, Fact, FactStore, InMemoryStore, LayeredView, TypeFact
from .verdict import LoreViolation, Verdict, Violation

__all__ = ["Session", "SessionRecorder", "Verdict", "Violation"]


class SessionRecorder(Protocol):
    """Observer for session lifecycle events (e.g. ``lore.viz.TraceRecorder``).

    Called synchronously at each transaction boundary; a recorder must never
    raise or mutate the session. Event kinds and payloads: ``session_start``
    (facts), ``propose`` (facts, verdict), ``commit`` (step — ``None`` for an
    empty commit), ``rollback``, ``check`` (facts, verdict), ``check_goals``
    (verdict), ``snapshot``, ``restore`` (facts).
    """

    def on_event(self, kind: str, session: "Session", **data: Any) -> None: ...


class Session:
    """One agent run's fact graph, validated proposal by proposal.

    Sessions are **single-threaded by design** — use one session per agent run
    and never share it across threads. The session is closed-world over its
    seed plus committed facts: what it doesn't contain doesn't exist.
    """

    def __init__(
        self,
        guard: Guard,
        seed: Iterable[Entity] = (),
        *,
        validate_seed: bool = True,
        recorder: SessionRecorder | None = None,
    ) -> None:
        self._guard = guard
        self._committed = InMemoryStore()
        for obj in seed:
            self._committed.add(ground(guard, obj, "seed"))
        self._staged: list[Fact] | None = None
        self._staged_store = InMemoryStore()
        self._last_verdict: Verdict | None = None
        self._step = 0
        self._recorder = recorder
        self._recording_paused = False
        if validate_seed and self._committed.all_facts():
            self._validate_seed()
        self._emit("session_start", facts=self._committed.all_facts())

    def _emit(self, kind: str, **data: Any) -> None:
        if self._recorder is not None and not self._recording_paused:
            self._recorder.on_event(kind, self, **data)

    def _validate_world(self) -> Verdict:
        """Run the full derive+check pass with every committed fact treated as
        staged — the whole-world consistency check behind seed validation and
        snapshot revalidation."""
        facts = list(self._committed.all_facts())
        return Verdict(run_checks(build_context(self._guard, self._committed, facts)))

    def _validate_seed(self) -> None:
        """An inconsistent seed fails loudly instead of silently exempting
        itself from the lore forever."""
        verdict = self._validate_world()
        if verdict.rejects:
            lines = [
                f"invalid seed: {len(verdict.rejects)} reject-severity violation(s) "
                "(pass validate_seed=False to skip this check):"
            ]
            lines += [f"  - {v.message}" for v in verdict.rejects]
            raise LoreError("\n".join(lines))

    def propose(self, *objs: Entity) -> Verdict:
        """Stage ``objs`` as hypothetical facts and check the combined graph.

        Any previously staged (uncommitted) facts are discarded first — an
        implicit rollback, which is exactly right for agent retry loops.
        """
        staged: list[Fact] = []
        retractions: list[Violation] = []
        for obj in objs:
            for fact in ground(self._guard, obj, "asserted"):
                # Re-asserting an already-committed fact adds no information:
                # keep it out of the staged set so it can't re-fire violations
                # against purely pre-existing state.
                if not self._already_committed(fact) and fact not in staged:
                    staged.append(fact)
            retractions.extend(self._retraction_violations(obj))
        staged_store = InMemoryStore()
        staged_store.add(staged)
        view = LayeredView(self._committed, staged_store)
        # Derivations are computed locally per proposal and passed through the
        # context — never stored on the session, so check()'s save/restore of
        # (_staged, _staged_store, _last_verdict) stays complete.
        verdict = Verdict(
            run_checks(build_context(self._guard, view, staged, base=self._committed))
            + retractions
        )
        # The implicit rollback is emitted only here, where the prior staged
        # facts are actually discarded — never before grounding/checks, which
        # can raise and leave the old proposal (and the session) untouched.
        if self._staged is not None:
            self._emit("rollback")
        self._staged = staged
        self._staged_store = staged_store
        self._last_verdict = verdict
        self._emit("propose", facts=tuple(staged), verdict=verdict)
        return verdict

    def commit(self) -> None:
        """Merge the staged facts into the committed graph (atomic).

        Flag-severity violations are committable; rejects are not. Successful
        non-empty commits are numbered 1, 2, 3… and every fact is stamped with
        its commit's step (seeds stay at step 0); idempotent re-commits of an
        empty staged set consume no step number.
        """
        if self._staged is None or self._last_verdict is None:
            raise LoreError("nothing to commit: call propose() first")
        if not self._last_verdict.ok:
            rejects = len(self._last_verdict.rejects)
            raise LoreError(
                f"cannot commit: the last verdict has {rejects} reject-severity violation(s)"
            )
        step: int | None = None
        if self._staged:
            assert all(f.source != "derived" for f in self._staged)
            self._step += 1
            step = self._step
            self._committed.add(replace(f, step=self._step) for f in self._staged)
        self._clear_staged()
        self._emit("commit", step=step)

    def rollback(self) -> None:
        """Drop the staged facts (no-op if nothing is staged)."""
        had_staged = self._staged is not None
        self._clear_staged()
        if had_staged:
            self._emit("rollback")

    # --- ergonomic wrappers over propose/commit/rollback ----------------------

    def check(self, *objs: Entity) -> Verdict:
        """Preflight — "can I do this?": validate ``objs`` with zero state change.

        The check runs against the committed world; the returned verdict
        carries the reasons. Afterwards the session is restored exactly as it
        was, so (unlike ``propose``) a pending staged proposal survives a
        ``check`` — it is safe to call anywhere.
        """
        saved = (self._staged, self._staged_store, self._last_verdict)
        # A check is a preflight, not a proposal: pause recording so the
        # internal propose() doesn't emit, then report one "check" event.
        self._recording_paused = True
        try:
            verdict = self.propose(*objs)
            checked = tuple(self._staged or ())
        finally:
            self._recording_paused = False
            self._staged, self._staged_store, self._last_verdict = saved
        self._emit("check", facts=checked, verdict=verdict)
        return verdict

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

    # --- the output boundary ----------------------------------------------------

    def check_goals(self) -> Verdict:
        """Run every ``@lore.goal`` on the committed world — "am I done?".

        Rules catch invalid facts per proposal; goals catch *missing* ones, so
        they run only here, when the agent claims to be finished. Every goal
        fires once per committed instance of its target class (subclass
        instances count; zero instances pass vacuously), each receiving the
        same derived :class:`~lore.graph.Graph` rules see. Zero state change:
        nothing is staged and the last proposal's verdict is untouched.

        Violations carry ``check="goal:<name>"`` in the ordinary
        :class:`Verdict`, so ``verdict.ok``, ``.flags``, and
        ``repair_prompt()`` work unchanged — a goal-only failure renders the
        boundary-appropriate "The work is not finished" prompt. Finishing with
        an uncommitted proposal is a bug, so pending staged facts raise — the
        same turn-boundary discipline as :meth:`snapshot`.
        """
        if self._staged is not None:
            raise LoreError(
                "cannot check goals with a pending proposal: commit() or rollback() first"
            )
        verdict = Verdict(run_goals(self._guard, self._committed))
        self._emit("check_goals", verdict=verdict)
        return verdict

    # --- persistence -----------------------------------------------------------

    def snapshot(self) -> str:
        """Serialize the committed graph to a self-contained JSON blob.

        The blob is storage-agnostic — put it in Redis, a Postgres row, or a
        file, and rehydrate next turn with :meth:`Guard.restore`::

            redis.set(f"lore:{run_id}", session.snapshot())
            ...
            session = guard.restore(redis.get(f"lore:{run_id}"))

        Restore reproduces the committed store exactly (facts, insertion
        order, seed/asserted sources, commit steps), so verdicts — including
        their "already committed at step N" messages — and ``graph`` hydration
        are identical across the round-trip. Snapshots capture
        turn boundaries: persisting a pending proposal is almost certainly a
        bug, so snapshotting with staged facts raises — commit or roll back
        first. The blob is stamped with ``guard.fingerprint``; restoring
        under a changed declarative schema fails loudly. Python rule/goal
        bodies are not fingerprinted; restore reruns them only with
        ``revalidate=True``.
        """
        if self._staged is not None:
            raise LoreError(
                "cannot snapshot with a pending proposal: commit() or rollback() first"
            )
        blob = encode_snapshot(self._guard, self._committed.all_facts())
        self._emit("snapshot")
        return blob

    @classmethod
    def restore(
        cls,
        guard: Guard,
        blob: str | bytes,
        *,
        revalidate: bool = False,
        recorder: SessionRecorder | None = None,
    ) -> "Session":
        """Rehydrate a session from a :meth:`snapshot` blob.

        By default the restored facts are trusted as-is (they passed checks
        when they were committed) — nothing is re-validated, so anything the
        blob's session was allowed to hold (e.g. a ``validate_seed=False``
        inconsistency, such as an id typed under two incomparable classes
        whose unpicked branch's rules are skipped) carries over silently.
        The fingerprint covers the declarative schema and rule/goal metadata
        but not Python rule bodies, so pass ``revalidate=True`` to re-run
        every check over the restored world and fail loudly if it no longer
        satisfies the *current* rules — the safety net for edited rule logic,
        at the cost of one full check pass.

        Raises :class:`~lore.schema.LoreError` if the blob is not a snapshot,
        uses an unknown snapshot format, was taken under a lore whose
        fingerprint differs from ``guard.fingerprint``, or (with
        ``revalidate=True``) draws reject-severity violations under the
        current rules.
        """
        facts = decode_snapshot(guard, blob)
        # Construct with the recorder detached: a restore is not a fresh
        # session start, so it reports one "restore" event instead.
        session = cls(guard)
        session._committed.add(facts)
        # Resume the commit-step counter where the snapshot left off, so
        # post-restore commits keep numbering (and messages) correct.
        session._step = max((f.step for f in facts), default=0)
        if revalidate:
            verdict = session._validate_world()
            if verdict.rejects:
                lines = [
                    f"snapshot is invalid under the current rules: "
                    f"{len(verdict.rejects)} reject-severity violation(s):"
                ]
                lines += [f"  - {v.message}" for v in verdict.rejects]
                raise LoreError("\n".join(lines))
        session._recorder = recorder
        session._emit("restore", facts=session._committed.all_facts())
        return session

    # --- inspection ------------------------------------------------------------

    @property
    def graph(self) -> Graph:
        """Read-only view over committed ∪ staged — the same API rules receive.

        ``graph.get(id)`` rehydrates an entity; ``graph.incoming(id,
        "Class.field")`` lists the edges pointing at it; ``graph.reachable(id,
        "Class.field")`` follows the relation through mirrors and transitive
        closure (derivations are recomputed on access — session graphs are
        tiny).
        """
        view = LayeredView(self._committed, self._staged_store)
        derivations = derive(self._guard, view)
        return Graph(
            self._guard,
            view,
            mirrors=LayeredView(derivations.sym_mirrors, derivations.inv_mirrors),
            closure=derivations.closure,
        )

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

    def _retraction_violations(self, obj: Entity) -> list[Violation]:
        """Reject an explicit ``None`` on a field with a committed value.

        Grounding drops ``None`` fields, so such a proposal would otherwise be
        a silent no-op: the writer believes the value was cleared while lore
        keeps enforcing the old one — in a ``guarded()`` tool that permits an
        external clearing side effect the graph never records. Facts are
        append-only; state transitions must be modeled as new facts (e.g. a
        status value or a superseding entity), never as retractions. A ``None``
        on a field with no committed value stays a no-op: it is
        indistinguishable from "no statement" and normal for partial objects.
        """
        out = []
        for field_name, predicate, is_relation in explicit_nones(self._guard, obj):
            if is_relation:
                # ids render like every other message; attribute values render
                # with repr like the single_value/one_of listings do.
                values = ", ".join(
                    _safe(e.object_id)
                    for e in self._committed.edges_from(obj.id, predicate)
                )
            else:
                values = ", ".join(
                    repr(f.value)
                    for f in self._committed.attrs(predicate)
                    if f.subject_id == obj.id
                )
            if not values:
                continue
            out.append(
                Violation(
                    check="retraction",
                    severity="reject",
                    message=(
                        f"{_safe(obj.id)} sets {field_name}=None, but {predicate} "
                        f"already holds {values} — facts are append-only, so a "
                        "committed value cannot be cleared. Model the change as a "
                        "new fact (e.g. a status value or a superseding entity) "
                        "instead."
                    ),
                    subjects=(obj.id,),
                )
            )
        return out

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
