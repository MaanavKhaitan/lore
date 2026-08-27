"""Offline harness tests: guard behavior through the real tau2 environment,
gold-path bypass, and the strict-replay round trip the evaluator depends on.
No LLM, no API key.

Run: .venv/bin/python benchmarks/tau3_airline/harness/test_harness.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from tau2.data_model.message import AssistantMessage, ToolCall  # noqa: E402

import lore_env  # noqa: E402
from lore_env import get_environment  # noqa: E402
from shadow import shadow_one  # noqa: E402

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
