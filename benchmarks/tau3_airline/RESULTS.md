# Airline benchmark results

In this run, **Sonnet 4.6 with lore scored 89.0% pass¹**, compared with **82.5%**
for the published stock baseline. lore blocked six policy violations, and the
agent recovered and passed in all six cases. The full score difference cannot
be attributed to lore: these are single runs, and agent behavior varies.

## Scores

| Setup | pass¹ | pass⁴ | Cost per conversation |
|---|---:|---:|---:|
| Stock Sonnet 4.6 (published baseline) | 82.5% | 76.0% | $0.253 |
| Sonnet 4.6 + lore | 89.0% | 82.0% | ~$0.27 |
| Difference | +6.5 percentage points | +6.0 percentage points | ~$0.02 |

pass¹ measures success on a single trial, averaged across tasks and trials.
pass⁴ measures the share of tasks passed in all four trials.

## Setup

- **Benchmark:** τ³-bench (tau2-bench v1.0.1, commit `a2c0247`), airline,
  text mode; all 50 tasks, four trials each (200 conversations).
- **Models:** `claude-sonnet-4-6` for both the agent and user simulator.
- **Settings:** temperature 0.0, seed 300, maximum 200 steps.
- **Baseline:** the published stock run from tau2 PR #489, with scores
  verified from its raw trajectories.
- **With lore:** the same settings, using the `airline_lore` domain. lore
  checks six write tools before they change the database. Rejected calls
  return a repair prompt as a tool error.

See the [benchmark README](README.md) for the rules and harness.

## Where the guard helped

The recorded run contains six cases where a rejection led to a repair and a
passing trial:

| Task | Observed behavior |
|---|---|
| 20 | Blocked a booking that used two travel certificates in all four trials. The agent recovered each time. This task went from 0/4 baseline passes to 4/4 with lore. |
| 14 | The only passing trial was the one where the guard rejected an action. |
| 5 | Blocked ineligible delayed-flight compensation. The agent recovered and passed. |

No false positives were observed: all recorded rejections were policy
violations, and no control task degraded with guard involvement. This is an
observation about this run, not a guarantee for other tasks or runs.

The clearest evidence of benefit comes from these six repairs. Improvements
on tasks without guard intervention may reflect run-to-run variation.

## Limitations

- **One run per setup, no error bars.** The baseline is the submitters'
  published run, not a new paired run.
- **Some itinerary changes are not blocked.** The itinerary rule compares only
  unambiguous invariants and can miss round-trip turnaround changes.
- **Dialogue requirements are not checked.** The guard does not verify the
  reason given for an insurance cancellation, confirmation before a write,
  or whether the user requested extra bags.
- **Intent errors remain.** Adding unrequested free checked bags was the
  model's main remaining failure mode. State checks alone cannot tell whether
  the user wanted the action.

The checks are defined in [world.py](world.py), with 30 world tests and nine
harness tests. These regression tests cover guard behavior; they do not remove
the statistical limitations of the benchmark comparison.
