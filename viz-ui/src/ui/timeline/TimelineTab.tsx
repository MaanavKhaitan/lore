import { useState } from "react";
import type { Replay } from "../../data/replay";
import type { Spec } from "../../types";
import { EventRow } from "./EventRow";

/** The Timeline tab — the flight recorder. One row per thing the agent did:
 * green committed, red rejected (expand for the repair prompt and the
 * violation drawn on the graph), plus preflights, finish-line checks, and
 * snapshots. */
export function TimelineTab({ spec, replay }: { spec: Spec; replay: Replay }) {
  const [expanded, setExpanded] = useState<number | null>(() => {
    const firstRejected = replay.rows.find((r) => r.kind === "rejected");
    return firstRejected ? firstRejected.index : null;
  });
  return (
    <div className="timeline">
      <ol>
        {replay.rows.map((row) => (
          <EventRow
            key={row.index}
            row={row}
            spec={spec}
            committedFacts={replay.facts}
            expanded={expanded === row.index}
            onToggle={() => setExpanded(expanded === row.index ? null : row.index)}
          />
        ))}
      </ol>
    </div>
  );
}
