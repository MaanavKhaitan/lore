"""Offline harness tests: guard behavior through the real tau2 environment,
gold-path bypass, and the strict-replay round trip the evaluator depends on.
No LLM, no API key.

Run: uv run --with-editable .context/tau2-bench pytest benchmarks/tau3_airline/harness
"""

import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tau2.data_model.message import AssistantMessage, ToolCall

from . import lore_env
from .lore_env import get_environment
from .shadow import shadow_one

# real db records (verified by data probe):
INELIGIBLE = ("VAAOXJ", "lei_rossi_3206")   # economy, no insurance, old, ok
BUSINESS = ("4WQ150", "chen_jackson_3290")  # business => cancellable
BASIC = ("Z30P1H", "mei_wilson_7043")       # basic economy


class Convo:
    """Drives an env exactly like the orchestrator: get_response per call,
    recording replayable (AssistantMessage, ToolMessage) history."""

    def __init__(self, env):
        self.env, self.messages, self._n = env, [], 0

    def call(self, name, **arguments):
        self._n += 1
        tc = ToolCall(id=f"tc_{self._n}", name=name, arguments=arguments,
                      requestor="assistant")
        self.messages.append(
            AssistantMessage(role="assistant", content=None, tool_calls=[tc]))
        tm = self.env.get_response(tc)
        self.messages.append(tm)
        return tm


def test_unauth_write_rejected_and_env_untouched():
    c = Convo(get_environment())
    rid, _ = BUSINESS
    tm = c.call("cancel_reservation", reservation_id=rid)
    assert tm.error and "authenticated" in tm.content
    assert c.env.tools.db.reservations[rid].status != "cancelled"


def test_ineligible_cancel_rejected_after_auth():
    c = Convo(get_environment())
    rid, uid = INELIGIBLE
    assert not c.call("get_user_details", user_id=uid).error
    tm = c.call("cancel_reservation", reservation_id=rid)
    assert tm.error and "not eligible" in tm.content
    assert c.env.tools.db.reservations[rid].status != "cancelled"


def test_eligible_cancel_commits():
    c = Convo(get_environment())
    rid, uid = BUSINESS
    assert not c.call("get_user_details", user_id=uid).error
    tm = c.call("cancel_reservation", reservation_id=rid)
    assert not tm.error, tm.content
    assert c.env.tools.db.reservations[rid].status == "cancelled"
    # cancel-once: a second cancel is rejected by the guard (axiom), the
    # env would happily... also refuse; guard message should win
    tm2 = c.call("cancel_reservation", reservation_id=rid)
    assert tm2.error


def test_cross_user_write_rejected():
    c = Convo(get_environment())
    _, other_uid = INELIGIBLE
    rid, _ = BUSINESS  # eligible reservation, but not the authed user's
    assert not c.call("get_user_details", user_id=other_uid).error
    tm = c.call("cancel_reservation", reservation_id=rid)
    assert tm.error and "authenticated" in tm.content


def test_basic_economy_flight_change_rejected():
    c = Convo(get_environment())
    rid, uid = BASIC
    assert not c.call("get_user_details", user_id=uid).error
    res = c.env.tools.db.reservations[rid]
    pm_id = next(iter(c.env.tools.db.users[uid].payment_methods))
    legs = [{"flight_number": f.flight_number, "date": f.date}
            for f in res.flights]
    legs[0] = {"flight_number": "HAT001", "date": legs[0]["date"]}  # change
    tm = c.call("update_reservation_flights", reservation_id=rid,
                cabin=res.cabin, flights=legs, payment_id=pm_id)
    assert tm.error and "ModifiableReservation" in tm.content


def test_gold_path_bypasses_guard():
    env = get_environment()
    rid, _ = INELIGIBLE  # guard would reject; gold path must not
    env.make_tool_call("cancel_reservation", requestor="assistant",
                       reservation_id=rid)
    assert env.tools.db.reservations[rid].status == "cancelled"


def test_strict_replay_roundtrip():
    """The evaluator replays trajectories with strict output comparison —
    a guarded run must reproduce byte-identically on a fresh env."""
    c = Convo(get_environment())
    rid_b, uid_b = BUSINESS
    rid_i, _ = INELIGIBLE
    c.call("get_user_details", user_id=uid_b)
    c.call("cancel_reservation", reservation_id=rid_i)   # rejected (x-user)
    c.call("cancel_reservation", reservation_id=rid_b)   # committed
    c.call("get_reservation_details", reservation_id=rid_b)  # read

    replay_env = get_environment()
    replay_env.set_state(None, None, c.messages, strict=True)  # must not raise
    assert (replay_env.get_db_hash() == c.env.get_db_hash()), "db divergence"


