"""TraceRecorder: every session choke point emits the right event — and the
recorded trace replays to exactly the committed world (the contract the
viewer's replay engine implements)."""

import json
from datetime import datetime, timezone
from typing import Any

import pytest

from lore import Entity, Graph, Lore, LoreError, LoreViolation, Relation, relation
from lore.viz import TRACE_FORMAT, TraceRecorder

lore = Lore("viz-record-test")


@lore.entity
class Customer(Entity):
    name: str
    meta: Any = None


@lore.entity
class Order(Entity):
    total: float
    placed_at: datetime
    placed_by: Relation[Customer]


@lore.entity
class Refund(Entity):
    amount: float
    refunds: Relation[Order] = relation(max_per_target=1)


@lore.entity
class Employee(Entity):
    name: str
    reports_to: Relation["Employee"] | None = relation(
        transitive=True, irreflexive=True, default=None
    )


@lore.rule(message="Refund {obj.id} exceeds order {obj.refunds}'s total.")
def refund_within_total(refund: Refund, graph: Graph) -> bool:
    order = graph.get(refund.refunds)
    return order is None or refund.amount <= order.total


@lore.goal(message="Order {obj.id} has no refund.")
def order_refunded(order: Order, graph: Graph) -> bool:
    return len(graph.incoming(order.id, "Refund.refunds")) == 1


guard = lore.compile()

PLACED_AT = datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc)


def recorded_session(**seed_kwargs):
    recorder = TraceRecorder()
    session = guard.session(
        seed=[
            Customer(id="cust_1", name="Ada", **seed_kwargs),
            Order(id="ord_1", total=100.0, placed_at=PLACED_AT, placed_by="cust_1"),
        ],
        recorder=recorder,
    )
    return session, recorder


def kinds(recorder):
    return [e["type"] for e in recorder.events]


# --- event emission per choke point ---------------------------------------------


def test_session_start_carries_seed_facts():
    _, recorder = recorded_session()
    assert kinds(recorder) == ["session_start"]
    start = recorder.events[0]
    assert {"kind": "type", "node": "cust_1", "class": "Customer", "source": "seed", "step": 0} in start["facts"]
    assert recorder.lore == "viz-record-test"
    assert recorder.fingerprint == guard.fingerprint


def test_propose_then_commit_with_step():
    session, recorder = recorded_session()
    session.propose(Refund(id="ref_1", amount=40.0, refunds="ord_1"))
    session.commit()
    assert kinds(recorder) == ["session_start", "propose", "commit"]
    propose, commit = recorder.events[1], recorder.events[2]
    assert propose["verdict"]["ok"] is True
    assert propose["verdict"]["repairPrompt"] == ""
    assert any(f["kind"] == "edge" and f["predicate"] == "Refund.refunds" for f in propose["facts"])
    assert commit["step"] == 1


def test_propose_then_rollback():
    session, recorder = recorded_session()
    verdict = session.propose(Refund(id="ref_x", amount=400.0, refunds="ord_1"))
    assert not verdict.ok
    session.rollback()
    assert kinds(recorder) == ["session_start", "propose", "rollback"]
    violation = recorder.events[1]["verdict"]["violations"][0]
    assert violation["check"] == "rule:refund_within_total"
    assert violation["severity"] == "reject"
    assert "ref_x" in violation["message"]
    assert recorder.events[1]["verdict"]["repairPrompt"].startswith("Your output violates")


def test_implicit_rollback_between_bare_proposes_is_recorded():
    # propose → propose (no explicit rollback): the second propose discards
    # the first — the trace must show that, or rejected rows vanish from the
    # timeline (the commerce demo's exact shape).
    session, recorder = recorded_session()
    session.propose(Refund(id="ref_x", amount=400.0, refunds="ord_1"))  # rejected
    session.propose(Refund(id="ref_1", amount=40.0, refunds="ord_1"))  # ok
    session.commit()
    assert kinds(recorder) == ["session_start", "propose", "rollback", "propose", "commit"]


def test_failed_propose_emits_no_phantom_rollback():
    # A propose that raises during grounding (unregistered entity) must leave
    # the pending proposal AND the trace untouched: a rollback emitted before
    # grounding would record a discard that never happened, and the surviving
    # proposal's commit would then vanish from the replay.
    class Unregistered(Entity):
        pass

    session, recorder = recorded_session()
    session.propose(Refund(id="ref_1", amount=40.0, refunds="ord_1"))
    with pytest.raises(LoreError):
        session.propose(Unregistered(id="huh"))
    session.commit()  # the pending ref_1 proposal is still alive
    assert kinds(recorder) == ["session_start", "propose", "commit"]
    assert replay(recorder.events) == json.loads(session.snapshot())["facts"]


def test_rollback_without_a_proposal_is_silent():
    session, recorder = recorded_session()
    session.rollback()
    assert kinds(recorder) == ["session_start"]


def test_try_commit_both_branches():
    session, recorder = recorded_session()
    assert session.try_commit(Refund(id="ref_1", amount=40.0, refunds="ord_1")).ok
    assert not session.try_commit(Refund(id="ref_2", amount=40.0, refunds="ord_1")).ok
    assert kinds(recorder) == ["session_start", "propose", "commit", "propose", "rollback"]


def test_guarded_success_and_reject():
    session, recorder = recorded_session()
    with session.guarded(Refund(id="ref_1", amount=40.0, refunds="ord_1")):
        pass
    with pytest.raises(LoreViolation):
        with session.guarded(Refund(id="ref_2", amount=40.0, refunds="ord_1")):
            pytest.fail("body must not run for a rejected proposal")
    assert kinds(recorder) == ["session_start", "propose", "commit", "propose", "rollback"]


