import { classVar } from "../../palette";
import type { RuleSpec, Spec } from "../../types";

interface RuleCardsProps {
  spec: Spec;
  selected: string | null;
  onSelect: (name: string | null) => void;
}

/** Rules and goals as plain-English cards. Rules block (or advise on) each
 * action as it happens; goals are only checked when the agent says it's
 * done — the UI keeps that distinction front and center. Clicking a card
 * highlights the entity classes it watches. */
export function RuleCards({ spec, selected, onSelect }: RuleCardsProps) {
  return (
    <div className="rule-cards">
      <section>
        <h3>Rules — checked on every action</h3>
        {spec.rules.length === 0 && <p className="empty">No custom rules declared.</p>}
        {spec.rules.map((rule) => (
          <RuleCard key={rule.name} rule={rule} selected={selected} onSelect={onSelect} />
        ))}
        {spec.disjointPairs.map(([a, b]) => (
          <article key={`${a}|${b}`} className="rule-card structural">
            <p>
              A thing can never be both <ClassChip name={a} /> and <ClassChip name={b} />.
            </p>
          </article>
        ))}
      </section>
      {spec.goals.length > 0 && (
        <section>
          <h3>Goals — checked when the agent finishes</h3>
          {spec.goals.map((goal) => (
            <RuleCard key={goal.name} rule={goal} selected={selected} onSelect={onSelect} goal />
          ))}
        </section>
      )}
    </div>
  );
}

function RuleCard({
  rule,
  selected,
  onSelect,
  goal = false,
}: {
  rule: RuleSpec;
  selected: string | null;
  onSelect: (name: string | null) => void;
  goal?: boolean;
}) {
  const active = selected === rule.name;
  return (
    <article
      className={["rule-card", active && "active"].filter(Boolean).join(" ")}
      onClick={() => onSelect(active ? null : rule.name)}
    >
      <header>
        <ClassChip name={rule.target} />
        <span className={`chip ${rule.severity === "flag" ? "chip-advisory" : goal ? "chip-goal" : "chip-blocks"}`}>
          {rule.severity === "flag" ? "advisory" : goal ? "at the end" : "blocks"}
        </span>
      </header>
      <p className="rule-plain">{rule.plain}</p>
      {rule.doc && <p className="rule-doc">{rule.doc.split("\n")[0]}</p>}
    </article>
  );
}

function ClassChip({ name }: { name: string }) {
  return (
    <span className="class-chip">
      <span className="swatch" style={{ background: `var(${classVar(name)}, var(--muted))` }} />
      {name}
    </span>
  );
}
