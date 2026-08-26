import { useMemo } from "react";
import { nodeOf } from "../../data/decode";
import { buildGraph } from "../../data/graphmodel";
import type { Fact, Spec, VerdictPayload } from "../../types";
import { GraphView } from "../graph/GraphView";
import { verdictHighlight } from "../graph/highlight";

interface ViolationDetailProps {
  spec: Spec;
  proposal: Fact[];
  verdict: VerdictPayload;
  committedFacts: Fact[];
  stepBefore: number;
  committed: boolean; // proposal landed (solid) vs stayed hypothetical (dashed)
}

/** The mini graph under an expanded row: the proposal (dashed) drawn against
 * the neighborhood of the committed world it hit, with the violating path in
 * red — provenance edges when the engine recorded them (cycles), subjects
 * otherwise. */
export function ViolationDetail({
  spec,
  proposal,
  verdict,
  committedFacts,
  stepBefore,
  committed,
}: ViolationDetailProps) {
  const model = useMemo(() => {
    // Involved ids: the proposal's own nodes (both edge endpoints — the
    // committed entity a proposed edge points at belongs in the picture)
    // plus everything the violations name.
    const involved = new Set<string>(proposal.map(nodeOf));
    for (const fact of proposal) {
      if (fact.kind === "edge") involved.add(fact.object);
    }
    for (const violation of verdict.violations) {
      violation.subjects.forEach((s) => involved.add(s));
      for (const fact of violation.provenance) {
        involved.add(nodeOf(fact));
        if (fact.kind === "edge") involved.add(fact.object);
      }
    }
    // One-hop neighborhood of the committed world at that moment.
    const visible = committedFacts.filter((f) => f.step <= stepBefore);
    const shown = new Set(involved);
    for (const fact of visible) {
      if (fact.kind === "edge" && (shown.has(fact.subject) || shown.has(fact.object))) {
        shown.add(fact.subject);
        shown.add(fact.object);
      }
    }
    const context = visible.filter((f) =>
      f.kind === "edge" ? shown.has(f.subject) && shown.has(f.object) : shown.has(nodeOf(f)),
    );
    return buildGraph(context, spec, { proposed: committed ? [] : proposal });
  }, [proposal, verdict, committedFacts, stepBefore, committed, spec]);

  const highlight = useMemo(
    () => verdictHighlight(verdict, model.edges),
    [verdict, model],
  );

  if (model.nodes.length === 0) return null;
  return (
    <div className="violation-detail">
      <h4>{committed ? "What was committed" : "What the proposal would have done"}</h4>
      <GraphView model={model} spec={spec} highlight={highlight} maxHeight={340} />
    </div>
  );
}
