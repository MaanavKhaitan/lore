# τ²-bench retail: policy coverage, enforcement gaps, and task selection

Groundwork for the lore benchmark (analyzed 2026-08-25, **τ³-bench** —
i.e. tau2-bench v1.0.1, commit `a2c0247` — retail domain: 114 tasks,
`policy.md`, `tools.py`). Note on naming: "τ³" is the v1.0 release of the
same `sierra-research/tau2-bench` repo (voice mode + `banking_knowledge`
domain + 75+ task fixes per SABER, arXiv 2512.07850); we use the retail
domain in text mode, which under τ³ is the *corrected* task set. Report
results as "τ³-bench (tau2-bench v1.0.1), retail, text mode" and pin the
version — v1.0.1 changed banking_knowledge grading only, retail unaffected.
The clone lives in `.context/tau2-bench` (gitignored); the draft ontology
is `world.py` in this directory (compiles and passes a trap smoke test
against lore @ current main).

## Finding 1: the environment already enforces most per-call rules

τ²-bench's retail tools are hand-hardened (~600 lines): status gates,
item/variant matching, gift-card balances, payment-method ownership, refund
destinations, cancel-reason validity all raise `ValueError` inside the tool.
A baseline agent that attempts these violations gets an error string and can
recover. **Lore's incremental detection surface on the stock environment is
the procedural, cross-turn layer the tools cannot see:**

1. **Authentication** — tools accept any `user_id`/`order_id` directly;
   nothing checks the agent authenticated anyone (policy: authenticate
   first, even if the user provides their id).
2. **Cross-user scope** — the agent can read and *write* another user's
   orders (e.g. cancel any pending order); only payment-method lookups are
   user-scoped. Policy: one user per conversation, deny everything else.
3. **The items-then-address trap** — `modify_pending_order_items` sets
   status to `"pending (item modified)"`; policy says the order may not be
   modified or cancelled after that. But `modify_pending_order_address` /
   `_payment` gate on `_is_pending_order`, which is a **substring** check
   (`"pending" in status`), so the environment permits what the policy
   forbids. Ordering matters cross-turn: address/payment must be modified
   *before* items. 14 tasks require both writes on the same order.
4. **Refusal discipline** — for impossible requests, lore's `check()`
   produces the deny-with-reason repair prompt *before* any state change.

## Finding 2: reward and policy compliance are partially decoupled

τ²-bench's reward compares final DB state (+ NL assertions) to gold. An
items-then-address violation produces the *same final DB state* as the
compliant order of operations, so the reward does not see it. Consequence
for the eval: **pass^k alone under-measures lore's effect; violation count
must be a primary metric.** Design twist that falls out: run lore in
shadow (flag-only) mode over the *baseline* arm's transcripts to count
violations there — lore is both the intervention (enforce + repair) and the
measurement instrument. This also means the headline table has two rows of
evidence: task reward (pass^1/pass^4) and violations-per-conversation
(baseline shadow count vs. lore-arm count, which is ~0 by construction for
expressible clauses).

## Policy clause coverage

| # | Policy clause | Env enforces? | Lore expresses? |
|---|---|---|---|
| 1 | Authenticate before acting (even if id given) | **no** | yes (`action_requires_owner_auth`) |
| 2 | One user per conversation; deny others | **no** | yes (`max_per_target=1` on `Authentication.in_session` + owner-auth rules) |
| 3 | Explicit confirmation before each write | **no** | partial — needs a `Confirmation` dialogue-act entity asserted by the harness; out of scope for v1 |
| 4 | Act only on pending / delivered orders | yes | yes (status rules) |
| 5 | Exchange / modify-items once per order | yes (via status change) | yes (`max_per_target=1`) |
| 6 | No modify/cancel after items-modified | **partial — address/payment modify slips through (substring bug)** | yes (`no_modify_after_items_modified`, `cancel_requires_clean_pending`) |
| 7 | Cancel reason ∈ {no longer needed, ordered by mistake} | yes | yes (`one_of`) |
| 8 | Gift card must cover amount / difference | yes | yes (rule) |
| 9 | Return refund → original method or gift card | yes | yes (rule) |
| 10 | New payment method must differ; single method | yes | yes (rule) |
| 11 | Exchange/modify within same product type | yes | expressible with item-level modeling (Item/OrderLine entities); v2 |
| 12 | One tool call at a time; no reply+call together | harness-level | out of scope |
| 13 | No fabricated info / subjective recommendations | no | out of scope (semantic, not relational) |
| 14 | Transfer to human iff request out of scope | no | partial (lore can prove "impossible under the rules", not "out of scope") |

Score: 10/14 fully expressible, 2 partial, 2 out of scope (both are
dialogue/semantic clauses, not state clauses — worth stating in the
writeup as the boundary of the approach).

## Task selection

Selection is a deterministic property of each task's gold-action
annotations (`evaluation_criteria.actions`) — no model behavior involved.
Generated by `.context/select_tasks.py`:

| Stratum | Rule | n | Task ids |
|---|---|---|---|
| `order_trap` | items-modify + address/payment-modify on the **same order** (finding-1.3) | 14 | 41, 42, 71, 72, 78, 96, 97, 101, 102, 103, 104, 109, 111, 112 |
| `multi_distinct` | ≥2 distinct write tools (cross-turn state) | 18 | 16, 22, 23, 30, 31, 32, 35, 54, 55, 59, 64, 74, 86, 87, 91, 98, 100, 110 |
| `refusal` | gold = no actions (agent must deny) | 2 | 24, 57 |
| `transfer` | gold ends in transfer_to_human_agents | 4 | 10, 12, 26, 50 |
| `read_ctrl` | read-only gold actions (false-positive control) | 5 | 25, 62, 65, 67, 68 |

**Probe → final 20:** run the baseline once over the 38 candidate tasks
(order_trap ∪ multi_distinct ∪ refusal ∪ transfer, ~$40), keep the ~15
that produce observed violations or failures, add the 5 `read_ctrl`
controls. Publish the final id list + this rule.

## Eval arms and metrics (main run)

- **A. baseline** — stock τ²-bench agent, policy in prompt (as shipped).
- **B. + lore** — same agent; write tools wrapped via the adapter
  (propose → repair-prompt on reject), `Authentication` asserted by the
  harness on successful `find_user_id_by_*`, final summary re-checked.
- Deferred variants: `+to_context()` (prevention arm) and a **naive-tools**
  arm (strip the ~600 lines of hand-written checks; lore supplies them
  declaratively from ~250 lines of `world.py`) — the "lore replaces guard
  code" claim, transparently labeled as a modified environment.

Metrics per arm: pass^1 / pass^4 (τ²-bench reward); violations per
conversation (lore shadow mode on A, enforce mode on B); honest-refusal
rate on refusal/transfer tasks; false-positive blocks on `read_ctrl`;
token overhead and added turns on B.

Cost at 20 tasks × 2 arms × 4 trials + probe: ≈ $250–300 (Sonnet 5,
prompt caching on, user-sim fixed).

## Open items

- Harness: tau2-bench agent integration (its agent loop → lore adapter);
  seeding a session from each task's db.json slice; shadow-mode runner.
- `Confirmation` modeling decision (clause 3) — v1: document as out of
  scope; v2 candidate.
- Item-level modeling for clause 11 if the naive-tools arm is run.
