import type { Row } from "../../data/replay";
import type { Fact, Spec } from "../../types";
import { ViolationDetail } from "./ViolationDetail";

interface EventRowProps {
  row: Row;
  spec: Spec;
  committedFacts: Fact[]; // final stamped facts; filtered by row.stepBefore
  expanded: boolean;
  onToggle: () => void;
}

/** Which entities does a fact list talk about? "Refund ref_2, Customer ada". */
function entitySummary(facts: Fact[]): string {
  const names = facts
    .filter((f) => f.kind === "type")
    .map((f) => (f.kind === "type" ? `${f.class} ${f.node}` : ""));
  const unique = [...new Set(names)];
  const shown = unique.slice(0, 3).join(", ");
  return unique.length > 3 ? `${shown}, +${unique.length - 3} more` : shown;
}

interface Presentation {
  tone: "good" | "bad" | "warn" | "muted";
  icon: string;
  title: string;
  detail: string;
}

function present(row: Row): Presentation {
  switch (row.kind) {
    case "seeded":
      return { tone: "muted", icon: "◆", title: "World seeded", detail: entitySummary(row.facts) };
    case "restored":
      return { tone: "muted", icon: "▲", title: "Session restored from a snapshot", detail: entitySummary(row.facts) };
    case "committed":
      if (row.step === null) return { tone: "muted", icon: "·", title: "Nothing new to commit", detail: "the proposal repeated already-known facts" };
      return {
        tone: row.verdict.violations.length ? "warn" : "good",
        icon: "✓",
        title: `Committed — step ${row.step}`,
        detail:
          entitySummary(row.facts) +
          (row.verdict.violations.length ? ` — ⚑ ${row.verdict.violations.length} advisory` : ""),
      };
    case "rejected": {
      const message = row.verdict.violations[0]?.message ?? "";
      const snippet = message.length > 90 ? `${message.slice(0, 90)}…` : message;
      return {
        tone: "bad",
        icon: "✕",
        title: "Rejected",
        detail: [entitySummary(row.facts), snippet].filter(Boolean).join(" — "),
      };
    }
    case "withdrawn":
      return { tone: "muted", icon: "↩", title: "Withdrawn", detail: `${entitySummary(row.facts)} — valid, but rolled back` };
    case "pending":
      return { tone: "warn", icon: "…", title: "Proposed, never resolved", detail: entitySummary(row.facts) };
    case "check":
      return {
        tone: "muted",
        icon: "?",
        title: `Preflight check — ${row.verdict.ok ? "would pass" : "would fail"}`,
        detail: entitySummary(row.facts),
      };
    case "check_goals":
      return row.verdict.ok
        ? { tone: "good", icon: "◎", title: "Finish-line check — all goals met", detail: "" }
        : {
            tone: "bad",
            icon: "◎",
            title: `Finish-line check — ${row.verdict.violations.length} goal(s) unmet`,
            detail: row.verdict.violations.map((v) => v.message).join(" "),
          };
    case "snapshot":
      return { tone: "muted", icon: "▽", title: "Snapshot saved", detail: "the committed world was persisted" };
  }
}

export function EventRow({ row, spec, committedFacts, expanded, onToggle }: EventRowProps) {
  const p = present(row);
  const verdict = "verdict" in row ? row.verdict : undefined;
  const facts = "facts" in row ? row.facts : [];
  const expandable = Boolean(verdict && (verdict.violations.length > 0 || facts.length > 0));
  return (
    <li className={`event-row tone-${p.tone}`}>
      <button
        type="button"
        className="row-head"
        onClick={expandable ? onToggle : undefined}
        aria-expanded={expanded}
        disabled={!expandable}
      >
        <span className="row-icon" aria-hidden="true">
          {p.icon}
        </span>
        <span className="row-title">{p.title}</span>
        <span className="row-detail">{p.detail}</span>
        {expandable && <span className="row-caret">{expanded ? "▾" : "▸"}</span>}
      </button>
      {expanded && verdict && (
        <div className="row-body">
          {verdict.repairPrompt && (
            <div className="repair">
              <h4>What the agent was told</h4>
              <pre>{verdict.repairPrompt}</pre>
            </div>
          )}
          {!verdict.repairPrompt && verdict.violations.length > 0 && (
            <div className="repair">
              <h4>Advisories</h4>
              <pre>{verdict.violations.map((v) => v.message).join("\n")}</pre>
            </div>
          )}
          {facts.length > 0 && (
            <ViolationDetail
              spec={spec}
              proposal={facts}
              verdict={verdict}
              committedFacts={committedFacts}
              stepBefore={
                row.kind === "committed" && row.step !== null ? row.step : row.stepBefore
              }
              committed={row.kind === "committed"}
            />
          )}
        </div>
      )}
    </li>
  );
}
