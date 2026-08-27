import { edgeKey } from "../../data/decode";
import type { GraphEdge } from "../../data/graphmodel";
import type { VerdictPayload, Violation } from "../../types";

export interface Highlight {
  nodes: Set<string>;
  edges: Set<string>;
}

export const emptyHighlight: Highlight = { nodes: new Set(), edges: new Set() };

/** Map a violation onto graph geometry: provenance edge facts light up as the
 * offending path (cycles arrive as ordered base edges); when provenance is
 * empty (rules, cardinality, existence, ...) fall back to the subject nodes
 * plus any edge joining two subjects — which is exactly the fan-in for
 * max_per_target, whose subjects are (target, *pointers). */
export function violationHighlight(violation: Violation, edges: GraphEdge[]): Highlight {
  const nodes = new Set(violation.subjects);
  const lit = new Set<string>();
  for (const fact of violation.provenance) {
    if (fact.kind === "edge") {
      lit.add(edgeKey(fact.subject, fact.predicate, fact.object));
      nodes.add(fact.subject);
      nodes.add(fact.object);
    }
  }
  if (lit.size === 0) {
    for (const edge of edges) {
      if (nodes.has(edge.subject) && nodes.has(edge.object)) lit.add(edge.key);
    }
  }
  return { nodes, edges: lit };
}

export function verdictHighlight(verdict: VerdictPayload, edges: GraphEdge[]): Highlight {
  const nodes = new Set<string>();
  const lit = new Set<string>();
  for (const violation of verdict.violations) {
    const h = violationHighlight(violation, edges);
    h.nodes.forEach((n) => nodes.add(n));
    h.edges.forEach((e) => lit.add(e));
  }
  return { nodes, edges: lit };
}
