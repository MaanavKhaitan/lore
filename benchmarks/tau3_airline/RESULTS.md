# Results: lore-guarded Sonnet 4.6 on τ³-bench airline

**Setup**: τ³-bench (tau2-bench v1.0.1 @ `a2c0247`), airline domain, text
mode, all 50 tasks. Agent and user simulator: `claude-sonnet-4-6`,
temperature 0.0, seed 300, 4 trials per task, max 200 steps — replicating
the recipe of the published stock baseline (tau2 PR #489).
Arm B: identical setup with lore
guarding the six write tools (`airline_lore` domain): violating tool
calls are rejected before the database changes and the repair prompt
returns as the tool error.

## Current results (full domain)

| Arm | pass^1 | pass^4 | $/conversation |
|---|---|---|---|
| Stock Sonnet 4.6 (published) | 82.5 | 76.0 | $0.253 |
| **Sonnet 4.6 + lore** | **89.0** | **82.0** | ~$0.27 |

## What the guard demonstrably did

- **6 rejection→repair→pass loops, 6/6 successful**: task 20 (0/4 → 4/4;
  the double-certificate booking trap, caught by the `max_per_target=1`
  certificate axiom in all four trials), task 14 (its only passing trial
  is the one where the rejection fired), task 5 (the agent attempted
  the illegal delayed-flight compensation live, was rejected, recovered,
  passed).
- **Zero false positives across all conversations**: every rejection was
  a genuine policy violation; no legal action was ever blocked; no
  control task degraded with guard involvement.
- Guard-attributable improvement is concentrated where the baseline
  commits actual policy violations; improvements on other tasks are
  within run-to-run agent variance (Sonnet 4.6's dominant residual
  failure mode is intent-level: spontaneously adding unrequested free
  checked bags — outside state-based guarding by design).

## Known limitations

- Single-round runs (no error bars); baseline numbers are the
  submitters' published single run (score-verified from their raw
  trajectories).
- Documented under-blocks: round-trip turnaround changes (itinerary rule
  compares only unambiguous invariants), conversational insurance
  reasons, dialogue-act clauses (confirmation-before-write, "bags the
  user does not need").
- All rules are in `world.py`, regression-tested (30 world + 9 harness
  tests).
