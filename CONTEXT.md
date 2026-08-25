# Project Context: Ontology Guardrails for Agent Outputs

> **Purpose of this doc:** complete context handoff for agents (and humans) joining this
> project with zero prior knowledge. It captures the idea, the market research, every
> settled design decision (with rationale), the v1 scope, and the open questions.
> Last updated: 2026-08-24. Status: **Milestones 1 and 2 implemented**.
> Milestone 1 (vertical slice) plus a DX pass (2026-08-24):
> `check`/`try_commit`/`guarded()` session API + `LoreViolation`, Anthropic
> adapter, `to_context()` (resolves open question 11), exported `Graph` for
> typed rules, session inspection (`dump()`, `session.graph`, reprs),
> `py.typed`. Durable sessions (2026-08-24): `Session.snapshot()`/
> `Guard.restore()` JSON round-trip + `Guard.fingerprint` content-hash
> (resolves the session half of open question 8) — the persistence answer for
> stateless deployments, see §4.7. Milestone 2 (2026-08-24): the inference
> layer — relation characteristics, provenance-carrying derived facts, cycle
> detection, commit step numbers, seed validation, `graph.reachable()` (see
> §4.11 and §5); snapshots carry commit steps and the fingerprint hashes
> relation characteristics + inverse pairs.
> See §5 Milestones; code lives in `src/lore/`, demos in `examples/commerce/`
> and `examples/hr/`.
> Name: **`lore`** (chosen 2026-08-24, renamed from the `ontic` placeholder). The
> `lore` dist name on PyPI is held by Instacart's abandoned ML framework, so the
> distribution name is **`agent-lore`** (import stays `lore`); not yet published.

---

## 1. What this project is

A Python library — **"Pydantic for agent outputs"** — where a developer declares their
business domain as an ontology (entity classes, relations, and logical axioms) and
LLM/agent outputs and tool calls are **deterministically validated** against it.
Violations are flagged, rejected, or fed back to the agent as natural-language repair
prompts.