def test_check_emits_one_check_event_and_pending_proposal_survives():
    session, recorder = recorded_session()
    session.propose(Refund(id="ref_1", amount=40.0, refunds="ord_1"))
    # check() preflights against the committed world (staged ref_1 is not
    # part of it), so an over-total refund is the thing that rejects.
    verdict = session.check(Refund(id="ref_2", amount=400.0, refunds="ord_1"))
    assert not verdict.ok
    assert kinds(recorder) == ["session_start", "propose", "check"]
    check = recorder.events[2]
    assert any(f.get("subject") == "ref_2" for f in check["facts"])
    assert check["verdict"]["ok"] is False
    session.commit()  # the pending ref_1 proposal survived the check
    assert kinds(recorder) == ["session_start", "propose", "check", "commit"]
    assert recorder.events[3]["step"] == 1


def test_empty_commit_has_null_step():
    session, recorder = recorded_session()
    # Re-proposing the seed adds no facts: staged is empty, commit consumes no step.
    session.propose(Customer(id="cust_1", name="Ada"))
    session.commit()
    assert recorder.events[1]["facts"] == []
    assert recorder.events[2]["step"] is None


def test_check_goals_event():
    session, recorder = recorded_session()
    verdict = session.check_goals()
    assert not verdict.ok
    event = recorder.events[-1]
    assert event["type"] == "check_goals"
    assert event["verdict"]["violations"][0]["check"] == "goal:order_refunded"
    assert event["verdict"]["repairPrompt"].startswith("The work is not finished")


def test_snapshot_and_restore_events():
    session, recorder = recorded_session()
    session.try_commit(Refund(id="ref_1", amount=40.0, refunds="ord_1"))
    blob = session.snapshot()
    assert kinds(recorder)[-1] == "snapshot"
    restore_recorder = TraceRecorder()
    guard.restore(blob, recorder=restore_recorder)
    assert kinds(restore_recorder) == ["restore"]
    # Restored facts keep their original commit steps.
    steps = {f["step"] for f in restore_recorder.events[0]["facts"]}
    assert steps == {0, 1}


def test_cycle_violation_carries_ordered_subjects_and_provenance():
    recorder = TraceRecorder()
    session = guard.session(
        seed=[Employee(id="ceo", name="Grace")], recorder=recorder
    )
    session.try_commit(Employee(id="mgr", name="Sam", reports_to="ceo"))
    verdict = session.try_commit(Employee(id="ceo", name="Grace", reports_to="mgr"))
    assert not verdict.ok
    violation = next(
        v
        for e in recorder.events
        if e["type"] == "propose" and not e["verdict"]["ok"]
        for v in e["verdict"]["violations"]
        if v["check"] == "irreflexive"
    )
    # The ordered cycle node list — which end it starts from depends on which
    # self-edge the closure derived first.
    assert set(violation["subjects"]) == {"ceo", "mgr"}
    edges = [(f["subject"], f["object"]) for f in violation["provenance"]]
    assert ("mgr", "ceo") in edges and ("ceo", "mgr") in edges


def test_datetime_encodes_and_unserializable_value_degrades_to_repr():
    session, recorder = recorded_session(meta=object())  # Any-typed, unserializable
    start = recorder.events[0]
    placed = next(f for f in start["facts"] if f.get("attr") == "Order.placed_at")
    assert placed["value"] == "2026-08-25T09:00:00Z"
    meta = next(f for f in start["facts"] if f.get("attr") == "Customer.meta")
    assert meta["repr"] is True
    assert meta["value"].startswith("<object object")


# --- the replay invariant: trace events ⇒ exactly the snapshot ------------------


def _fact_key(fact):
    if fact["kind"] == "type":
        return ("type", fact["node"], fact["class"])
    if fact["kind"] == "edge":
        return ("edge", fact["subject"], fact["predicate"], fact["object"])
    return ("attr", fact["subject"], fact["attr"], json.dumps(fact["value"], sort_keys=True))


def replay(events):
    """Reference fold — the semantics ui's replay.ts implements."""
    committed, seen, pending = [], set(), None
    for event in events:
        if event["type"] in ("session_start", "restore"):
            new = event["facts"]
        elif event["type"] == "propose":
            pending = event["facts"]
            continue
        elif event["type"] == "commit":
            new = [] if event["step"] is None else [{**f, "step": event["step"]} for f in pending]
            pending = None
        elif event["type"] == "rollback":
            pending = None
            continue
        else:  # check / check_goals / snapshot: stateless
            continue
        for fact in new:
            key = _fact_key(fact)
            if key not in seen:
                seen.add(key)
                committed.append(fact)
    return committed


def test_replay_reproduces_the_snapshot_exactly():
    session, recorder = recorded_session()
    session.try_commit(Refund(id="ref_1", amount=40.0, refunds="ord_1"))
    session.try_commit(Refund(id="ref_2", amount=40.0, refunds="ord_1"))  # rejected
    session.check(Refund(id="ref_3", amount=1.0, refunds="ord_1"))  # stateless
    session.propose(Customer(id="cust_2", name="Sam"))
    session.commit()
    session.check_goals()
    assert replay(recorder.events) == json.loads(session.snapshot())["facts"]


def test_payload_envelope_and_json_round_trip():
    session, recorder = recorded_session()
    session.try_commit(Refund(id="ref_1", amount=40.0, refunds="ord_1"))
    payload = recorder.to_payload()
    assert payload["format"] == TRACE_FORMAT
    assert payload["kind"] == "lore-trace"
    assert payload["fingerprint"] == guard.fingerprint
    assert json.loads(recorder.dumps()) == payload
