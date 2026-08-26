import type { AttrFact, Fact, Spec } from "../types";
import { edgeKey } from "./decode";

export interface GraphNode {
  id: string;
  cls: string; // most-specific class, "?" for a referenced-but-untyped id
  seed: boolean;
  step: number;
  ghost: boolean; // referenced by an edge but never typed (dangling reference)
  proposed: boolean;
}

export interface GraphEdge {
  key: string; // subject|predicate|object — matches highlight.ts / Violation mapping
  subject: string;
  predicate: string;
  field: string;
  object: string;
  step: number;
  proposed: boolean;
}

export interface GraphModel {
  nodes: GraphNode[];
  edges: GraphEdge[];
  attrs: Map<string, AttrFact[]>; // node id → attribute facts, for the panel
}

/** Most-specific class per node: lore guarantees a node's classes sit on one
 * ancestry chain, so the one with the longest `parents` list wins. */
function mostSpecific(classes: string[], spec: Spec): string {
  const depth = new Map(spec.classes.map((c) => [c.name, c.parents.length]));
  let best = classes[0] ?? "?";
  for (const cls of classes) {
    if ((depth.get(cls) ?? -1) > (depth.get(best) ?? -1)) best = cls;
  }
  return best;
}

/** Build the node-link model from step-stamped facts, plus optional proposed
 * (staged, uncommitted) facts rendered dashed — used by the State tab
 * (committed only) and the Timeline mini-graphs (committed + proposal). */
export function buildGraph(
  committed: Fact[],
  spec: Spec,
  opts: { maxStep?: number; proposed?: Fact[] } = {},
): GraphModel {
  const maxStep = opts.maxStep ?? Infinity;
  const visible = committed.filter((f) => f.step <= maxStep);
  const all: { fact: Fact; proposed: boolean }[] = [
    ...visible.map((fact) => ({ fact, proposed: false })),
    ...(opts.proposed ?? []).map((fact) => ({ fact, proposed: true })),
  ];

  const classesByNode = new Map<string, string[]>();
  const nodeMeta = new Map<string, { seed: boolean; step: number; proposed: boolean }>();
  const edges: GraphEdge[] = [];
  const attrs = new Map<string, AttrFact[]>();

  for (const { fact, proposed } of all) {
    if (fact.kind === "type") {
      const existing = classesByNode.get(fact.node) ?? [];
      classesByNode.set(fact.node, [...existing, fact.class]);
      const meta = nodeMeta.get(fact.node);
      if (!meta || (meta.proposed && !proposed)) {
        nodeMeta.set(fact.node, { seed: fact.source === "seed", step: fact.step, proposed });
      }
    } else if (fact.kind === "edge") {
      edges.push({
        key: edgeKey(fact.subject, fact.predicate, fact.object),
        subject: fact.subject,
        predicate: fact.predicate,
        field: fact.predicate.split(".")[1] ?? fact.predicate,
        object: fact.object,
        step: fact.step,
        proposed,
      });
    } else {
      const list = attrs.get(fact.subject) ?? [];
      list.push(fact);
      attrs.set(fact.subject, list);
    }
  }

  const nodes: GraphNode[] = [...classesByNode.entries()].map(([id, classes]) => {
    const meta = nodeMeta.get(id)!;
    return { id, cls: mostSpecific(classes, spec), ghost: false, ...meta };
  });

  // Ghost nodes for dangling references, so existence violations are visible.
  // They inherit the referencing edge's step: a ghost must not predate the
  // edge that conjures it (the scrubber would show it "as seeded" otherwise).
  const known = new Set(nodes.map((n) => n.id));
  for (const edge of edges) {
    for (const id of [edge.subject, edge.object]) {
      if (!known.has(id)) {
        known.add(id);
        nodes.push({
          id,
          cls: "?",
          seed: false,
          step: edge.step,
          ghost: true,
          proposed: edge.proposed,
        });
      }
    }
  }
  return { nodes, edges, attrs };
}
