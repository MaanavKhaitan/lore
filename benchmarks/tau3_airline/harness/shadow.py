"""Shadow-mode replayer: count lore violations in saved tau2 trajectories.

Replays each simulation's tool calls against a stock airline env (to keep DB
context current) while checking every *successful* write through a lore
session in observe-only mode. Violations recorded here are policy violations
that reached the environment — the baseline arm's headline number, with
per-check attribution (axiom vs rule).

Usage:
  uv run --with-editable .context/tau2-bench python -m benchmarks.tau3_airline.harness.shadow <results.json> \\
      [-o violations.json]

Rejected-then-not-committed actions are rolled back and replay continues;
downstream duplicates of an uncommitted violation may be undercounted (a
conservative bias, noted in the report).
"""

import argparse
import json
from collections import Counter
from pathlib import Path

from tau2.domains.airline.environment import (
    get_environment as get_stock_environment,
)

from .actions import (
    ActionMapper, WRITE_TOOLS, seed_from_db, sync_new_certificates,
)
from ..world import guard as lore_guard


def iter_tool_events(messages: list) -> "list[tuple[dict, dict]]":
    """(tool_call, tool_message) pairs from a raw message list, in order."""
    by_id = {}
    order = []
    for m in messages:
        if m.get("role") in ("assistant", "user") and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                by_id[tc["id"]] = [tc, None]
                order.append(tc["id"])
        elif m.get("role") == "tool":
            if m.get("id") in by_id:
                by_id[m["id"]][1] = m
    return [tuple(by_id[i]) for i in order if by_id[i][1] is not None]


def shadow_one(messages: list) -> dict:
    env = get_stock_environment()
    session = lore_guard.session(seed=seed_from_db(env.tools.db.model_dump()),
                                 validate_seed=False)
    mapper = ActionMapper(lambda: env.tools.db.model_dump())
    events = []
    for tc, tm in iter_tool_events(messages):
        name, args, failed = tc["name"], tc["arguments"] or {}, tm.get("error")
        if isinstance(args, str):
            args = json.loads(args)
        if failed:
            continue  # never reached the env live
        # Shadow auth convention (more generous than the live guard's): the
        # airline policy requires the user to *provide* their id, not that
        # the agent call a lookup tool — so grant auth on the first user_id
        # seen in any successful call. Biases toward undercounting auth
        # violations, which is the defensible direction for third-party
        # trajectories.
        if "user_id" in args:
            session.try_commit(mapper.auth_entity(args["user_id"]))
        if name not in WRITE_TOOLS:
            continue
        try:
            entities = mapper.map_tool_call(name, args)
        except Exception as e:
            events.append({"tool": name, "mapping_error": str(e)})
            continue
        verdict = session.check(*entities)
        events.append({
            "tool": name,
            "ok": verdict.ok,
            "violations": [
                {"check": v.check, "severity": v.severity, "message": v.message}
                for v in verdict.violations
            ],
        })
        if not verdict.ok:
            session.rollback()
        # advance the stock DB so later mappings see current state
        result = None
        try:
            result = env.make_tool_call(
                name, requestor=tc.get("requestor", "assistant"), **args)
        except Exception:
            pass
        if verdict.ok:
            if name == "send_certificate":
                sync_new_certificates(session, env.tools.db.model_dump(),
                                      args["user_id"])
            # bookings must be committed under the env-assigned id, exactly
            # as the live guard does — committing the pending placeholder
            # makes later bookings/updates collide (false single_value).
            if name == "book_reservation":
                rid = getattr(result, "reservation_id", None)
                if rid is None:
                    continue  # env rejected the booking: nothing to commit
                entities = mapper.map_tool_call(
                    name, args, new_reservation_id=rid)
            session.try_commit(*entities)
    return {"events": events}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results", help="tau2 results json (Results or a single "
                                    "SimulationRun)")
    ap.add_argument("-o", "--out", default=None)
    args = ap.parse_args()

    data = json.load(open(args.results))
    sims = data.get("simulations", [data])
    report, check_counter = [], Counter()
    for sim in sims:
        res = shadow_one(sim.get("messages") or [])
        n_viol = sum(len(e.get("violations", [])) for e in res["events"]
                     if not e.get("ok", True))
        for e in res["events"]:
            if not e.get("ok", True):
                for v in e.get("violations", []):
                    check_counter[v["check"]] += 1
        report.append({
            "task_id": sim.get("task_id"),
            "trial": sim.get("trial"),
            "violations_reaching_env": n_viol,
            "events": res["events"],
        })

    summary = {
        "simulations": len(report),
        "sims_with_violations": sum(1 for r in report
                                    if r["violations_reaching_env"]),
        "total_violations": sum(r["violations_reaching_env"] for r in report),
        "by_check": dict(check_counter.most_common()),
        "runs": report,
    }
    out = args.out or (Path(args.results).stem + "_shadow.json")
    Path(out).write_text(json.dumps(summary, indent=1))
    print(json.dumps({k: v for k, v in summary.items() if k != "runs"},
                     indent=1))
    print(f"written: {out}")


if __name__ == "__main__":
    main()
