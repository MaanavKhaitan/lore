import type { Fact, Trace, VerdictPayload } from "../types";
import { FactStore } from "./store";

// One Timeline row. propose→commit and propose→rollback pairs are folded into
// a single row; the rest map 1:1 to events. `stepBefore` is the committed
// step count when the row's event happened (what the scrubber shows as "the
// world it saw").
type RowBody =
  | { kind: "seeded"; facts: Fact[] }
  | { kind: "restored"; facts: Fact[] }
  | { kind: "committed"; step: number | null; facts: Fact[]; verdict: VerdictPayload }
  | { kind: "rejected"; facts: Fact[]; verdict: VerdictPayload }
  | { kind: "withdrawn"; facts: Fact[]; verdict: VerdictPayload } // ok but rolled back
  | { kind: "pending"; facts: Fact[]; verdict: VerdictPayload } // trace ended mid-proposal
  | { kind: "check"; facts: Fact[]; verdict: VerdictPayload }
  | { kind: "check_goals"; verdict: VerdictPayload }
  | { kind: "snapshot" };

export type Row = RowBody & { index: number; stepBefore: number };

export interface Replay {
  rows: Row[];
  /** Final committed facts, step-stamped — replaying the whole trace yields
   * exactly session.snapshot()'s fact list (pinned by the Python test suite's
   * replay-invariant test). State at step N is facts.filter(f => f.step <= N). */
  facts: Fact[];
  maxStep: number;
}

export function replay(trace: Trace): Replay {
  const store = new FactStore();
  const rows: Row[] = [];
  let pending: { facts: Fact[]; verdict: VerdictPayload } | null = null;
  let maxStep = 0;
  const push = (row: RowBody) =>
    rows.push({ ...row, index: rows.length, stepBefore: maxStep });

  for (const event of trace.events) {
    switch (event.type) {
      case "session_start": {
        push({ kind: "seeded", facts: event.facts ?? [] });
        store.add(event.facts ?? []);
        break;
      }
      case "restore": {
        push({ kind: "restored", facts: event.facts ?? [] });
        store.add(event.facts ?? []);
        maxStep = Math.max(maxStep, ...(event.facts ?? []).map((f) => f.step));
        break;
      }
      case "propose": {
        // Defensive: a pending proposal here means a trace predating the
        // implicit-rollback event — surface it rather than dropping the row.
        if (pending) {
          push({
            kind: pending.verdict.ok ? "withdrawn" : "rejected",
            facts: pending.facts,
            verdict: pending.verdict,
          });
        }
        pending = { facts: event.facts ?? [], verdict: event.verdict! };
        break;
      }
      case "commit": {
        if (!pending) break; // unreachable in a well-formed trace
        const step = event.step ?? null;
        const stamped =
          step === null ? [] : pending.facts.map((f) => ({ ...f, step }) as Fact);
        push({ kind: "committed", step, facts: stamped, verdict: pending.verdict });
        store.add(stamped);
        if (step !== null) maxStep = Math.max(maxStep, step);
        pending = null;
        break;
      }
      case "rollback": {
        if (!pending) break;
        push({
          kind: pending.verdict.ok ? "withdrawn" : "rejected",
          facts: pending.facts,
          verdict: pending.verdict,
        });
        pending = null;
        break;
      }
      case "check": {
        push({ kind: "check", facts: event.facts ?? [], verdict: event.verdict! });
        break;
      }
      case "check_goals": {
        push({ kind: "check_goals", verdict: event.verdict! });
        break;
      }
      case "snapshot": {
        push({ kind: "snapshot" });
        break;
      }
    }
  }
  if (pending) push({ kind: "pending", facts: pending.facts, verdict: pending.verdict });
  return { rows, facts: store.facts, maxStep };
}
