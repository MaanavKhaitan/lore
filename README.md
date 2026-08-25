# lore

**Pydantic validates the shape of one object. lore validates whether your
agent's outputs make sense in your world.**

Declare what's true in your world — entity classes, relations, and
axioms — on Pydantic models you already have. Agent outputs and tool calls are
**deterministically** checked against it: cross-object, cross-turn, stateful
constraints that per-object schema validation structurally cannot express.
Violations render as natural-language repair prompts fed back to the agent.

```
"an order can be refunded at most once"
"a payout recipient can never be a support-rep account"
"a refund cannot exceed the order's total"
```

No RDF, no reasoner JVM, no graph database. Runtime dependency: pydantic.
*(Not on PyPI yet — will ship as `agent-lore`, imported as `lore`.)*

## Install

```bash
pip install -e .                  # core
pip install -e ".[pydantic-ai]"   # + the Pydantic AI adapter
pip install -e ".[anthropic]"     # + the live-agent example (Anthropic SDK)
```

## Declare your world

```python
from lore import Entity, Graph, Lore, Relation, one_of, relation

lore = Lore("commerce")

@lore.entity
class Customer(Entity):
    name: str

@lore.entity
class SupportRep(Entity):
    lore_disjoint_with = [Customer]   # never both, same id
    name: str

@lore.entity
class Order(Entity):
    status: str = one_of("paid", "shipped", "refunded")
    total: float
    placed_by: Relation[Customer]      # relations reference targets by id

@lore.entity
class Refund(Entity):
    amount: float
    refunds: Relation[Order] = relation(max_per_target=1)  # refund-once
    paid_to: Relation[Customer]

@lore.rule(message="Refund {obj.id} of ${obj.amount} exceeds the total of order {obj.refunds}.")
def refund_within_order_total(refund: Refund, graph: Graph) -> bool:
    order = graph.get(refund.refunds)          # arbitrary-Python escape hatch
    return order is None or refund.amount <= order.total

guard = lore.compile()   # broken lore fails loudly here, Pydantic-style
```

## Catch the double refund

```python
session = guard.session(seed=[ada, sam, ord_1, ord_2, prior_refund])

session.propose(Refund(id="ref_1", amount=40.0, refunds="ord_2", paid_to="cust_1"))
session.commit()   # ok — committed

verdict = session.propose(Refund(id="ref_2", amount=40.0, refunds="ord_2", paid_to="cust_1"))
verdict.ok         # False
print(verdict.repair_prompt())
```

```
Your output violates 1 rule(s) of this domain:
  1. An Order can have at most 1 Refund pointing at it via 'refunds', but ord_2
     has 2: ref_1 (already committed), ref_2 (proposed).
Produce a corrected output that satisfies every rule. If the requested action
is impossible under these rules, say so instead of retrying it.
```

Note what happened: the second refund was a perfectly valid `Refund` object.
Pydantic has nothing to complain about — the violation only exists across
objects and across turns. A refund paid to the support rep (`range`), a refund
of a nonexistent order (`existence`), one id typed as both customer and rep
(`disjoint`), a bad status (`one_of`) are all caught the same way. Run
`python examples/commerce/demo.py` to see each verdict — no API key needed.

## Relation characteristics — inference with receipts

Declare *how a relation behaves* and violations are caught across chains
nobody enumerated:

```python
lore = Lore("hr")

@lore.entity
class Employee(Entity):
    name: str
    reports_to: Relation["Employee"] | None = relation(
        transitive=True,     # A→B and B→C imply A→C
        irreflexive=True,    # nobody is in their own chain
        default=None,
    )
```

That one declaration is cycle detection: a proposed edge that closes a
reporting loop derives a self-edge, the irreflexive check catches it, and
the repair prompt renders the exact chain — every derived fact carries
provenance, the base facts that produced it:

```
Your output violates 1 rule(s) of this domain:
  1. emp_9 cannot reach itself via 'reports_to', but this proposal creates a
     cycle: emp_9 → mgr_2 (already committed at step 2) → ceo (already
     committed at step 1) → emp_9 (proposed).
```

