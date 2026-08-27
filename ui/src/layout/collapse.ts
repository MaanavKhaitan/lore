import type { GraphEdge, GraphModel, GraphNode } from "../data/graphmodel";
import type { Spec } from "../types";

const THRESHOLD = 8;

export interface Aggregate {
  key: string; // `${object}|${predicate}` — pass back in `expanded` to open
  count: number;
  label: string;
}

/** Hub fan-in collapsing: when one node has more than THRESHOLD incoming
 * edges of the same relation, the leaf subjects (nodes with no other
 * connections) fold into a single "N Clauses via in_section" node. Keeps a
 * 200-invoice vendor readable; click-to-expand is the caller's `expanded`. */
export function collapseHubs(
  model: GraphModel,
  spec: Spec,
  expanded: Set<string>,
): { model: GraphModel; aggregates: Map<string, Aggregate> } {
  const degree = new Map<string, number>();
  for (const edge of model.edges) {
    degree.set(edge.subject, (degree.get(edge.subject) ?? 0) + 1);
    degree.set(edge.object, (degree.get(edge.object) ?? 0) + 1);
  }

  const groups = new Map<string, GraphEdge[]>();
  for (const edge of model.edges) {
    const key = `${edge.object}|${edge.predicate}`;
    groups.set(key, [...(groups.get(key) ?? []), edge]);
  }

  const hiddenNodes = new Set<string>();
  const hiddenEdges = new Set<string>();
  const aggregates = new Map<string, Aggregate>();
  const extraNodes: GraphNode[] = [];
  const extraEdges: GraphEdge[] = [];

  for (const [key, edges] of groups) {
    if (edges.length <= THRESHOLD || expanded.has(key)) continue;
    // Only leaf subjects fold — a subject with other edges stays visible.
    const leaves = edges.filter((e) => (degree.get(e.subject) ?? 0) === 1 && !e.proposed);
    if (leaves.length < 2) continue;
    const [object, predicate] = [edges[0].object, edges[0].predicate];
    const owner = spec.relations.find((r) => r.predicate === predicate)?.owner ?? "node";
    const label = `${leaves.length} ${owner}s via ${edges[0].field}`;
    for (const edge of leaves) {
      hiddenNodes.add(edge.subject);
      hiddenEdges.add(edge.key);
    }
    const aggId = `agg:${key}`;
    aggregates.set(aggId, { key, count: leaves.length, label });
    extraNodes.push({
      id: aggId,
      cls: label,
      seed: false,
      step: 0,
      ghost: false,
      proposed: false,
    });
    extraEdges.push({
      key: aggId,
      subject: aggId,
      predicate,
      field: edges[0].field,
      object,
      step: 0,
      proposed: false,
    });
  }

  return {
    model: {
      nodes: [...model.nodes.filter((n) => !hiddenNodes.has(n.id)), ...extraNodes],
      edges: [...model.edges.filter((e) => !hiddenEdges.has(e.key)), ...extraEdges],
      attrs: model.attrs,
    },
    aggregates,
  };
}
