# lore

**Check agent actions against rules that span objects and turns.**

Pydantic checks individual objects. lore adds checks against the state of an
agent's run: whether an order was already refunded, a payment recipient exists,
or an approver is in the employee's management chain.

Define entities and rules in Python. lore checks each proposal before you apply
it and returns a plain-English explanation when it fails. Your agent can use
that explanation to retry or explain why the request cannot be completed.

The core requires Python 3.11+ and Pydantic. It does not need an LLM, graph
database, or separate reasoning service.

## Install

From a local checkout:

```bash
pip install -e .
```

Optional integrations:

```bash
pip install -e ".[pydantic-ai]"
pip install -e ".[anthropic]"
```

The package name is `agent-lore`; the Python import is `lore`.

## Example: prevent a second refund

Relations refer to entities by ID. Here, `max_per_target=1` means each order can
have at most one refund, even when the refunds are proposed on different turns.

```python
from lore import Entity, Lore, Relation, relation

lore = Lore("commerce")


@lore.entity
class Order(Entity):
    total: float


@lore.entity
class Refund(Entity):
    amount: float
    refunds: Relation[Order] = relation(max_per_target=1)


guard = lore.compile()
session = guard.session(seed=[Order(id="order_1", total=100.0)])

first = session.try_commit(
    Refund(id="refund_1", amount=40.0, refunds="order_1")
)
assert first.ok

second = session.try_commit(
    Refund(id="refund_2", amount=40.0, refunds="order_1")
)
assert not second.ok
print(second.repair_prompt())
```

The second refund is a valid Pydantic object, but it breaks the rule for this
order. The repair prompt identifies the order and both refunds. The rejected
refund is not added to the session.

The [commerce example](examples/commerce/world.py) also checks refund amounts,
recipient types, and missing orders. Run it without an API key:

```bash
python examples/commerce/demo.py
```

## Write rules in Python

Use `@lore.rule` for checks that need to read other entities. Add this rule
before calling `lore.compile()` in the example above:

```python
from lore import Graph


@lore.rule(message="Refund {obj.id} exceeds the total of order {obj.refunds}.")
def refund_within_order_total(refund: Refund, graph: Graph) -> bool:
    order = graph.get(refund.refunds)
    return order is None or refund.amount <= order.total
```

Missing relation targets are checked separately. Rules can read entities with
`graph.get()`, find incoming relations with `graph.incoming()`, and follow
relation chains with `graph.reachable()`.

Some requirements apply only when the agent finishes. Declare those with
`@lore.goal` and call `session.check_goals()` at the output boundary. For
example, a completed contract must have a governing-law clause, but a draft
can be incomplete. See the [legal example](examples/legal/world.py).

## Guard tool calls and outputs

Use `session.guarded()` to validate an action before running its side effects:

```python
from lore import LoreViolation

refund = Refund(id="refund_3", amount=20.0, refunds="order_1")
ledger = []

try:
    with session.guarded(refund):
        ledger.append({"refund_id": refund.id, "amount": refund.amount})
except LoreViolation as exc:
    print(str(exc))  # Send this repair prompt back to the agent.
```

A rejection raises before the body runs. A successful body commits the
proposal. If the body raises, lore rolls back the proposal; it cannot undo
external side effects that already happened.

| Method | Use it to |
|---|---|
| `session.check(obj)` | Validate without changing session state |
| `session.try_commit(obj)` | Validate and commit if allowed; roll back otherwise |
| `session.guarded(obj)` | Validate, run side effects, then commit |
| `session.propose(obj)` | Stage a proposal for an explicit `commit()` or `rollback()` |
| `session.check_goals()` | Check completion requirements without changing state |

Give the agent the same rules in its prompt with `guard.to_context()`.

For Pydantic AI, connect the session to an output validator:

```python
from lore.adapters.pydantic_ai import validate_output


@agent.output_validator
def check_against_lore(output: Refund) -> Refund:
    return validate_output(session, output)
```

Valid outputs are committed. Rejections raise `ModelRetry` with the repair
prompt. The [offline agent demo](examples/commerce/agent_demo.py) shows the
full retry loop. The [Anthropic live example](examples/commerce/live_agent.py)
uses `guard_tool` to check tool calls and also validates the final summary.

## Other checks

| Requirement | Declaration |
|---|---|
| A relation target must exist and have the right type | `Relation[Customer]` |
| A field must use an allowed value | `one_of("paid", "shipped", "refunded")` |
| An ID cannot belong to two incompatible classes | `lore_disjoint_with = [Customer]` |
| A relation can contain multiple IDs | `Relation[list[Customer]]` |
| A reporting chain cannot contain cycles | `relation(transitive=True, irreflexive=True)` |
| A relation works in both directions | `relation(symmetric=True)` |
| Two fields represent opposite directions of one relation | `relation(inverse_of="field")` |
| A reverse edge is forbidden | `relation(asymmetric=True)` |

Inferred relations carry the facts that produced them, so a cycle rejection
can show the full chain. They are used for checks and `graph.reachable()`;
they are not committed or added to the entities returned by `graph.get()`.

Rules reject by default. Use `severity="flag"` to report a violation while
still allowing the proposal to commit.

## Save and inspect a session

Save committed state between turns and restore it on another worker:

```python
blob = session.snapshot()  # JSON string; store it with your run state.
session = guard.restore(blob)
```

Snapshots preserve facts, commit order, and seed markings. Restore rejects a
mismatched schema fingerprint. Commit or roll back any pending proposal before
snapshotting.

Use `session.dump()` for a text view or `session.graph` to inspect entities and
relations. To export an HTML viewer with declarations, transaction history,
and state playback, run:

```bash
python examples/commerce/demo.py --html commerce.html
```

You can also record your own run with `lore.viz.TraceRecorder` and export it
with `lore.viz.to_html`. The viewer is a self-contained file; no server is needed.

## Session behavior

- A session checks only its seed data and committed facts, plus the current
  proposal. Seed the records your rules need; missing records count as missing.
- Seeds are validated by default. Use `validate_seed=False` to opt out.
- Facts are append-only. Changing a committed scalar value is a violation.
  List relations can gain IDs but cannot remove them.
- Use one session per agent run. Sessions are single-threaded.

## Examples and results

| Example | What it covers |
|---|---|
| [Commerce](examples/commerce/demo.py) | Refunds, recipient types, and agent retries |
| [HR](examples/hr/demo.py) | Reporting cycles and approval chains |
| [Accounting](examples/accounting/demo.py) | Aggregate rules, flags, inverse relations, and persistence |
| [Legal](examples/legal/demo.py) | Contract rules and completion goals |
| [Airline benchmark](benchmarks/tau3_airline/README.md) | Policy checks around an agent's write tools |

Each example's `demo.py` runs without an API key. Live examples require
`ANTHROPIC_API_KEY`.

In the recorded airline benchmark run, Sonnet 4.6 with lore scored **89.0%
pass¹**, compared with **82.5%** for the published baseline. This is a single-run
comparison; the full difference cannot be attributed to the guard. See
[results and limitations](benchmarks/tau3_airline/RESULTS.md).

See [CONTRIBUTING.md](CONTRIBUTING.md) for adapter guidance and
[CONTEXT.md](CONTEXT.md) for design notes and research.
