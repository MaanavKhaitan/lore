import { useMemo, useState } from "react";
import { buildGraph } from "../../data/graphmodel";
import { classVar } from "../../palette";
import type { Fact, Spec } from "../../types";
import { GraphView } from "../graph/GraphView";
import { NodePanel } from "../graph/NodePanel";
import { Scrubber } from "./Scrubber";

interface StateTabProps {
  spec: Spec;
  facts: Fact[]; // step-stamped committed facts (trace replay or snapshot blob)
  maxStep: number;
  scrubbable: boolean; // false in snapshot-only mode
}

/** The State tab: everything the agent's world contains, as a graph. Nodes
 * are entities (colored by family, labeled with id + class), edges are their
 * relations, seeds are marked, and the scrubber replays the world growing
 * step by step. */
export function StateTab({ spec, facts, maxStep, scrubbable }: StateTabProps) {
  const [step, setStep] = useState(maxStep);
  const [selected, setSelected] = useState<string | null>(null);
  const model = useMemo(() => buildGraph(facts, spec), [facts, spec]);
  // The graph view filters by step itself (so layout stays stable while
  // scrubbing), but the node panel reads the model directly — give it one
  // truncated to the scrubbed step or it would reveal future attributes/links.
  const panelModel = useMemo(
    () => (scrubbable && step < maxStep ? buildGraph(facts, spec, { maxStep: step }) : model),
    [facts, spec, model, step, maxStep, scrubbable],
  );

  const families = useMemo(() => {
    const seen = new Map<string, string>(); // family root → var
    for (const cls of spec.classes) {
      const root = cls.parents.length ? cls.parents[cls.parents.length - 1] : cls.name;
      if (!seen.has(root)) seen.set(root, classVar(root));
    }
    return [...seen.entries()];
  }, [spec]);

  if (model.nodes.length === 0) {
    return <p className="empty">This world has no entities yet.</p>;
  }
  return (
    <div className="state">
      <div className="state-bar">
        <div className="legend">
          {families.map(([name, cssVar]) => (
            <span key={name} className="class-chip">
              <span className="swatch" style={{ background: `var(${cssVar}, var(--muted))` }} />
              {name}
            </span>
          ))}
        </div>
        {scrubbable && maxStep > 0 && <Scrubber step={step} maxStep={maxStep} onChange={setStep} />}
      </div>
      <div className="state-main">
        <GraphView
          model={model}
          spec={spec}
          selected={selected}
          onSelect={setSelected}
          collapse
          fill
          visibleStep={scrubbable ? step : Infinity}
        />
        {selected && <NodePanel id={selected} model={panelModel} spec={spec} onClose={() => setSelected(null)} />}
      </div>
    </div>
  );
}
