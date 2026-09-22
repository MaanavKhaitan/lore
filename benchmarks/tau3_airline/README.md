# Airline benchmark

This benchmark tests whether lore helps an agent follow airline policy by
checking tool calls before they change the database. It uses **τ³-bench
(tau2-bench v1.0.1), airline, text mode**.

The airline API leaves many policy checks to the agent, including cancellation
eligibility. Of the 50 tasks, 24 require denying a request. This makes the
domain useful for testing whether a guard catches actions the API would allow.

See [RESULTS.md](RESULTS.md) for scores, observed repairs, and limitations.

## Setup and offline tests

Use Python 3.12. The benchmark requires a local tau2-bench v1.0.1 checkout at
`.context/tau2-bench`; tau2 requires Python below 3.14. Run these commands from
the repository root after placing the checkout there:

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e . -e .context/tau2-bench
.venv/bin/python benchmarks/tau3_airline/test_world.py
.venv/bin/python benchmarks/tau3_airline/harness/test_harness.py
```

The 30 world tests and nine harness tests run without an LLM or API key.
They cover policy violations, allowed actions, and replay behavior.

## How the harness works

The `airline_lore` domain wraps the stock airline environment:

1. Convert a write-tool call into one or more lore entities.
2. Check the proposal against the session.
3. If rejected, return a repair prompt as an `error=True` tool result.
4. If allowed, execute the tool and commit the proposal.

A successful `get_user_details` call records authentication. Each task's seed
includes the conversation, a fixed clock (`2024-05-15T15:00:00`), customer and
payment records, reservations, and flight segments with date-specific statuses.

| File | Purpose |
|---|---|
| [world.py](world.py) | Entities, constraints, and policy rules |
| [harness/lore_env.py](harness/lore_env.py) | Guarded environment and domain registration |
| [harness/actions.py](harness/actions.py) | Database-to-seed and tool-call-to-entity mapping |
| [harness/run_lore.py](harness/run_lore.py) | Register the domain and run the stock tau2 CLI |
| [harness/shadow.py](harness/shadow.py) | Replay baseline trajectories and count violations by check |

For live runs, use `harness/run_lore.py run --domain airline_lore` with the
stock tau2 flags. Use `--domain airline` with identical flags for the baseline.
The settings used for the recorded run are in [RESULTS.md](RESULTS.md).

## What lore checks

These constraints use declarations rather than custom rule functions:

| Policy | Declaration |
|---|---|
| At most one certificate, one credit card, and three gift cards per reservation | `max_per_target` on typed payment uses |
| At most five passengers per reservation | `max_per_target=5` |
| Cancel a reservation at most once | `max_per_target=1` |
| One authenticated customer per conversation | `max_per_target=1` |
| No flight changes for basic economy | `FlightUpdate` must target a `ModifiableReservation` |
| No certificate payments for updates | Payment must target a `NonCertificateMethod` |
| Payment methods must exist in the seeded user profiles | Relation existence checks |
| Cabin, trip type, membership, insurance, and status must use known values | `one_of` |
| Payment kinds cannot overlap | `lore_disjoint_with` |

Python rules check authentication, customer scope, cancellation eligibility,
already-flown segments, itinerary and passenger-count restrictions, baggage
allowances, payment ownership, actions on cancelled reservations, and
compensation eligibility. Compensation amounts use `severity="flag"`: a
mismatch is reported but does not block the action.

## Mapping and replay details

- `update_reservation_flights` becomes a `CabinUpdate` when the flight list is
  unchanged, including for basic economy. Otherwise it becomes a `FlightUpdate`,
  with derived itinerary fields for the immutability rule.
- `book_reservation` proposes reservation, passenger, and payment-use entities
  together. Booking also checks authentication.
- The guard runs on `get_response`, which covers live agent calls and trajectory
  replay. Initialization and the evaluator's reference actions use direct
  `make_tool_call` calls and bypass the guard.
- `get_user_details` is marked as mutating so strict replay rebuilds
  authentication state. `test_strict_replay_roundtrip` checks that replay
  reproduces the same guard decisions.

## Known gaps

The guard checks state, so it cannot verify conversational requirements such as
confirmation before an action or whether a cancellation reason qualifies for
insurance. The insurance path currently accepts `insurance == "yes"` without
checking the stated reason. The itinerary rule can also miss round-trip
turnaround changes. See the [results limitations](RESULTS.md#limitations).

## API lessons

This integration exposed a few recurring needs for future API work:

- A reusable constraint for actions that require a target status.
- Per-field immutability for entities with both mutable and immutable data.
- Allowing subclasses to change options on inherited relations.
- A `graph.instances(Class)` query; current rules use a conversation anchor
  and `incoming()` instead.
- A way for the harness to record dialogue facts, such as user confirmation.