Pydantic validates the *shape of one object*. This validates whether an agent's outputs
*make sense in your world*: cross-object, cross-turn, stateful, relational constraints
("an order can be refunded at most once", "a payout recipient can never be a support-rep
account", "cumulative refunds ≤ order total") that per-object schema validation
structurally cannot express.

**Positioning one-liner:** *Pydantic validates the shape of one object; this validates
whether your agent's outputs make sense in your world.*

This is a **personal project** (author: Maanav, maanav@infiniteworlds.xyz), not a company
— priorities are learning, reputation, community adoption, and a possible
benchmark/paper. See §9.

### Origin / inspiration
- Frank Coyle (UC Berkeley), talk "Why Agentic Systems Need Ontologies"
  (youtube.com/watch?v=Sir59K8ZDPU) — source of the founding framing: "A sentence in a
  spec is a hope; an OWL axiom is a rule a machine enforces." LLM emits candidate facts →
  reasoner checks against ontology → verdict: flag / reject / repair.
- Emil Eifrem (Neo4j), "Thinner Agents on a Smarter Substrate: The Ontology-based
  Semantic Layer" (youtube.com/watch?v=VGN22pPpb-8).
- Latent Space article "Ontologies Are So Back: Why AI Agents Are Reviving the Semantic
  Web" (latent.space/p/ontologies-agentic-systems) — the "neurosymbolic guardrails"
  wave; also features Kingsley Idehen (OpenLink/OPAL).

---

## 2. Market & literature research (done 2026-08-23, two deep web-research sweeps)

### Verdict
The *thesis* (ontologies as deterministic guardrails for agents) is loudly validated
industry-wide. The *artifact* — a developer-ergonomic Python DSL + real ontology axioms
+ deterministic reasoner + repair loop, for arbitrary agent outputs — **does not exist
as a product or maintained library as of mid-2026**. Multiple 2026 papers explicitly
name output-side ontological validation as an open, unimplemented frontier. GitHub
searches for this exact combination come back empty.

### Closest prior art and the gap each leaves
- **Instructor / Pydantic AI** — have the exact repair-loop UX (validation error → error
  message fed back → model retries; `ModelRetry` in Pydantic AI, `max_retries` +
  `ValueError` in Instructor). But all constraints are hand-written imperative Python on
  a single object; no axioms, no state, no reasoner. **These are our integration
  sockets, not competitors.**
- **Guardrails AI / NeMo Guardrails** — per-output checks; many validators are themselves
  ML models (non-deterministic). No relational logic.
- **Outlines / OpenAI structured outputs / grammars (constrained decoding)** — guarantee
  *syntax* (JSON schema/regex/CFG) by token masking at decode time. Cannot express
  stateful/relational constraints. This is the solved layer *below* us.
- **AWS Bedrock Automated Reasoning checks** (GA Aug 2025) — strongest production
  precedent for deterministic symbolic validation of LLM output (SMT-style verification
  against policies extracted from documents). But propositional rules over variables,
  not an ontology (no class hierarchy, no relation axioms over instance graphs); closed
  AWS service, not a library.
- **data.world OBQC** (Allemang & Sequeda, arXiv 2405.11706) — best published evidence
  the approach works: 8 ontological checks (domain, range, double-domain, double-range,
  domain-range chaining, property-existence, 2 output-shape checks) on LLM-generated
  SPARQL; violations rendered from English templates; minimal repair prompt ("query +
  issues, please rewrite"); ≤3 retries then "I don't know". **Accuracy 42.9% → 72.6%
  (80.6% counting honest IDK); error rate 45.8% → 19.4%.** Key detail: the
  multi-triple *relational coherence* checks (double-domain, domain-range) did ~60% of
  the repair work — evidence the value concentrates in cross-object constraints.
  Scope gap: validates SPARQL queries in one product, not arbitrary agent outputs.
- **Palantir AIP/Foundry** — the thesis at platform scale (agents act only through
  governed, ontology-typed Actions). Closed, whole-platform buy-in. We are the open,
  pip-installable version of that insight.
- **LinkML** (+ OntoGPT/SPIRES) — nearest infrastructure neighbor: YAML schema language,
  `gen-pydantic`, SHACL export plugin. No reasoner over instance graphs, no agent
  runtime loop, YAML-first DX. **Biggest competitive risk if it grows this organically.**
- **OntoLogX** (arXiv 2510.01409) — SHACL validate→repair loop for KG extraction, but
  domain-locked (cybersecurity logs), not a general library.
- **arXiv 2504.07640** — attempted almost exactly this idea (HermiT OWL reasoner +
  repair loop in Python) and was **withdrawn** for inaccuracies. Niche remains open;
  also a cautionary tale (see open-world trap, §4.2).
- **arXiv 2604.00555** (enterprise agentic systems) — explicitly: output-side ontology
  validation "is the primary research frontier and is not yet implemented."
- **Logic-LM, LLM-Modulo (Kambhampati), PDDL/VAL loops, Z3-feedback work** — establish
  "LLM proposes, symbolic checker disposes, error feeds back" as a proven pattern
  (+39% on logic tasks for Logic-LM). ACL 2024 self-correction survey: intrinsic
  self-correction is unreliable; **external verifier feedback is what works** (our core
  motivating citation).
- **AgentSpec** (ICSE 2026) — DSL + runtime enforcement for agents, but ad-hoc safety
  predicates, no ontology semantics.
- **CRANE** (ICML 2025) — strict decode-time constraints *degrade reasoning* → we are
  post-hoc validate-and-repair, not constrained decoding.
- **xpSHACL** (arXiv 2507.08432) — LLM-friendly explanations of SHACL violations
  (reusable ideas for our repair messages). **KGValidator** (arXiv 2404.15923) — LLM-as-
  judge validation (the inverse of us). **Stardog Voicebox, Neo4j semantic layer, Timbr,
  Graphwise** — all "ontology as *context* for agents," none "ontology as output
  validator"; they are potential distribution partners/backends, not competitors.

### Semantic-web tooling facts that shaped decisions
- **pySHACL** — mature pure-Python SHACL validator (used by us only for testing/export).
- **owlready2** — Pythonic OWL, but reasoning requires JVM (HermiT/Pellet) — the DX
  failure we must not repeat.
- Graph DBs: RDF stores (Stardog, GraphDB) ship OWL inference + SHACL validation
  natively; property-graph DBs (Neo4j) are semantically shallow. Analogy that settles
  our relationship to them: *Postgres has constraints, Pydantic exists anyway* —
  storage-layer vs application-boundary validation are different products. Graph DBs =
  future pluggable backends via the FactStore seam, not competitors.

---

## 3. Why not just hand-written Python checks? (the core pitch)

For five checks on one output type, hand-written validators are fine — that's the status
quo. We win the way Pydantic won over hand-rolled `if`s: leverage, not capability.
1. **The stateful infrastructure is the product**: "refunded at most once" needs a
   session graph, entity resolution, and cross-turn accumulation — everyone hand-rolling
   it builds a bad bespoke version.
2. **Inference multiplies rules**: declare `transitive` once and violations are caught
   across chains nobody enumerated.
3. **Uniform violations → repair loop for free**: consistent LLM-legible English per
   axiom (where the data.world lift came from), plus shared severity/retry/audit.
4. **Declarative rules are analyzable**: contradiction detection between axioms,
   coverage/fuzzing, introspection, readable by compliance people. Same declaration
   also serves prompt context, docs, and a `can_i()` pre-flight tool.
Escape hatch always exists (`@lore.rule` arbitrary Python) — pitch is "the 80% of
invariants that are relational patterns become declarations; infrastructure is shared."

---

## 4. Settled design decisions (with rationale)

### 4.1 SDK surface: Pydantic models ARE the ontology carrier
- No RDF/Turtle/YAML authoring — authoring friction killed the semantic web; owlready2's
  JVM killed its DX. Users decorate Pydantic models they already have.
- **An axiom lives where its subject lives**: per-class/per-field axioms inline on the
  model; cross-cutting rules registered on the ontology object.
- `@lore.entity` explicit registration (no metaclass auto-magic); `Entity` base class;
  `Relation[Target]` fields carry OWL-style characteristics as kwargs
  (`functional=True`, `transitive=True`, `inverse_of=`, `min_count/max_count`,
  symmetric/asymmetric/irreflexive); `one_of(...)` for enums;
  `lore_disjoint_with = [...]` class attribute.
- **Python inheritance IS the subclass hierarchy** (read from `type(obj).__mro__`,
  filtered to registered entities). One source of truth; cannot drift.
- `@lore.rule` — arbitrary Python predicate escape hatch, same Violation machinery.
- v1 axiom vocabulary (deliberately small; recurs across every domain studied):
  functional, disjoint, domain/range (implicit from `Relation[Target]`), one_of,
  cardinality, existence (dangling ref), transitive, inverse, subclass,
  irreflexive/asymmetric, + Python rule escape hatch.

### 4.2 Semantics: closed world + bounded inference (NOT textbook OWL)
- Textbook OWL is open-world: missing ≠ violation, and a functional-property "violation"
  quietly *merges individuals* instead of erroring. Fatal for guardrails (likely a cause
  of the withdrawn 2504.07640). We want **SHACL-style closed-world checking with OWL's
  vocabulary**.
- Pipeline: bounded inference pre-pass (transitive closure, inverse normalization,
  subclass/type propagation) → closed-world checks on the expanded graph.
- Full DL tableaux reasoners (HermiT etc.) stay out of the runtime path forever: slow,
  JVM, and answer only "consistent?" without naming culprits.

### 4.3 Runtime: propose → check → commit (transactional), never validate-after-ingest
Facts are staged in an overlay; checks run over committed ∪ staged; commit is atomic and
decided by the **verdict**, not the writer. Three failure modes of the naive
ingest-then-validate design that this prevents:
1. **Retry pollution** — a rejected output's facts must leave zero trace or later checks
   fire against the agent's own failed attempts.
2. **Side effects before judgment** — tool calls must be checked as *hypotheticals*
   before execution (gate, not alarm).
3. **Atomicity** — multi-fact proposals land wholly or not at all.
Falls out for free: severity=flag commits anyway; escalate = staged pending human;
`can_i()` pre-flight = propose without commit; audit journal = the commit log;
optimistic-concurrency fix for the commit race has a natural home.

### 4.4 Pipeline stages (ground / infer / check)
- **Ground**: purely mechanical, deterministic, no LLM — typed object/tool call → facts.
  Instance → node typed by MRO; `Relation` field → edge (targets are IDs resolved
  against the graph — unresolved ID = existence violation, not extraction failure);
  scalar → literal.
- **Infer**: derive implied facts with **provenance** (which base facts produced each
  derived fact — powers explanations). v1: three special-cased procedures, not a
  general engine (see §5).
- **Check**: deliberately dumb scans over the completed picture (count per group,
  type-set intersection, self-edge exists…), each mapping to an English template.
- Verdict → structured `Violation`s (axiom, severity, instances, provenance,
  minimal-edit hint) → `verdict.repair_prompt()` renders LLM-legible English.

### 4.5 Extraction / grounding boundary (the "is it deterministic?" answer)
- **Structured outputs & tool calls: deterministic end-to-end** (fields are the facts;
  identity is ID lookup). This is v1 scope, and conveniently where irreversible actions
  live.
- Schemas must reference entities **by ID** (with lookup tools for the agent) — never
  free-text names.
- **Free prose: extracting claims requires an LLM** → guarantee degrades from "output is
  valid" to "extracted claims are valid" (lose completeness, keep soundness of what is
  checked). Explicitly out of v1; later an optional, clearly-labeled advisory layer.

### 4.6 Architecture: framework-free core + thin adapters
- Core (`schema`/`compile`/`engine`/`session`) imports **no agent framework and no
  rdflib**. Adapters <50 lines each; if bigger, the functionality belongs in core.
- Flagship adapter: **Pydantic AI** (`@agent.output_validator` + `ModelRetry` — the
  retry socket already exists; our demo is a 3-line diff to code people already have).
  Instructor works via `ValueError` in a validator (documentation example, not code).
- MCP tool-call proxy = most strategic future adapter (framework-agnostic interception),
  its own milestone, not v1.

### 4.7 Storage: in-memory first; FactStore protocol as the seam
- Session graphs are tiny (10²–10³ facts); dicts/sets; zero infra; `pip install` DX is
  the whole bet. No graph DB, no rdflib at runtime.
- Keep a ~5-method `FactStore` protocol so graph-DB/live-DB backends can arrive later as
  adapters (trigger: a user wanting validation against a live enterprise KG).
- **Durable sessions** (2026-08-24): `session.snapshot()` → storage-agnostic JSON blob
  (committed facts only; snapshotting a pending proposal raises — snapshots are turn
  boundaries), `guard.restore(blob)` rehydrates exactly (facts, order, sources; attr
  values round-trip through their field annotations, so datetimes stay datetimes).
  Blobs are stamped with `Guard.fingerprint`; restore refuses a mismatch. This — not a
  DB backend — is the persistence answer for stateless deployments (worker-per-turn):
  the user stores the blob wherever they already keep state. Still no DB at runtime.

### 4.8 Enforcement point: post-hoc validate + repair (not constrained decoding)
Grammar-level enforcement is solved by others and cannot express our constraints; CRANE
shows decode-time clamping degrades reasoning. Layered stack: JSON schema (in-decode,
free) → per-object Pydantic → our cross-object ontology layer → repair loop.

### 4.9 Dependencies
Runtime: **pydantic only**. rdflib + pySHACL: export/CI only (deferred, see §5).
Framework SDKs: optional extras. Tests: pytest + hypothesis. Engine built from scratch
(~1–2k lines total; it IS the product — provenance, staging, error quality are the
differentiators).

### 4.10 Testing strategy
- Table-driven per-axiom cases + Hypothesis property tests (e.g. "adding a
  second `refunds` edge always yields exactly one functional violation").
- **Deferred but designed-for**: SHACL/OWL export + pySHACL as a *differential-testing
  oracle* in CI (our engine and pySHACL-on-exported-shapes must agree on which
  violations exist; disagreement = bug somewhere). Deferred because for 6 axiom types
  the exporter costs as much as the engine; keep test cases table-driven so the oracle
  bolts on later. Export also doubles as an interop feature eventually.

### 4.11 Inference layer (Milestone 2, settled 2026-08-24)
Relation characteristics on `relation()`: `transitive`, `symmetric`,
`asymmetric` (implies irreflexive), `irreflexive`, `inverse_of` (names a field
on the *target* class; one-sided declarations complete the pair; the finished
map must be an involution). Contradictory combos are compile errors
(symmetric∧asymmetric; symmetric∧transitive∧(irreflexive∨asymmetric);
symmetric requires owner == target; self-inverse → "use symmetric=True";
symmetric∧inverse_of). Decisions, with rationale in the M2 plan:
1. **Cardinality vs derived edges** — transitive-closure edges count toward NO
   cardinality check (OWL precedent). Mirror edges count toward
   `max_per_target`; `single_value` counts symmetric mirrors (same predicate)
   but NOT inverse mirrors (already counted on their home predicate;
   one-to-many pairs would false-positive). Consequence: one-to-one inverse
   pairs need `max_per_target=1` on one side to be enforced cross-direction.
2. **Entity rehydration reads asserted facts only**; rules reach the derived
   layer via `graph.reachable(node_id, predicate)` (asserted + same-predicate
   mirrors + closure).
3. **Derivations are never session state**: recomputed per propose, passed to
   checks via `CheckContext`; a derived edge is staged-involved iff any base
   fact in its provenance path is staged.
4. **Provenance**: flattened path of base facts per derived edge,
   first/shortest path kept, recomputed every propose, never committed. One
   cycle = one violation (self-edges deduped by base-fact set) and the message
   renders the chain: *"emp_9 cannot reach itself via 'reports_to', but this
   proposal creates a cycle: emp_9 → mgr_2 (already committed at step 2) →
   ceo (already committed at step 1) → emp_9 (proposed)."*
5. **Step counter**: successful non-empty commits are numbered 1, 2, 3… and
   facts stamped at commit (seeds = step 0; `step` is compare=False so dedup
   ignores it). Messages render "(already committed at step N)" /
   "(seeded)" / "(proposed)".
6. **Seed validation**: `Session.__init__` runs the full derive+check pass
   treating all seed facts as staged and raises `LoreError` on rejects;
   `validate_seed=False` opts out.
7. Characteristics do NOT propagate across inverse pairs; closure edges are
   not mirrored; rules re-run on staged subjects plus the direct targets of
   asserted staged edges (so aggregate rules survive stray incoming edges),
   but not when only a node's derived neighborhood changes (sound for
   monotone, closure-dependent rules like approver-in-chain).

---

## 5. v1 scope: the simplification pass (cuts + reinstate triggers)

Principle: protect future optionality with **seams and data shapes**, not built
machinery. Everything cut can be added without breaking users.

| Cut from v1 | Replaced by | Bring back when |
|---|---|---|
| Expression DSL (`Path`, `Sum`, operator overloading) | `@lore.rule` Python functions | Same rule patterns hand-written 3× in real usage. Cost if built: ~500–800 lines but 1–2 weeks and ~2× conceptual surface (Pydantic metaclass fight over class-attribute access, `__eq__`/`__hash__` traps, un-overloadable chained comparisons / `and`). Cheap 80% substitute: named helper functions returning rule objects. |
| Generic Datalog engine (semi-naive fixpoint over compiled rules) | 3 special-cased procedures: subclass propagation at grounding (MRO), inverse normalization at insert, one transitive-closure routine with provenance (~100 lines) | A new axiom type needs genuine rule interaction |
| Incremental check scoping | Full re-check per propose (µs at session scale); report violations involving staged facts | A profiler says so |
| SHACL export + differential oracle | Table-driven unit tests + Hypothesis | Axiom vocabulary outgrows exhaustive unit-testing |
| `escalate` severity + callbacks | Two levels: `reject`, `flag` (shadow mode) | Human-approval-queue demand |
| Adapters beyond Pydantic AI; MCP proxy; fuzzer; benchmarks dir; docs site | One adapter; README is the docs | Post-launch; extra adapters are deliberate contribution bait (<50 lines, well-seamed) |
| `@guard.tool_call` registry / auto-interception | `session.guarded()` context manager + `@guard_tool(session)` per-tool wrapper (Anthropic adapter) — 1–2 lines per tool, explicit, correct side-effect ordering (validate → effects → commit). Full registry/interception stays cut: no framework-neutral dispatch point exists yet (the natural one is the MCP proxy), and the unregistered-tool policy + arg→entity mapping DSL is the real work | Tedium at ~dozens of tools; returns naturally with the MCP-proxy milestone |
| Per-predicate world-completeness + per-axiom trust requirements | One blunt documented rule ("session is closed-world over seed + committed") + a `source: "seed"\|"asserted"` tag on every fact NOW (cheap now, painful to retrofit) | A real use case breaks the blunt rule |
| Concurrency handling | Document "sessions are single-threaded; one session per agent run" | Multi-agent/parallel-tool users appear (then: commit-time re-validation / serialized commits) |

### v1 repo layout (7 source files, ~800–1,200 lines)
```
lore/
├── src/lore/
│   ├── __init__.py        # public API: Ontology, Entity, Relation, one_of
│   ├── schema.py          # Entity, Relation, Ontology registry, @entity/@rule
│   ├── compile.py         # ontology self-check (contradictory axioms, unknown
│   │                      #   targets — fail loudly like Pydantic) + axioms→checks
│   ├── store.py           # FactStore protocol + InMemoryStore + fact types
│   ├── engine.py          # ground + the checks
│   ├── infer.py           # derive (mirrors, closure, provenance), CheckContext  [M2]
│   ├── graph.py           # Graph (get/incoming/reachable)  [split from engine, M2]
│   ├── session.py         # propose/commit, Verdict, Violation, repair_prompt()
│   └── adapters/pydantic_ai.py
├── tests/                 # table-driven axiom cases + hypothesis properties
├── examples/commerce/     # the refund demo — README hero
├── examples/hr/           # approval chains + cycle detection  [M2]
└── README.md
```
Split a file only past ~500 lines.

### Milestones
1. **Vertical slice** — ✅ implemented 2026-08-23: schema DSL + ground + InMemoryStore +
   6 checks (existence, domain/range, max_per_target, single_value, one_of, disjoint) +
   `@lore.rule` escape hatch + Verdict/repair prompts + Pydantic AI adapter + refund demo
   + tests. No inference yet. Demos the double-refund catch (`examples/commerce/`).
2. **Inference + provenance** — ✅ implemented 2026-08-24: relation
   characteristics (`transitive`, `symmetric`, `asymmetric`, `irreflexive`,
   `inverse_of`) with compile-time contradiction/involution checks, the
   derive pass (`src/lore/infer.py`) with per-edge provenance, irreflexive +
   asymmetric checks (cycle detection rendering the exact chain), commit step
   numbers in messages, seed validation, `graph.reachable()` for rules
   (`Graph` moved to `src/lore/graph.py`), HR approval-chain example
   (`examples/hr/`). Design decisions in §4.11.
3. Severity/shadow mode polish, (maybe) SHACL export + differential CI.
4. **The benchmark** (see §9 — for a personal project this jumps in priority), MCP
   proxy, fuzzer ("ontology coverage": mutate valid graphs → invalid, measure catch
   rate).

### Canonical demo (README hero — the double-refund catch)
Commerce ontology: `Customer`, `SupportRep` (disjoint w/ Customer), `Order`
(status one_of paid/shipped/refunded, `placed_by: Relation[Customer]`), `Refund`
(`refunds: Relation[Order] = relation(max_per_target=1)`, `paid_to: Relation[Customer]`).
(Correction vs the original sketch's `functional=True`: OWL-functional means ≤1 *object
per subject* — scalar fields hold one value, enforced cross-turn by the `single_value`
check (committed values are immutable, so re-asserting a committed id with a different
target is rejected); refund-once is ≤1 *subject per object*, OWL *inverse*-functional,
spelled `max_per_target=1` to avoid the jargon trap.)
Agent tool `issue_refund` → `session.propose(Refund(...))` → verdict catches: second
refund on same order (max_per_target, cross-turn), payout to a SupportRep (range +
disjoint), refund of nonexistent order (existence). Violation message style (modeled on
data.world templates): *"An order can be refunded at most once, but ord_456 already has
refund ref_88 (committed at step 3). Do not issue another refund; instead explain that
a refund was already processed."*

---

## 6. Open questions (need consensus before/while building)

**Gate Milestone 1** (they shape FactStore/Session interfaces):
1. World-completeness contract — which predicates is the session authoritative
   (closed-world) for; how declared? (v1: blunt rule per §5, but design the seam.)
2. Trust labels — verified vs agent-asserted facts; which axioms require which? (v1:
   `source` tag on facts only.)
3. Draft entities — provisional/blank-node identities for not-yet-created entities;
   cross-references among multiple drafts in one proposal; IDs minted at commit.
7. Eager seed vs lazy lookup through FactStore at check time (freshness vs determinism;
   couples to #1).

**Decidable later:**
4. Time/retraction — lean: append-only facts with supersession; state-machine axiom
   over the supersession chain.
5. Expressiveness ceiling — write the explicit "no" list (negation, disjunction,
   user recursion → escape hatch). Negation-as-failure interacts dangerously with #1.
6. Concurrent-commit race — v1 documents single-threaded sessions; later optimistic
   re-validation at commit.
8. Ontology versioning — **session half resolved 2026-08-24**: `Guard.fingerprint`
   (sha256 over the declarative semantics; `@lore.rule` *bodies* deliberately not
   hashed — deterministic across processes beats catching silent logic edits) stamps
   snapshots, and `restore` refuses a mismatch. Still open: stamping verdicts.
9. Rollout modes — observe/enforce per axiom ships v1 (flag severity); analytics later.
10. Repair-prompt shape — state *what's true* vs *what the check wants* (Goodhart risk:
    agent told "ord_456 already refunded" may just refund ord_457); all-violations vs
    top-k; escalate on repeated near-misses. Nobody has published on this —
    potential novel finding.
11. ~~Prompt-side scope~~ — **resolved 2026-08-24**: `guard.to_context()` (with an
    `Ontology.to_context()` delegate) renders the ontology as deterministic
    system-prompt English. Enables the killer experiment: context-only vs
    validation-only vs both.
12. License — MIT vs Apache-2.0 (lean Apache if a company might grow out of it).
13. Injection hardening — violation messages interpolate graph data; a hostile display
    name becomes prompt injection *via the guardrail*. Quote/fence all data fields.
Plus: retry-exhaustion surface (typed "cannot produce valid output" failure — the
data.world "I don't know" path), package name + PyPI/GitHub squatting (do early),
employment-IP hygiene (personal time/equipment).

---

## 7. Target domains & example axioms (demand-side validation)

Same small axiom vocabulary recurs everywhere; highest-value checks are stateful and
relational; many are really fraud/compliance controls (auditor = eventual buyer).
- **Customer service/commerce** (beachhead: agent adoption, vivid demos): refund-once
  (functional), payout-to-buyer-not-rep (range+disjoint), Σrefunds ≤ total, status
  transitions, no invented policies (Air Canada chatbot precedent).
- **Accounting**: Σdebits = Σcredits per journal entry; postings only to existing
  accounts; account type disjoint-union; no postings to closed periods; payment
  reconciles exactly one invoice.
- **Tax**: dependent claimed on ≤1 return (functional); spouse ∧ dependent disjoint;
  filing-status one_of + cardinality; jurisdiction nexus.
- **Legal**: cited case exists + not overruled + binding jurisdiction (Mata v. Avianca
  as a deterministic check); defined terms defined; party-role disjointness; dangling
  section cross-references.
- **Healthcare**: drug-interaction/allergy relations vs patient graph; dose ranges per
  patient class; prescriber authority; ICD code existence. (Free TBoxes: SNOMED, RxNorm.)
- **Insurance**: claim against policy active on date-of-loss; payout ≤ coverage line
  limit; adjuster ≠ claimant; one payout per claim line.
- **HR**: no self-approval (irreflexive — a real SOX control); approver in transitive
  reports_to chain; salary band per level.
- **DevOps**: referenced resources exist; prod must-not-depend-on dev; instance in
  exactly one VPC; no delete on protected resources.

---

## 8. Key background explainers (for agents unfamiliar with the space)
- **OWL** = W3C language for domain logic (classes, relation axioms) + reasoners that
  *infer*; **open-world** (missing ≠ wrong; functional violations merge individuals).
  **SHACL** = W3C shapes/constraints language; **closed-world**; produces violation
  reports. We use OWL's vocabulary with SHACL's checking behavior (§4.2).
- **Datalog** = facts + recursive rules, bottom-up evaluation to a guaranteed-terminating
  fixpoint (semi-naive = each round only joins against facts new since last round). Our
  inference stage is a special-cased fragment of it; the expressiveness ceiling (§6 Q5)
  is "don't accidentally rebuild full Datalog with worse syntax."
- **MRO** = Python's Method Resolution Order (`cls.__mro__`, C3 linearization) — we read
  it as the ontology's subclass hierarchy at grounding.
- **Constrained decoding** = token-masking against a compiled grammar/schema during
  generation; guarantees syntax only; the solved layer below us.
- **SMT** (Z3 etc.) = satisfiability solvers over arithmetic/theories; Bedrock's engine;
  a possible future backend for arithmetic-heavy axioms — sibling symbolic checker,
  graph/relational constraints are awkward in it, native in ours.
- **Differential testing / oracle** = run two independent implementations on the same
  input; any disagreement proves a bug somewhere; pySHACL is our planned oracle.

---

## 9. Personal-project strategy notes
- Payoff = learning + reputation + optionality, which **promotes the benchmark**
  ("axiom-violation feedback vs generic retry", planted relational traps, measure
  violation rates / gaming) to the highest-leverage artifact: launch post, credibility,
  possible workshop paper occupying a gap multiple 2026 papers name explicitly.
- Scope discipline is existential: Milestone 1 = core + ONE adapter + ONE example +
  README. README is the marketing department. Extra adapters = contribution bait.
- The architecture seams chosen for "enterprise" reasons (FactStore, thin adapters,
  framework-free core) are the same seams that make solo maintenance + community PRs
  safe. Nothing architectural changes with the personal-project framing.

## 10. Source links (research runs, 2026-08-23)
- latent.space/p/ontologies-agentic-systems · youtube Sir59K8ZDPU (Coyle) ·
  youtube VGN22pPpb-8 (Eifrem)
- arXiv: 2405.11706 (OBQC) · 2510.01409 (OntoLogX) · 2504.07640 (withdrawn HermiT-loop)
  · 2604.00555 (output-side gap named) · 2503.18666 (AgentSpec) · 2305.12295 (Logic-LM)
  · 2402.01817 (LLM-Modulo) · 2502.09061 (CRANE) · 2507.08432 (xpSHACL) ·
  2404.15923 (KGValidator) · 2406.01297 (self-correction survey) ·
  2507.22419 (KG repair w/ LLMs) · 2606.13405 (DFKI agenda)
- docs.aws.amazon.com/bedrock/latest/userguide/guardrails-automated-reasoning-checks.html
- linkml.io · github.com/monarch-initiative/ontogpt · github.com/RDFLib/pySHACL ·
  owlready2.readthedocs.io · palantir.com/docs/foundry/aip/overview ·
  python.useinstructor.com · ai.pydantic.dev (now pydantic.dev/docs/ai)
