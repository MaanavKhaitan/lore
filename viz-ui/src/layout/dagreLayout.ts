import dagre from "@dagrejs/dagre";

export interface Point {
  x: number;
  y: number;
}

export interface LayoutNode {
  id: string;
  width: number;
  height: number;
  /** Stable ordering key — layout must be deterministic, so callers pass
   * (declaration index, id)-style keys and we sort before inserting. */
  sort: string;
}

export interface LayoutEdge {
  id: string;
  from: string;
  to: string;
  sort: string;
}

export interface LayoutResult {
  positions: Map<string, Point>; // node id → center
  edgePoints: Map<string, Point[]>; // edge id → polyline
  width: number;
  height: number;
}

/** Deterministic layered layout: same input set → same picture, every render.
 * Nodes/edges are sorted before insertion (dagre is insertion-order
 * sensitive), the ranker is fixed, and the dagre version is pinned exactly. */
export function runLayout(
  nodes: LayoutNode[],
  edges: LayoutEdge[],
  opts: { rankdir?: "LR" | "TB"; ranksep?: number; nodesep?: number } = {},
): LayoutResult {
  const g = new dagre.graphlib.Graph({ multigraph: true });
  g.setGraph({
    rankdir: opts.rankdir ?? "LR",
    ranker: "network-simplex",
    ranksep: opts.ranksep ?? 60,
    nodesep: opts.nodesep ?? 24,
    edgesep: 12,
    marginx: 16,
    marginy: 16,
  });
  g.setDefaultEdgeLabel(() => ({}));

  const byKey = <T extends { sort: string; id: string }>(a: T, b: T) =>
    a.sort < b.sort ? -1 : a.sort > b.sort ? 1 : a.id < b.id ? -1 : 1;
  for (const node of [...nodes].sort(byKey)) {
    g.setNode(node.id, { width: node.width, height: node.height });
  }
  // Self-edges distort dagre's ranking; lay the graph out without them and
  // synthesize a loop on the node's right edge afterwards.
  const nodeIds = new Set(nodes.map((n) => n.id));
  const selfEdges: LayoutEdge[] = [];
  for (const edge of [...edges].sort(byKey)) {
    if (!nodeIds.has(edge.from) || !nodeIds.has(edge.to)) continue;
    if (edge.from === edge.to) selfEdges.push(edge);
    else g.setEdge(edge.from, edge.to, {}, edge.id);
  }

  dagre.layout(g);

  const positions = new Map<string, Point>();
  for (const id of g.nodes()) {
    const n = g.node(id);
    positions.set(id, { x: n.x, y: n.y });
  }
  const edgePoints = new Map<string, Point[]>();
  for (const e of g.edges()) {
    if (e.name) edgePoints.set(e.name, g.edge(e).points ?? []);
  }
  const sizes = new Map(nodes.map((n) => [n.id, n]));
  for (const [i, edge] of selfEdges.entries()) {
    const pos = positions.get(edge.from)!;
    const half = (sizes.get(edge.from)!.width ?? 0) / 2;
    const [x, y, r] = [pos.x + half, pos.y, 18 + i * 10];
    edgePoints.set(edge.id, [
      { x, y: y - 10 },
      { x: x + r, y: y - r * 0.75 },
      { x: x + r, y: y + r * 0.75 },
      { x, y: y + 10 },
    ]);
  }
  const graph = g.graph() as { width?: number; height?: number };
  return {
    positions,
    edgePoints,
    width: Math.max(graph.width ?? 0, 1),
    height: Math.max(graph.height ?? 0, 1),
  };
}

/** Text-width heuristic for sizing boxes without measuring the DOM. */
export function textWidth(text: string, fontSize = 12): number {
  return Math.ceil(text.length * fontSize * 0.62);
}
