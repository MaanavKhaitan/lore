import { useMemo, useState } from "react";
import { replay } from "../data/replay";
import { assignClassColors, classVar } from "../palette";
import type { Snapshot, Spec, Trace } from "../types";
import { StateTab } from "./state/StateTab";
import { Tabs } from "./Tabs";
import { TimelineTab } from "./timeline/TimelineTab";
import { WorldTab } from "./world/WorldTab";

interface AppProps {
  spec: Spec;
  trace: Trace | null;
  snapshot: Snapshot | null;
}

export function App({ spec, trace, snapshot }: AppProps) {
  // full: trace present (World + Timeline + State). snapshot: blob only
  // (World + State, no scrubber). world: schema only.
  const mode = trace ? "full" : snapshot ? "snapshot" : "world";
  // #world / #timeline / #state deep-links a tab (handy for sharing too).
  const fromHash = window.location.hash.slice(1);
  const [tab, setTab] = useState(
    ["world", "timeline", "state"].includes(fromHash) ? fromHash : "world",
  );

  const colors = useMemo(() => assignClassColors(spec), [spec]);
  const colorCss = useMemo(() => {
    const vars = (map: Map<string, string>) =>
      [...map.entries()].map(([cls, hex]) => `${classVar(cls)}:${hex};`).join("");
    return (
      `.viz-root{${vars(colors.light)}}` +
      `@media (prefers-color-scheme: dark){.viz-root{${vars(colors.dark)}}}`
    );
  }, [colors]);

  const world = useMemo(() => {
    if (trace) return replay(trace);
    if (snapshot) {
      const maxStep = Math.max(0, ...snapshot.facts.map((f) => f.step));
      return { rows: [], facts: snapshot.facts, maxStep };
    }
    return null;
  }, [trace, snapshot]);

  const staleData =
    (trace && trace.fingerprint !== spec.fingerprint) ||
    (snapshot && snapshot.fingerprint !== spec.fingerprint);

  const tabs = [
    { id: "world", label: "World" },
    ...(mode === "full" ? [{ id: "timeline", label: "Timeline" }] : []),
    ...(mode !== "world" ? [{ id: "state", label: "State" }] : []),
  ];

  return (
    <div className="viz-root">
      <style>{colorCss}</style>
      <header className="app-header">
        <h1>lore</h1>
      </header>
      {staleData && (
        <div className="banner-warn" role="alert">
          ⚠ This recording was made under a different version of the world declaration — what you
          see may not match the rules shown on the World tab.
        </div>
      )}
      <Tabs tabs={tabs} active={tab} onSelect={setTab} />
      {tab === "world" && <WorldTab spec={spec} />}
      {tab === "timeline" && world && trace && <TimelineTab spec={spec} replay={world} />}
      {tab === "state" && world && (
        <StateTab spec={spec} facts={world.facts} maxStep={world.maxStep} scrubbable={mode === "full"} />
      )}
    </div>
  );
}
