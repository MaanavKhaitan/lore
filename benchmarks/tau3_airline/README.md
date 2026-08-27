# τ³-bench airline: lore world

Primary eval domain. Report as "τ³-bench (tau2-bench v1.0.1), airline,
text mode".

`world.py` compiles against lore @ main; `test_world.py` (22 traps +
legal-action controls) and `harness/test_harness.py` (9 integration tests)
all pass offline:

```
.venv/bin/python benchmarks/tau3_airline/test_world.py
.venv/bin/python benchmarks/tau3_airline/harness/test_harness.py
```

## Harness (`harness/`)

- `lore_env.py` — `LoreAirlineEnv`, registered as domain **`airline_lore`**:
  wraps the stock env; write tools are checked against the lore session
  before executing (reject → repair prompt as an `error=True` tool result,
  env untouched; pass → execute, then commit). Auth is asserted on a
  successful `get_user_details`. **Guard scoping:** active only for calls
  through `get_response` (live orchestrator + trajectory replay), so the
  evaluator's gold-action path (direct `make_tool_call`) and init actions
  bypass it; `get_user_details` is reported as mutating so strict replay
  rebuilds auth state and reproduces guard decisions byte-identically
  (verified by `test_strict_replay_roundtrip`).
- `actions.py` — db→seed construction (~9k entities, ~2s/session) and
  write-tool-call→action-entity mapping (incl. the `FlightUpdate` vs
  `CabinUpdate` split and multi-entity booking proposals).
- `shadow.py` — replays saved baseline trajectories, counting violations
  that reached the env, with per-check attribution
  (`shadow.py results.json`).
- `run_lore.py` — registers the domain, then hands over to the stock tau2
  CLI. Arm B: `run_lore.py run --domain airline_lore ...`; Arm A uses
  `--domain airline` with identical flags.

Requires the Python 3.12 venv (`/opt/homebrew/bin/python3.12 -m venv .venv;
.venv/bin/pip install -e . -e .context/tau2-bench` — tau2 needs <3.14).

## Why airline

The airline env enforces almost nothing the policy requires —
`cancel_reservation` performs zero eligibility checks, and the policy
itself says *"The API does not check that cancellation rules are met, so
the agent must make sure the rules apply before calling the API!"*
Violations reach the DB and directly cost τ³ reward, making policy
enforcement visible in the benchmark score. 24 of 50 tasks are deny-tasks.

## Axiom vs rule split (per-check attribution baseline)

**Declarative axioms (checked with zero rule code):**

| Policy clause | Axiom |
|---|---|
| ≤1 travel certificate / ≤1 credit card / ≤3 gift cards per reservation | `max_per_target=1/1/3` on typed `PaymentUse` subclasses |
| ≤5 passengers per reservation | `max_per_target=5` |
| A reservation is cancelled at most once | `max_per_target=1` |
| One authenticated customer per conversation | `max_per_target=1` on the `Conversation` singleton |
| Basic economy flights cannot be modified | range: `FlightUpdate.updates: Relation[ModifiableReservation]`; `BasicEconomyReservation` is disjoint, not a subtype |
| Updates can never be paid by certificate | range: `payment: Relation[NonCertificateMethod]` |
| Payment methods must already be in the user profile | existence check on relation ids |
| Cabin / trip type / membership / insurance / statuses take known values | `one_of` |
| Payment kinds are mutually exclusive | `lore_disjoint_with` |

**Rules (the `@lore.rule` escape hatch — lore's `field_validator`):**
authentication + cross-user scope, cancellation eligibility (24h via seeded
`Clock` / business / insured / airline-cancelled segment), flown-segment
gates (cancel; cabin change), itinerary immutability (origin/destination/
trip type), baggage monotonicity + the membership×cabin allowance table,
passenger-count immutability, payment ownership, no-action-on-cancelled,
compensation eligibility (reject) and schedule amounts (`severity="flag"` —
commits but surfaces).

**Known under-blocks (documented, deliberate):** the insurance path of
cancellation eligibility passes whenever `insurance == "yes"` without
verifying the stated reason is covered (reason is conversational, not
state); confirmation-before-write and no-fabricated-info are dialogue/
semantic clauses out of scope for state validation.

## Harness mapping notes

- `update_reservation_flights` maps to `CabinUpdate` when the flight list
  is unchanged (legal for every cabin incl. basic economy), else to
  `FlightUpdate` (whose harness-computed `new_origin`/`new_destination`/
  `new_trip_type` feed the immutability rule).
- `book_reservation` proposes the new `Reservation` subclass +
  `PassengerRecord`s + `PaymentUse`s — booking constraints are entirely
  axioms on the proposed entities.
- Seed per task: `Conversation`, `Clock(now="2024-05-15T15:00:00")`, the
  task's customer, their payment methods, reservations (subclassed by
  cabin), and `Segment`s with date-specific statuses from `flights[*].dates`.
- `Authentication` is asserted by the harness when the agent verifies the
  user id.

## DSL friction log (input to the v2 axiom-promotion decision)

1. **Status gates recur across both domains** (~7 instances): "action valid
   only if target has attribute value X". Strongest promotion candidate:
   e.g. `relation(Order, requires_target={"status": "pending"})` — the OWL
   hasValue-restriction shape.
2. **Per-field immutability**: itinerary immutability wanted the
   `single_value` re-assertion pattern, but entities mixing mutable and
   immutable fields make full re-assertion unusable. Candidate:
   `field(frozen=True)` mirroring Pydantic.
3. **Subclasses cannot redeclare an inherited relation with new options**
   → forced fieldless abstract bases (`AgentAction`, `PaymentUse`) plus
   `getattr` helpers in shared rules. Livable; an ergonomic gap.
4. **No `graph.instances(Class)`** — rules cannot enumerate a class;
   worked around with the `Conversation` singleton anchor + `incoming()`.
5. **Dialogue-act facts** (cancellation reason, user confirmation) have no
   home: only the harness can assert them. Boundary of the approach; the
   under-blocks above follow from it.
