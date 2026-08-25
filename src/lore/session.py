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

import json
from contextlib import contextmanager
from dataclasses import replace
from typing import Any, Iterable, Iterator

from pydantic import TypeAdapter

from .compile import Guard
from .engine import Graph, _most_specific, ground, run_checks
from .infer import build_context, derive
from .schema import Entity, LoreError
from .store import AttrFact, EdgeFact, Fact, FactStore, InMemoryStore, LayeredView, TypeFact
from .verdict import LoreViolation, Verdict, Violation

__all__ = ["Session", "Verdict", "Violation"]


class Session:
    """One agent run's fact graph, validated proposal by proposal.

    Sessions are **single-threaded by design** — use one session per agent run
    and never share it across threads. The session is closed-world over its
    seed plus committed facts: what it doesn't contain doesn't exist.
    """

    def __init__(
        self, guard: Guard, seed: Iterable[Entity] = (), *, validate_seed: bool = True
    ) -> None:
        self._guard = guard
        self._committed = InMemoryStore()
        for obj in seed:
            self._committed.add(ground(guard, obj, "seed"))
        self._staged: list[Fact] | None = None
        self._staged_store = InMemoryStore()
        self._last_verdict: Verdict | None = None
        self._step = 0
        if validate_seed and self._committed.all_facts():
            self._validate_seed()

    def _validate_seed(self) -> None:
        """Run the full derive+check pass with every seed fact treated as
        staged; an inconsistent seed fails loudly instead of silently exempting
        itself from the lore forever."""
        seed_facts = list(self._committed.all_facts())
        verdict = Verdict(run_checks(build_context(self._guard, self._committed, seed_facts)))
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
        # Derivations are computed locally per proposal and passed through the
        # context — never stored on the session, so check()'s save/restore of
        # (_staged, _staged_store, _last_verdict) stays complete.
        verdict = Verdict(run_checks(build_context(self._guard, view, staged)))
        self._staged = staged
        self._staged_store = staged_store
        self._last_verdict = verdict
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
        if self._staged:
            assert all(f.source != "derived" for f in self._staged)
            self._step += 1
            self._committed.add(replace(f, step=self._step) for f in self._staged)
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
        under a lore that has since changed fails loudly.
        """
        if self._staged is not None:
            raise LoreError(
                "cannot snapshot with a pending proposal: commit() or rollback() first"
            )
        adapters: dict[str, TypeAdapter[Any]] = {}
        payload = {
            "format": _SNAPSHOT_FORMAT,
            "lore": self._guard.name,
            "fingerprint": self._guard.fingerprint,
            "facts": [
                _encode_fact(self._guard, fact, adapters)
                for fact in self._committed.all_facts()
            ],
        }
        return json.dumps(payload, separators=(",", ":"))

    @classmethod
    def restore(cls, guard: Guard, blob: str | bytes) -> "Session":
        """Rehydrate a session from a :meth:`snapshot` blob.

        The restored facts are trusted as-is (they passed checks when they
        were committed) — nothing is re-validated. Raises
        :class:`~lore.schema.LoreError` if the blob is not a snapshot, uses
        an unknown snapshot format, or was taken under a lore whose
        fingerprint differs from ``guard.fingerprint``.
        """
        try:
            data = json.loads(blob)
        except (ValueError, TypeError, UnicodeDecodeError) as exc:
            raise LoreError(f"not a lore session snapshot: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("facts"), list):
            raise LoreError("not a lore session snapshot (no fact list)")
        if data.get("format") != _SNAPSHOT_FORMAT:
            raise LoreError(
                f"unsupported snapshot format {data.get('format')!r} "
                f"(this lore version reads format {_SNAPSHOT_FORMAT})"
            )
        if data.get("fingerprint") != guard.fingerprint:
            raise LoreError(
                f"snapshot was taken under lore {data.get('lore')!r} with fingerprint "
                f"{data.get('fingerprint')!r}, but restoring under {guard.name!r} with "
                f"{guard.fingerprint!r} — the lore has changed since this snapshot was "
                "taken; restore with the original definition or start a fresh session"
            )
        session = cls(guard)
        adapters: dict[str, TypeAdapter[Any]] = {}
        facts = [_decode_fact(guard, raw, adapters) for raw in data["facts"]]
        session._committed.add(facts)
        # Resume the commit-step counter where the snapshot left off, so
        # post-restore commits keep numbering (and messages) correct.
        session._step = max((f.step for f in facts), default=0)
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


# --- snapshot codec -----------------------------------------------------------
# Attribute values round-trip through the owning field's Pydantic annotation
# (datetime, Decimal, enums, ... come back as ``==``-equal values of the types
# the rules saw before the snapshot, though pydantic may canonicalize e.g.
# tzinfo implementations); a value the annotation cannot serialize or
# re-validate raises instead of degrading silently. Ids/sources are plain str.

_SNAPSHOT_FORMAT = 1
_SOURCES = ("seed", "asserted")


def _attr_adapter(guard: Guard, attr: str, adapters: dict[str, TypeAdapter[Any]]) -> TypeAdapter[Any]:
    adapter = adapters.get(attr)
    if adapter is None:
        spec = guard.attributes.get(attr)
        if spec is None:
            raise LoreError(
                f"snapshot refers to attribute {attr!r}, which is not in lore {guard.name!r}"
            )
        annotation = guard.classes[spec.owner].cls.model_fields[spec.field].annotation
        adapter = adapters[attr] = TypeAdapter(annotation)
    return adapter


def _encode_fact(guard: Guard, fact: Fact, adapters: dict[str, TypeAdapter[Any]]) -> dict[str, Any]:
    if isinstance(fact, TypeFact):
        return {"kind": "type", "node": fact.node_id, "class": fact.type_name, "source": fact.source,
                "step": fact.step}
    if isinstance(fact, EdgeFact):
        return {
            "kind": "edge",
            "subject": fact.subject_id,
            "predicate": fact.predicate,
            "object": fact.object_id,
            "source": fact.source,
            "step": fact.step,
        }
    try:
        value = _attr_adapter(guard, fact.attr, adapters).dump_python(
            fact.value, mode="json", warnings="error"
        )
    except LoreError:
        raise
    except Exception as exc:
        raise LoreError(
            f"cannot snapshot {fact.attr}={fact.value!r}: the value does not serialize "
            f"through the field's annotation ({exc})"
        ) from exc
    return {"kind": "attr", "subject": fact.subject_id, "attr": fact.attr, "value": value,
            "source": fact.source, "step": fact.step}


def _decode_fact(guard: Guard, raw: Any, adapters: dict[str, TypeAdapter[Any]]) -> Fact:
    try:
        kind, source = raw["kind"], raw["source"]
        if source not in _SOURCES:
            raise LoreError(f"malformed snapshot fact (bad source): {raw!r}")
        step = raw.get("step", 0)
        if not isinstance(step, int) or isinstance(step, bool) or step < 0:
            raise LoreError(f"malformed snapshot fact (bad step): {raw!r}")
        if kind == "type":
            if raw["class"] not in guard.classes:
                raise LoreError(
                    f"snapshot types {raw['node']!r} as {raw['class']!r}, which is not "
                    f"a class in lore {guard.name!r}"
                )
            return TypeFact(raw["node"], raw["class"], source, step=step)
        if kind == "edge":
            if raw["predicate"] not in guard.relations:
                raise LoreError(
                    f"snapshot refers to relation {raw['predicate']!r}, which is not "
                    f"in lore {guard.name!r}"
                )
            return EdgeFact(raw["subject"], raw["predicate"], raw["object"], source, step=step)
        if kind == "attr":
            adapter = _attr_adapter(guard, raw["attr"], adapters)
            try:
                value = adapter.validate_python(raw["value"])
            except Exception as exc:
                raise LoreError(
                    f"cannot restore {raw['attr']}={raw['value']!r} from a snapshot: {exc}"
                ) from exc
            return AttrFact(raw["subject"], raw["attr"], value, source, step=step)
    except (KeyError, TypeError) as exc:
        raise LoreError(f"malformed snapshot fact: {raw!r}") from exc
    raise LoreError(f"malformed snapshot fact (unknown kind): {raw!r}")
