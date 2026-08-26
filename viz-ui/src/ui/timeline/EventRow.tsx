import type { Row } from "../../data/replay";
import type { Fact, Spec, Violation } from "../../types";
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

/** A violation's check, in words — collapsed rows name the reason, not the
 * whole rendered message (that's in the expanded repair prompt). */
function reason(violation: Violation): string {
  const check = violation.check;
  if (check.includes(":")) return check.slice(check.indexOf(":") + 1).replace(/_/g, " ");
  const names: Record<string, string> = {
    existence: "refers to something that doesn't exist",
    range: "points at the wrong kind of thing",
    domain: "wrong kind of source",
    max_per_target: "over the limit",
    single_value: "changes an already-committed value",
    one_of: "value not allowed",
    disjoint: "can't be both kinds",
    type_coherence: "conflicting kinds",
    irreflexive: "creates a cycle",
    asymmetric: "reverse link already exists",
  };
  return names[check] ?? check;
}

function reasons(violations: Violation[]): string {
  if (!violations.length) return "";
  const first = reason(violations[0]);
  return violations.length > 1 ? `${first}, +${violations.length - 1} more` : first;
}

interface Presentation {
  tone: "good" | "bad" | "warn" | "muted";
  title: string;
  detail: string;
}

function present(row: Row): Presentation {
  switch (row.kind) {
    case "seeded":
      return { tone: "muted", title: "World seeded", detail: entitySummary(row.facts) };
    case "restored":
      return { tone: "muted", title: "Session restored", detail: entitySummary(row.facts) };
    case "committed":
      if (row.step === null)
        return { tone: "muted", title: "Nothing new to commit", detail: "" };
      return {
        tone: row.verdict.violations.length ? "warn" : "good",
        title: `Committed — step ${row.step}`,
        detail:
          entitySummary(row.facts) +
          (row.verdict.violations.length ? ` · ${row.verdict.violations.length} advisory` : ""),
      };
    case "rejected":
      return {
        tone: "bad",
        title: "Rejected",
        detail: [entitySummary(row.facts), reasons(row.verdict.violations)]
          .filter(Boolean)
          .join(" — "),
      };
    case "withdrawn":
      return { tone: "muted", title: "Withdrawn", detail: entitySummary(row.facts) };
    case "pending":
      return { tone: "warn", title: "Proposed, never resolved", detail: entitySummary(row.facts) };
    case "check":
      return {
        tone: "muted",
        title: `Preflight — ${row.verdict.ok ? "would pass" : "would fail"}`,
        detail: entitySummary(row.facts),
      };
    case "check_goals":
      return row.verdict.ok
        ? { tone: "good", title: "Finish-line check — all goals met", detail: "" }
        : {
            tone: "bad",
            title: `Finish-line check — ${row.verdict.violations.length} goal(s) unmet`,
            detail: reasons(row.verdict.violations),
          };
    case "snapshot":
      return { tone: "muted", title: "Snapshot saved", detail: "" };
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
        <span className="row-dot" aria-hidden="true" />
        <span className="row-title">{p.title}</span>
        <span className="row-detail">{p.detail}</span>
        {expandable && <span className="row-caret">{expanded ? "▾" : "▸"}</span>}
      </button>
      {expanded && verdict && (
        <div className="row-body">
          {verdict.repairPrompt && (
            <div className="repair">
              <h4>Repair prompt</h4>
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
