import type { Fact, Snapshot, Spec, Trace } from "../types";
import { SPEC_FORMAT, TRACE_FORMAT } from "../types";

/** Content identity for a fact — mirrors InMemoryStore.add's dedup key
 * (store.py), which ignores source and step. */
export function factKey(fact: Fact): string {
  if (fact.kind === "type") return `type|${fact.node}|${fact.class}`;
  if (fact.kind === "edge") return `edge|${fact.subject}|${fact.predicate}|${fact.object}`;
  return `attr|${fact.subject}|${fact.attr}|${JSON.stringify(fact.value)}`;
}

/** Identity of the graph edge a fact draws, for violation highlighting. */
export function edgeKey(subject: string, predicate: string, object: string): string {
  return `${subject}|${predicate}|${object}`;
}

export function nodeOf(fact: Fact): string {
  return fact.kind === "type" ? fact.node : fact.subject;
}

function checkFormat(payload: { format: number }, expected: number, what: string): void {
  if (payload.format !== expected) {
    throw new Error(
      `this viewer reads ${what} format ${expected}, got format ${payload.format} — ` +
        "re-export with a matching lore version",
    );
  }
}

export function decodeSpec(raw: unknown): Spec {
  const spec = raw as Spec;
  checkFormat(spec, SPEC_FORMAT, "spec");
  return spec;
}

export function decodeTrace(raw: unknown): Trace {
  const trace = raw as Trace;
  checkFormat(trace, TRACE_FORMAT, "trace");
  return trace;
}

export function decodeSnapshot(raw: unknown): Snapshot {
  const snapshot = raw as Snapshot;
  checkFormat(snapshot, 1, "snapshot");
  return snapshot;
}