The other characteristics: `symmetric=True` mirrors each edge onto its own
predicate, so "B married to both A and C" is caught even when every fact was
asserted from the other side; `inverse_of="field"` pairs two fields as two
directions of one fact, so cardinality holds whichever direction the agent
asserts (a one-to-one pair wants `max_per_target=1` on one side);
`asymmetric=True` rejects B→A once A→B holds, and implies `irreflexive`.

Derived edges are checked, never committed, and never leak into entity
rehydration — rules traverse them explicitly with `graph.reachable()`:

```python
@lore.rule(message="Expense {obj.id} was approved by {obj.approved_by}, "
                   "who is not in {obj.filed_by}'s management chain.")
def approver_in_chain(report: ExpenseReport, graph: Graph) -> bool:
    return report.approved_by in graph.reachable(report.filed_by, "Employee.reports_to")
```

The CEO approving a deep report passes without anyone enumerating chains, and
self-approval fails naturally — irreflexivity keeps you out of your own
chain. Run `python examples/hr/demo.py` to see all four verdicts:
self-manage, cycle, and out-of-chain approvals rejected; a transitively-valid
CEO approval committed.

## Guard a tool call

`propose`/`commit`/`rollback` is the transactional core; three wrappers cover
the everyday shapes:

```python
verdict = session.check(refund)        # preflight "can I?" — zero state change
verdict = session.try_commit(refund)   # commit if ok, roll back otherwise

with session.guarded(refund):          # raises LoreViolation on rejects
    ledger.append(entry)               # side effects run only if valid;
                                       # commit happens after they succeed
```

`guarded()` gets the ordering right by construction: an invalid proposal
raises *before* the body runs (no side effects), an exception in the body
rolls the proposal back (no commit for failed effects), and a clean exit
commits. `LoreViolation` carries the verdict, and `str(exc)` is the
repair prompt — catch it and feed it back to the model.

To see what the session believes while debugging: `print(session.dump())`
renders the world grouped by entity (committed vs staged, seeds marked), and
`session.graph` exposes the same read API rules receive
(`graph.get(id)`, `graph.incoming(id, "Refund.refunds")`).

## Persist the session between turns

Real deployments are stateless: each agent turn lands on whichever worker
picks it up, and an in-process session dies with the process. `snapshot()`
serializes the committed graph to a compact JSON blob — store it wherever you
already keep state and rehydrate next turn:

```python
redis.set(f"lore:{run_id}", session.snapshot())       # end of turn N
...
session = guard.restore(redis.get(f"lore:{run_id}"))  # start of turn N+1
```

Restore reproduces the committed graph exactly — facts, order, seed
markings — so cross-turn rules like refund-once keep firing across processes.
Attribute values round-trip through their field annotations (a `datetime`
comes back a `datetime`). The blob is stamped with `guard.fingerprint`, a
content-hash of the lore's validation semantics: restoring under a lore that
has since changed raises instead of silently validating old facts against new
rules. Snapshots capture turn boundaries — snapshotting with an uncommitted
proposal raises too.

## Put the rules in the prompt too

```python
system_prompt = guard.to_context() + "\n\n" + YOUR_INSTRUCTIONS
```

`to_context()` renders the lore as deterministic English — entity shapes,
disjointness, cardinality, and rules — so the same declaration serves
prevention (the model knows the rules) and detection (violations are caught
anyway when it ignores them).

## Close the loop with an agent

The repair prompt plugs straight into retry sockets that already exist.
With Pydantic AI it's three lines:

```python
from lore.adapters.pydantic_ai import validate_output

@agent.output_validator
def check_against_lore(output: Refund) -> Refund:
    return validate_output(session, output)   # ok → commit; bad → ModelRetry(repair_prompt)
```

The agent emits the double refund, receives the message above, and corrects
itself — `python examples/commerce/agent_demo.py` runs the whole
validate → explain → retry → pass loop offline with a scripted model.

### Anthropic tool-use loops

For hand-rolled Messages-API loops, `guard_tool` wraps a tool function so its
returned entity is proposed before anything takes effect — a rejection becomes
the `(repair_prompt, is_error=True)` tool result the loop feeds back:

```python
from lore.adapters.anthropic import guard_tool

@guard_tool(session)
def issue_refund(order_id: str, amount: float, payout_account_id: str):
    refund = Refund(id=next_id(), amount=amount, refunds=order_id,
                    paid_to=payout_account_id)
    entry = {"refund_id": refund.id, "order_id": order_id, "amount": amount}

    def issued():                        # runs only if the guard passes
        LEDGER.append(entry)
        return {"status": "issued", **entry}

    return refund, issued

content, is_error = issue_refund(**tool_use.input)   # → tool_result block
```

