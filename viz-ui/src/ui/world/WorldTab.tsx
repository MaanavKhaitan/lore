import { useMemo, useState } from "react";
import { runLayout } from "../../layout/dagreLayout";
import type { Spec } from "../../types";
import { relationBadges } from "./badges";
import { ClassCard, cardSize } from "./ClassCard";
import { RuleCards } from "./RuleCards";

/** The World tab: an ERD-style map of the declared world — one card per
 * entity class, solid arrows for relations (badged in plain English), dashed
 * "is a" links for inheritance and "never both" links for disjointness — with
 * the rules and goals as clickable plain-English cards alongside. */
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

  const layout = useMemo(() => {
    const pad = (n: number) => String(n).padStart(3, "0");
    const classIndex = new Map(spec.classes.map((c, i) => [c.name, i]));
    const nodes = spec.classes.map((cls, i) => ({
      id: cls.name,
      ...cardSize(cls),
      sort: pad(i),
    }));
    const edges = [
      ...spec.relations.map((rel, i) => ({
        id: `rel:${rel.predicate}`,
        from: rel.owner,
        to: rel.target,
        sort: `1|${pad(i)}`,
      })),
      ...spec.classes
        .filter((c) => c.parents.length > 0)
        .map((c) => ({
          id: `isa:${c.name}`,
          from: c.name,
          to: c.parents[0],
          sort: `2|${pad(classIndex.get(c.name) ?? 999)}`,
        })),
      ...spec.disjointPairs.map(([a, b], i) => ({
        id: `dis:${a}|${b}`,
        from: a,
        to: b,
        sort: `3|${pad(i)}`,
      })),
    ];
    return runLayout(nodes, edges, { rankdir: "LR", ranksep: 90, nodesep: 32 });
  }, [spec]);

  const relByPredicate = new Map(spec.relations.map((r) => [r.predicate, r]));
  const dimming = highlightedClasses.size > 0;

  return (
    <div className="world">
      <div className="graph-scroll world-diagram">
        {/* extra right margin: self-loop arcs and their labels sit past the last card */}
        <svg width={layout.width + 130} height={layout.height} role="img" aria-label="entity classes and relations">
          <defs>
            <marker id="w-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0,0.5 L7.5,4 L0,7.5" className="arrow-head" />
            </marker>
          </defs>
          {[...layout.edgePoints.entries()].map(([id, points]) => {
            if (!points.length) return null;
            const d = points.map((p, i) => `${i ? "L" : "M"}${p.x},${p.y}`).join(" ");
            const mid = points[Math.floor(points.length / 2)];
            const [kind, key] = [id.slice(0, id.indexOf(":")), id.slice(id.indexOf(":") + 1)];
            const rel = kind === "rel" ? relByPredicate.get(key) : undefined;
            const label = rel ? rel.field : kind === "isa" ? "is a" : "never both";
            const badges = rel ? relationBadges(rel).join(" · ") : "";
            return (
              <g key={id} className={["edge", "world-edge", kind !== "rel" && "meta", dimming && "dim"].filter(Boolean).join(" ")}>
                <path d={d} markerEnd={kind === "dis" ? undefined : "url(#w-arrow)"} />
                <text x={mid.x} y={mid.y - 6} textAnchor="middle" className="edge-label">
                  {label}
                </text>
                {badges && (
                  <text x={mid.x} y={mid.y + 8} textAnchor="middle" className="edge-badges">
                    {badges}
                  </text>
                )}
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
