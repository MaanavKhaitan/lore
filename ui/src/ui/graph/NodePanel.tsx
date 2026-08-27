import type { GraphModel } from "../../data/graphmodel";
import { classVar } from "../../palette";
import type { Spec } from "../../types";

interface NodePanelProps {
  id: string;
  model: GraphModel;
  spec: Spec;
  onClose: () => void;
}

/** Detail card for a clicked node: class, provenance (seed / committed step),
 * attribute values, and its connections in both directions. */
export function NodePanel({ id, model, spec, onClose }: NodePanelProps) {
  const node = model.nodes.find((n) => n.id === id);
  if (!node) return null;
  const attrs = model.attrs.get(id) ?? [];
  const outgoing = model.edges.filter((e) => e.subject === id);
  const incoming = model.edges.filter((e) => e.object === id);
  const doc = spec.classes.find((c) => c.name === node.cls)?.doc;

  return (
    <aside className="node-panel">
      <header>
        <span className="swatch" style={{ background: `var(${classVar(node.cls)}, var(--muted))` }} />
        <strong>{id}</strong>
        <button type="button" className="close" onClick={onClose} aria-label="close">
          ×
        </button>
      </header>
      <div className="panel-sub">
        {node.cls}
        {node.ghost
          ? " — referenced but not in this world"
          : node.proposed
            ? " — proposed, not committed"
            : node.seed
              ? " — seeded (given, not created by the agent)"
              : node.step > 0
                ? ` — committed at step ${node.step}`
                : ""}
      </div>
      {doc && <p className="panel-doc">{doc}</p>}
      {attrs.length > 0 && (
        <dl>
          {attrs.map((a) => (
            <div key={`${a.attr}|${JSON.stringify(a.value)}`}>
              <dt>{a.attr.split(".")[1]}</dt>
              <dd>{a.repr ? String(a.value) : JSON.stringify(a.value)}</dd>
            </div>
          ))}
        </dl>
      )}
      {outgoing.length > 0 && (
        <section>
          <h4>links to</h4>
          {outgoing.map((e) => (
            <div key={e.key} className="panel-edge">
              {e.field} → <code>{e.object}</code>
            </div>
          ))}
        </section>
      )}
      {incoming.length > 0 && (
        <section>
          <h4>linked from</h4>
          {incoming.map((e) => (
            <div key={e.key} className="panel-edge">
              <code>{e.subject}</code> — {e.field}
            </div>
          ))}
        </section>
      )}
    </aside>
  );
}
