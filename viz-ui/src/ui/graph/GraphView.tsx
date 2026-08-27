import { useMemo, useRef, useState } from "react";
import type { GraphModel } from "../../data/graphmodel";
import { collapseHubs } from "../../layout/collapse";
import { runLayout, textWidth } from "../../layout/dagreLayout";
import { useFitScale } from "../../layout/useFitScale";
import { classVar } from "../../palette";
import type { Spec } from "../../types";
import type { Highlight } from "./highlight";
import { emptyHighlight } from "./highlight";

const NODE_HEIGHT = 46;

interface GraphViewProps {
  model: GraphModel;
  spec: Spec;
  highlight?: Highlight;
  selected?: string | null;
  onSelect?: (id: string | null) => void;
  collapse?: boolean; // fold >8-edge fan-ins (State tab)
  maxHeight?: number;
  /** Scale a small graph up to fill the container width and viewport height
   * (main State-tab canvas); mini graphs stay at natural size. */
  fill?: boolean;
  /** Scrubber support: layout covers the whole model, but facts committed
   * after this step render invisible — nodes never move while scrubbing. */
  visibleStep?: number;
}

export function GraphView({
  model,
  spec,
  highlight = emptyHighlight,
  selected = null,
  onSelect,
  collapse = false,
  maxHeight,
  fill = false,
  visibleStep = Infinity,
}: GraphViewProps) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const scrollRef = useRef<HTMLDivElement>(null);

  const { shown, aggregates } = useMemo(() => {
    if (!collapse) return { shown: model, aggregates: new Map() };
    const result = collapseHubs(model, spec, expanded);
    return { shown: result.model, aggregates: result.aggregates };
  }, [model, spec, expanded, collapse]);

  const classIndex = useMemo(
    () => new Map(spec.classes.map((c, i) => [c.name, i])),
    [spec],
  );
  const predicateIndex = useMemo(
    () => new Map(spec.relations.map((r, i) => [r.predicate, i])),
    [spec],
  );

  const layout = useMemo(() => {
    const pad = (n: number | undefined) => String(n ?? 999).padStart(3, "0");
    const nodes = shown.nodes.map((n) => ({
      id: n.id,
      width: Math.max(textWidth(n.id, 12.5), textWidth(nodeSubtitle(n), 10)) + 36,
      height: NODE_HEIGHT,
      sort: `${pad(classIndex.get(n.cls))}|${n.id}`,
    }));
    const edges = shown.edges.map((e) => ({
      id: e.key,
      from: e.subject,
      to: e.object,
      sort: `${pad(predicateIndex.get(e.predicate))}|${e.subject}|${e.object}`,
    }));
    // Rank gap fits an edge label like "based_on (proposed)" between nodes.
    return runLayout(nodes, edges, { ranksep: 120, nodesep: 28 });
  }, [shown, classIndex, predicateIndex]);

  const dimming = highlight.nodes.size > 0 || highlight.edges.size > 0;
  const naturalWidth = layout.width + 90;
  const naturalHeight = layout.height;
  const scale = useFitScale(scrollRef, naturalWidth, naturalHeight, fill);

  return (
    <div
      ref={scrollRef}
      className={cls("graph-scroll", fill && "fill")}
      style={maxHeight ? { maxHeight } : undefined}
    >
      <svg
        className="graph"
        width={naturalWidth * scale}
        height={naturalHeight * scale}
        viewBox={`0 0 ${naturalWidth} ${naturalHeight}`}
        role="img"
        aria-label="entity graph"
        onClick={() => onSelect?.(null)}
      >
        <defs>
          <marker id="arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0,0.5 L7.5,4 L0,7.5" className="arrow-head" />
          </marker>
          <marker id="arrow-hl" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0,0.5 L7.5,4 L0,7.5" className="arrow-head arrow-head-hl" />
          </marker>
        </defs>
        {shown.edges.map((edge) => {
          const points = layout.edgePoints.get(edge.key);
          if (!points?.length || edge.step > visibleStep) return null;
          const lit = highlight.edges.has(edge.key);
          const mid = points[Math.floor(points.length / 2)];
          const d = points.map((p, i) => `${i ? "L" : "M"}${p.x},${p.y}`).join(" ");
          const label =
            edge.field +
            (lit ? (edge.proposed ? " (proposed)" : edge.step > 0 ? ` (step ${edge.step})` : " (seed)") : "");
          const loop = edge.subject === edge.object;
          return (
            <g key={edge.key} className={cls("edge", lit && "hl", edge.proposed && "proposed", dimming && !lit && "dim")}>
              <path d={d} markerEnd={lit ? "url(#arrow-hl)" : "url(#arrow)"} />
              <text
                x={loop ? mid.x + 8 : mid.x}
                y={mid.y - (loop ? -4 : 5)}
                textAnchor={loop ? "start" : "middle"}
                className="edge-label"
              >
                {label}
              </text>
            </g>
          );
        })}
        {shown.nodes.map((node) => {
          const pos = layout.positions.get(node.id);
          if (!pos || node.step > visibleStep) return null;
          const lit = highlight.nodes.has(node.id);
          const width = Math.max(textWidth(node.id, 12.5), textWidth(nodeSubtitle(node), 10)) + 36;
          const aggregate = aggregates.get(node.id);
          return (
            <g
              key={node.id}
              transform={`translate(${pos.x - width / 2},${pos.y - NODE_HEIGHT / 2})`}
              className={cls(
                "node",
                lit && "hl",
                node.proposed && "proposed",
                node.ghost && "ghost",
                selected === node.id && "selected",
                dimming && !lit && "dim",
              )}
              onClick={(e) => {
                e.stopPropagation();
                if (aggregate) setExpanded(new Set([...expanded, aggregate.key]));
                else onSelect?.(selected === node.id ? null : node.id);
              }}
            >
              <rect width={width} height={NODE_HEIGHT} rx="6" />
              {!node.ghost && (
                <circle
                  cx={15}
                  cy={15.5}
                  r={3.5}
                  style={{ fill: `var(${classVar(node.cls)}, var(--muted))` }}
                />
              )}
              <text x={node.ghost ? 14 : 25} y={19} className="node-id">
                {aggregate ? `+ ${aggregate.count} more` : node.id}
              </text>
              <text x={node.ghost ? 14 : 25} y={35} className="node-cls">
                {aggregate ? node.cls : nodeSubtitle(node)}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function nodeSubtitle(node: { cls: string; seed: boolean; ghost: boolean; step: number; proposed: boolean }): string {
  if (node.ghost) return "not in this world";
  // Seeds carry no suffix — provenance lives in the click panel.
  const marks = node.proposed ? " · proposed" : node.step > 0 ? ` · step ${node.step}` : "";
  return node.cls + marks;
}

function cls(...parts: (string | false | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}