(The adapter never imports the anthropic SDK — it only produces the result
shapes the loop needs, so it works with any hand-rolled loop.)

`examples/commerce/live_agent.py` runs this against a live Anthropic agent,
with the guard at both check points of a tool-use loop:

- **before a tool call executes** — each `issue_refund` call is proposed
  against the session first; a rejection returns the repair prompt as an
  `is_error` tool result and writes nothing;
- **on the final output** — the agent's structured summary is re-proposed
  against the committed ledger, so a summary that violates the lore or
  claims a refund that was never issued bounces back too.

```bash
pip install -e ".[anthropic]"
ANTHROPIC_API_KEY=... python examples/commerce/live_agent.py   # or put the key in a repo-root .env
```

The lookup tool plays a deliberately stale orders DB — no refund history, no
account types — while the session knows both. The model walks into a
cross-turn double refund and a payout redirected to a support-rep account,
gets the two repair prompts above, declines the impossible request, and
self-corrects the other. Costs a few cents per run.

## How it works

1. **Ground** — a proposed entity is mechanically decomposed into facts:
   type facts (one per class in the MRO, so subclasses satisfy supertype
   constraints), edge facts for relations, attribute facts for scalars.
   Deterministic; no LLM anywhere.
2. **Infer** — mirrors (symmetric and inverse pairs) and transitive closure
   are derived over committed ∪ staged, each derived edge recording the base
   facts that produced it. Derived edges are checked, never committed, and
   recomputed per proposal — a rejected proposal's derivations vanish with it.
3. **Check** — facts are staged in an overlay, never written directly. All
   checks (existence, domain/range, max_per_target, single_value, one_of,
   disjoint, irreflexive, asymmetric, rules) scan the composed views; only
   violations involving staged facts (directly or through a derived edge)
   are reported. Rules re-run on staged subjects *and* the direct targets of
   staged edges, so non-monotone aggregate rules (a sum over
   `graph.incoming`) stay safe against later stray edges.
4. **Verdict** — violations carry severity (`reject` blocks commit, `flag`
   commits but is surfaced) and render as concrete, id-naming English, with
   step numbers ("already committed at step 2") and derivation chains.
5. **Repair** — `verdict.ok` gates `session.commit()`; otherwise
   `repair_prompt()` goes back to the agent and the rejected facts vanish —
   a failed attempt leaves **zero trace**, so retries never fire against the
   agent's own earlier mistakes.

Sessions are transactional (propose → check → commit), closed-world over
their seed plus committed facts, and single-threaded by design: one session
per agent run. Seeds are validated against the lore at session creation
(`validate_seed=False` to opt out). Committed facts are immutable:
re-asserting an entity id with changed values is itself a violation
(`single_value`), never a silent update — so an agent can't dodge "refund at
most once" by reusing an old refund's id.

## Status & roadmap

Milestone 1: schema DSL, grounding, in-memory store, 6 axiom checks + rule
escape hatch, verdicts/repair prompts, Pydantic AI + Anthropic adapters,
`check`/`try_commit`/`guarded()` session API, `to_context()` prompt rendering,
durable sessions (`snapshot()`/`restore()` + lore fingerprint), session
inspection (`dump()`, `session.graph`), commerce example.

Milestone 2 (this): the inference layer — relation characteristics
(`transitive`, `symmetric`, `asymmetric`, `irreflexive`, `inverse_of`) with
compile-time contradiction checks, provenance-carrying derived facts,
irreflexive/asymmetric checks (cycle detection with rendered chains), commit
step numbers in messages, seed validation, `graph.reachable()`, HR
approval-chain example. Tested table-driven per axiom + Hypothesis properties.

Next: severity/shadow-mode polish; SHACL export as a differential-testing
oracle; an MCP tool-call proxy; a benchmark for axiom-violation feedback vs
generic retry. See `CONTEXT.md` for the full design rationale and research.

Want lore in a framework we don't cover? Adapters are ~30 lines —
[CONTRIBUTING.md](CONTRIBUTING.md) has the contract and a template.
