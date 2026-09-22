# Current design

Lore validates relationships across an agent run, complementing Pydantic's
per-object validation. The distribution is `agent-lore`; the import is `lore`.
The project is MIT licensed. This document describes the implementation;
historical proposals and research live in [the archive](archive/project-context.md).
See [README](../README.md) for usage and [CONTRIBUTING](../CONTRIBUTING.md)
for local setup.

## Boundaries and invariants

- One single-threaded session per agent run. The world is closed over seed
  and committed facts; unknown relation targets are violations.
- Facts are append-only. Scalar attributes and relations cannot change
  once asserted. Multi-valued relations accumulate references, not removals.
  Explicitly setting a field to `None` where a committed value exists is a
  `retraction` violation, not a silent no-op; a `None` with no committed
  value stays a no-op ("no statement"). Domains model mutable business
  state with new action entities.
- Pydantic validates object shape; lore grounds registered entities into
  type, edge, and attribute facts and checks their combined meaning.
- Proposals are checked in an overlay. Rejection or rollback adds no facts;
  commit merges the whole proposal and stamps a monotonically increasing step.
- `reject` violations block commit; `flag` violations remain visible but
  permit it. Seeds are checked by default; `validate_seed=False` explicitly
  accepts preexisting inconsistencies.
- `check()` preserves any pending proposal. `try_commit()` commits or
  discards its proposal. `guarded()` validates before executing its body
  and commits only after success. External side effects are not reversible
  by lore; body failures discard only the proposed facts.

## Checks, inference, and custom logic

Declarative checks cover existence, relation domain/range, per-target
cardinality, scalar immutability, allowed values, disjoint classes, coherent
entity typing, irreflexivity, and asymmetry. Class inheritance supplies
ancestor type facts during grounding.

Inference computes inverse/symmetric mirrors and transitive closure with
provenance. Derived facts are never committed. Shape checks and entity
hydration use asserted facts; cardinality also sees mirrors; irreflexive
and asymmetric checks see the full derived graph. Relation characteristics
do not automatically propagate across inverse pairs.

Custom rules receive an entity and a read-only `Graph`. `get()` and
`incoming()` expose asserted facts; `reachable()` also sees derived edges.
Use `graph.get(id, ExpectedClass)` when following a relation and handle
`None`: existence/range checks report absent or wrongly typed targets.
Other programming errors in rules remain exceptions, not policy verdicts.

Violation messages become repair prompts, so untrusted interpolations —
entity ids and rule-template field values — render quoted with escapes
unless they look like ordinary ids; the message templates themselves are
author-trusted.

Rules on staged subjects and their one-asserted-hop neighborhood report all
failures. Elsewhere, committed entities are checked differentially: only
rules newly broken by the proposal are reported. This catches non-monotone
aggregates and dependencies beyond one hop without repeating unrelated
preexisting failures. Rule functions must be deterministic and side-effect
free; they can run more than once for one proposal.

Goals are separate completion checks, explicitly run with `check_goals()`
on the committed graph. They do not run during proposals or seed validation.
A goal over a class with no instances passes vacuously.

## Persistence and observability

`snapshot()` and `Guard.restore()` preserve fact order, provenance, commit
steps, and typed attribute values. The snapshot codec uses each field's
Pydantic annotation. Pending proposals cannot be snapshotted. The same fact
encoding serves visualization traces.

The fingerprint covers declarative schema and rule/goal metadata, not
Python function bodies or external state. Restore rejects a fingerprint
mismatch but by default trusts decoded facts without rerunning policy
rules, so editing a function body alone does not invalidate existing
snapshots. `restore(blob, revalidate=True)` re-runs every check over the
restored world and raises if it draws rejects under the current rules.
Migration and retraction remain outside the current API.

`SessionRecorder` observes synchronous lifecycle events without mutating
the session. `lore.viz` renders a static world/session viewer; React/Vite
sources live in `ui/`, with built assets committed under
`src/lore/viz/assets/` so installed users do not need Node.

## Code organization

| Module | Responsibility |
| --- | --- |
| `schema.py` | Entity/relation declarations and rule/goal registry |
| `compile.py` | Definition checks, compiled schema, fingerprint, prompt context |
| `store.py` | Fact types, store protocol, in-memory store, layered views |
| `infer.py` | Derivations, provenance, and check context |
| `graph.py` | Read-only graph queries and entity hydration |
| `engine.py` | Grounding, structural checks, rule and goal evaluation |
| `session.py` | Transaction lifecycle, persistence entry points, inspection |
| `snapshot.py` | Snapshot envelope and fact encoding/decoding |
| `verdict.py` | Violations, severity, and repair prompts |
| `adapters/` | Pydantic AI retries and Anthropic tool-result wrappers |
| `viz/` | Viewer specification, trace recording, and static HTML export |

Core tests live in `tests/`, with structural checks, custom-rule behavior,
and multi-valued relations in separate modules. Other suites cover schema
compilation, sessions, snapshots, goals, adapters, properties, and the viewer.
Examples cover commerce, HR, accounting, and legal.

## Benchmark status and limitations

`benchmarks.tau3_airline` is an importable package containing the airline
world and policy tests. Its optional `harness` package integrates with a
separate tau2 checkout: a guarded live environment, a shadow replayer, and
an entry point invoked with `python -m`.

The harness rejects mapping errors before executing writes. If an external
write succeeds but lore cannot record it, the environment raises a fatal
consistency error and refuses further live calls. Gold actions intentionally
bypass the guard; trajectory replay goes through it.

[Results are committed](../benchmarks/tau3_airline/RESULTS.md); these are
single-round experiments with documented limitations. A known policy-test
failure remains: upgrading to business and then cancelling causes a
differential rule recheck to invalidate the earlier upgrade. This requires
a domain rule-semantics decision, not a change to generic rule checking.

Not implemented: a generic Datalog/expression engine, SHACL export, an MCP
proxy, automatic interception of arbitrary tools, concurrency, or temporal
fact retraction. Add these only when supported by concrete use cases.
