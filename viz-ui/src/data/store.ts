import type { AttrFact, EdgeFact, Fact, TypeFact } from "../types";
import { factKey } from "./decode";

/** Content-deduped fact accumulator mirroring InMemoryStore.add (store.py):
 * insertion order preserved, re-adding a fact with a different source/step is
 * a no-op — first write wins, exactly like the Python store. */
export class FactStore {
  readonly facts: Fact[] = [];
  private readonly seen = new Set<string>();

  add(facts: Iterable<Fact>): void {
    for (const fact of facts) {
      const key = factKey(fact);
      if (!this.seen.has(key)) {
        this.seen.add(key);
        this.facts.push(fact);
      }
    }
  }

  types(): TypeFact[] {
    return this.facts.filter((f): f is TypeFact => f.kind === "type");
  }

  edges(): EdgeFact[] {
    return this.facts.filter((f): f is EdgeFact => f.kind === "edge");
  }

  attrs(): AttrFact[] {
    return this.facts.filter((f): f is AttrFact => f.kind === "attr");
  }
}
