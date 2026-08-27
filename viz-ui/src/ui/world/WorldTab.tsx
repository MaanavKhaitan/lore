import { useMemo, useRef, useState } from "react";
import { runLayout } from "../../layout/dagreLayout";
import { useFitScale } from "../../layout/useFitScale";
import type { Spec } from "../../types";
import { relationBadges } from "./badges";
import { ClassCard, cardSize } from "./ClassCard";
import { RuleCards } from "./RuleCards";

/** The World tab: an ERD-style map of the declared world — one card per
 * entity class, arrows for relations — with the rules and goals as clickable
 * plain-English cards alongside. Inheritance lives on the card header ("a
 * kind of Clause") and disjointness in the rules panel, not as extra edges:
 * one encoding each keeps the diagram quiet. */
export function WorldTab({ spec }: { spec: Spec }) {
  // A selected rule highlights its target class and every subclass of it.
  const [selectedRule, setSelectedRule] = useState<string | null>(null);
  const highlightedClasses = useMemo(() => {
    if (!selectedRule) return new Set<string>();
    const rule = [...spec.rules, ...spec.goals].find((r) => r.name === selectedRule);
    if (!rule) return new Set<string>();
    return new Set(
      spec.classes
        .filter((c) => c.name === rule.target || c.parents.includes(rule.target))
        .map((c) => c.name),
    );
  }, [selectedRule, spec]);

  // Relation constraints render on the owner card's field line, so edges
  // carry just the field name ("many" already shows as "(zero or more)").
  const badges = useMemo(
    () =>
      new Map(
        spec.relations.map((rel) => [
          rel.predicate,
          relationBadges(rel)
            .filter((b) => b !== "zero or more")
            .join(" · "),
        ]),
      ),
    [spec],
  );

  const layout = useMemo(() => {
    const pad = (n: number) => String(n).padStart(3, "0");
    const nodes = spec.classes.map((cls, i) => ({
      id: cls.name,
      ...cardSize(cls, badges),
      sort: pad(i),
    }));
    const edges = spec.relations.map((rel, i) => ({
      id: `rel:${rel.predicate}`,
      from: rel.owner,
      to: rel.target,
      sort: pad(i),
    }));
    return runLayout(nodes, edges, { rankdir: "LR", ranksep: 90, nodesep: 32 });
  }, [spec, badges]);

  const relByPredicate = new Map(spec.relations.map((r) => [r.predicate, r]));
  const dimming = highlightedClasses.size > 0;

  // extra right margin: self-loop arcs and their labels sit past the last card
  const naturalWidth = layout.width + 130;
  const naturalHeight = layout.height;
  const diagramRef = useRef<HTMLDivElement>(null);
  const scale = useFitScale(diagramRef, naturalWidth, naturalHeight, true);

  return (
    <div className="world">
      <div ref={diagramRef} className="graph-scroll world-diagram fill">
        <svg
          width={naturalWidth * scale}
          height={naturalHeight * scale}
          viewBox={`0 0 ${naturalWidth} ${naturalHeight}`}
          role="img"
          aria-label="entity classes and relations"
        >
          <defs>
            <marker id="w-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0,0.5 L7.5,4 L0,7.5" className="arrow-head" />
            </marker>
          </defs>
          {[...layout.edgePoints.entries()].map(([id, points]) => {
            if (!points.length) return null;
            const d = points.map((p, i) => `${i ? "L" : "M"}${p.x},${p.y}`).join(" ");
            const mid = points[Math.floor(points.length / 2)];
            const rel = relByPredicate.get(id.slice("rel:".length));
            // Self-loop labels sit to the right of the arc, not centered on it
            // (centered, they'd overlap the card itself).
            const loop = rel && rel.owner === rel.target;
            return (
              <g key={id} className={["edge", "world-edge", dimming && "dim"].filter(Boolean).join(" ")}>
                <path d={d} markerEnd="url(#w-arrow)" />
                <text
                  x={loop ? mid.x + 8 : mid.x}
                  y={mid.y - (loop ? -4 : 6)}
                  textAnchor={loop ? "start" : "middle"}
                  className="edge-label"
                >
                  {rel?.field ?? ""}
                </text>
              </g>
            );
          })}
          {spec.classes.map((cls) => {
            const pos = layout.positions.get(cls.name);
            if (!pos) return null;
            return (
              <ClassCard
                key={cls.name}
                cls={cls}
                badges={badges}
                x={pos.x}
                y={pos.y}
                highlighted={highlightedClasses.has(cls.name)}
                dimmed={dimming && !highlightedClasses.has(cls.name)}
              />
            );
          })}
        </svg>
      </div>
      <RuleCards spec={spec} selected={selectedRule} onSelect={setSelectedRule} />
    </div>
  );
}
