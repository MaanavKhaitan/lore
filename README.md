# ontic

**Pydantic validates the shape of one object. ontic validates whether your
agent's outputs make sense in your world.**

Declare your business domain as an ontology — entity classes, relations, and
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
*(Working name — not on PyPI yet.)*

## Install

```bash
pip install -e .                  # core
pip install -e ".[pydantic-ai]"   # + the Pydantic AI adapter
pip install -e ".[anthropic]"     # + the live-agent example (Anthropic SDK)
```

## Declare your world

```python
from ontic import Entity, Graph, Ontology, Relation, one_of, relation

ont = Ontology("commerce")

@ont.entity
class Customer(Entity):
    name: str

@ont.entity
class SupportRep(Entity):
    ontic_disjoint_with = [Customer]   # never both, same id
    name: str

@ont.entity
class Order(Entity):
    status: str = one_of("paid", "shipped", "refunded")
    total: float
    placed_by: Relation[Customer]      # relations reference targets by id

@ont.entity
class Refund(Entity):
    amount: float
    refunds: Relation[Order] = relation(max_per_target=1)  # refund-once
    paid_to: Relation[Customer]

@ont.rule(message="Refund {obj.id} of ${obj.amount} exceeds the total of order {obj.refunds}.")
def refund_within_order_total(refund: Refund, graph: Graph) -> bool:
    order = graph.get(refund.refunds)          # arbitrary-Python escape hatch
    return order is None or refund.amount <= order.total

guard = ont.compile()   # broken ontologies fail loudly here, Pydantic-style
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

## Guard a tool call

`propose`/`commit`/`rollback` is the transactional core; three wrappers cover
the everyday shapes:

```python
verdict = session.check(refund)        # preflight "can I?" — zero state change
verdict = session.try_commit(refund)   # commit if ok, roll back otherwise

with session.guarded(refund):          # raises OntologyViolation on rejects
    ledger.append(entry)               # side effects run only if valid;
                                       # commit happens after they succeed
```

`guarded()` gets the ordering right by construction: an invalid proposal
raises *before* the body runs (no side effects), an exception in the body
rolls the proposal back (no commit for failed effects), and a clean exit
commits. `OntologyViolation` carries the verdict, and `str(exc)` is the
repair prompt — catch it and feed it back to the model.

To see what the session believes while debugging: `print(session.dump())`
renders the world grouped by entity (committed vs staged, seeds marked), and
`session.graph` exposes the same read API rules receive
(`graph.get(id)`, `graph.incoming(id, "Refund.refunds")`).

## Put the rules in the prompt too

```python
system_prompt = guard.to_context() + "\n\n" + YOUR_INSTRUCTIONS
```

`to_context()` renders the ontology as deterministic English — entity shapes,
disjointness, cardinality, and rules — so the same declaration serves
prevention (the model knows the rules) and detection (violations are caught
anyway when it ignores them).

## Close the loop with an agent

The repair prompt plugs straight into retry sockets that already exist.
With Pydantic AI it's three lines:

```python
from ontic.adapters.pydantic_ai import validate_output

@agent.output_validator
def check_against_ontology(output: Refund) -> Refund:
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
from ontic.adapters.anthropic import guard_tool

@guard_tool(session)
def issue_refund(order_id: str, amount: float, payout_account_id: str):
    refund = Refund(id=next_id(), amount=amount, refunds=order_id,
                    paid_to=payout_account_id)

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
  against the committed ledger, so a summary that violates the ontology or
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
2. **Check** — facts are staged in an overlay, never written directly. All
   checks (existence, domain/range, max_per_target, single_value, one_of,
   disjoint, rules) scan committed ∪ staged; only violations involving staged
   facts are reported.
3. **Verdict** — violations carry severity (`reject` blocks commit, `flag`
   commits but is surfaced) and render as concrete, id-naming English.
4. **Repair** — `verdict.ok` gates `session.commit()`; otherwise
   `repair_prompt()` goes back to the agent and the rejected facts vanish —
   a failed attempt leaves **zero trace**, so retries never fire against the
   agent's own earlier mistakes.

Sessions are transactional (propose → check → commit), closed-world over
their seed plus committed facts, and single-threaded by design: one session
per agent run. Committed facts are immutable: re-asserting an entity id with
changed values is itself a violation (`single_value`), never a silent update —
so an agent can't dodge "refund at most once" by reusing an old refund's id.

## Status & roadmap

Milestone 1 (this): schema DSL, grounding, in-memory store, 6 axiom checks +
rule escape hatch, verdicts/repair prompts, Pydantic AI + Anthropic adapters,
`check`/`try_commit`/`guarded()` session API, `to_context()` prompt rendering,
session inspection (`dump()`, `session.graph`), commerce example. ~1,200
lines, tested (table-driven per-axiom cases + property tests).

Next: inference (transitive/inverse relations) with provenance-backed
explanations; severity polish; SHACL export as a differential-testing oracle;
an MCP tool-call proxy; a benchmark for axiom-violation feedback vs generic
retry. See `CONTEXT.md` for the full design rationale and research.