def test_shadow_replayer_counts_violations():
    # trajectory produced by an UNGUARDED env (baseline arm): the ineligible
    # cancel reaches the DB; shadow must count it with rule attribution
    from tau2.domains.airline.environment import get_environment as stock
    c = Convo(stock())
    rid, uid = INELIGIBLE
    c.call("get_user_details", user_id=uid)
    c.call("cancel_reservation", reservation_id=rid)  # stock env allows!
    assert c.env.tools.db.reservations[rid].status == "cancelled"

    raw = [m.model_dump() for m in c.messages]
    res = shadow_one(raw)
    bad = [e for e in res["events"] if not e.get("ok", True)]
    assert len(bad) == 1
    checks = {v["check"] for v in bad[0]["violations"]}
    assert any(ch.startswith("rule:cancel_eligibility") for ch in checks), checks


def test_registration():
    reg = lore_env.register()
    assert "airline_lore" in reg.get_info().domains
    tasks = reg.get_tasks_loader("airline_lore")()
    assert len(tasks) == 50


def test_mapping_failure_never_executes_write():
    c = Convo(get_environment())
    rid, uid = BUSINESS
    assert not c.call("get_user_details", user_id=uid).error
    before_db = c.env.get_db_hash()
    before_facts = c.env.lore_session.facts
    with patch.object(c.env.lore_mapper, "map_tool_call", side_effect=ValueError("bad mapping")):
        tm = c.call("cancel_reservation", reservation_id=rid)
    assert tm.error and "no action was taken" in tm.content
    assert c.env.get_db_hash() == before_db
    assert c.env.lore_session.facts == before_facts


def test_conflicting_authentication_returns_guard_rejection():
    c = Convo(get_environment())
    _, uid = BUSINESS
    _, other_uid = INELIGIBLE
    assert not c.call("get_user_details", user_id=uid).error
    before = c.env.lore_session.facts
    tm = c.call("get_user_details", user_id=other_uid)
    assert tm.error
    assert c.env.lore_session.facts == before


def test_commit_divergence_aborts_and_blocks_further_calls():
    c = Convo(get_environment())
    rid, uid = BUSINESS
    assert not c.call("get_user_details", user_id=uid).error
    rejected = SimpleNamespace(ok=False, repair_prompt=lambda: "forced divergence")
    with patch.object(c.env.lore_session, "try_commit", return_value=rejected):
        with pytest.raises(lore_env.LoreConsistencyError, match="forced divergence"):
            c.call("cancel_reservation", reservation_id=rid)
    # The external write cannot be undone, so the run must not continue.
    assert c.env.tools.db.reservations[rid].status == "cancelled"
    assert not c.env._guarding
    with pytest.raises(lore_env.LoreConsistencyError):
        c.call("get_reservation_details", reservation_id=rid)
    # A fresh task rebuilds the lore world and clears the fatal state.
    c.env.set_state(None, None, [])
    assert not c.call("get_reservation_details", reservation_id=rid).error


def test_post_effect_exception_aborts_run():
    c = Convo(get_environment())
    rid, uid = BUSINESS
    assert not c.call("get_user_details", user_id=uid).error
    with patch.object(c.env.lore_session, "try_commit", side_effect=ValueError("cannot commit")):
        with pytest.raises(lore_env.LoreConsistencyError, match="cannot commit"):
            c.call("cancel_reservation", reservation_id=rid)
    assert c.env.tools.db.reservations[rid].status == "cancelled"


def test_rejected_certificate_sync_is_not_silently_ignored():
    session = SimpleNamespace(
        graph=SimpleNamespace(get=lambda _: None),
        try_commit=lambda _: SimpleNamespace(ok=False, repair_prompt=lambda: "invalid certificate"),
    )
    db = {"users": {"user": {"payment_methods": {
        "cert": {"id": "cert", "source": "certificate", "amount": 100},
    }}}}
    with pytest.raises(RuntimeError, match="Cannot sync certificate cert"):
        lore_env.sync_new_certificates(session, db, "user")


if __name__ == "__main__":
    import traceback

    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception:
                failed += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    sys.exit(1 if failed else 0)
