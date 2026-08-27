import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource/public-sans/latin-400.css";
import "@fontsource/public-sans/latin-500.css";
import "@fontsource/public-sans/latin-600.css";
import { decodeSnapshot, decodeSpec, decodeTrace } from "./data/decode";
import "./styles.css";
import type { Snapshot, Spec, Trace } from "./types";
import { FORMAT_SENTINEL } from "./types";
import { App } from "./ui/App";

/** Read an embedded payload; the literal __LORE_ marker means "not injected"
 * (the raw template, or a dev server without fixtures). */
function readBlock(id: string): unknown {
  const text = document.getElementById(id)?.textContent?.trim() ?? "";
  if (!text || text.startsWith("__LORE_")) return null;
  return JSON.parse(text); // an un-injected payload is the literal `null`
}

async function loadPayloads(): Promise<{ spec: Spec; trace: Trace | null; snapshot: Snapshot | null }> {
  const rawSpec = readBlock("lore-spec");
  if (rawSpec !== null) {
    const rawTrace = readBlock("lore-trace");
    const rawSnapshot = readBlock("lore-snapshot");
    return {
      spec: decodeSpec(rawSpec),
      trace: rawTrace === null ? null : decodeTrace(rawTrace),
      snapshot: rawSnapshot === null ? null : decodeSnapshot(rawSnapshot),
    };
  }
  if (import.meta.env.DEV) {
    // Dev fixtures: regenerate with
    //   python examples/contracts/demo.py --json ui/fixtures
    const [spec, trace, snapshot] = await Promise.all([
      import("../fixtures/spec.json"),
      import("../fixtures/trace.json").catch(() => ({ default: null })),
      import("../fixtures/snapshot.json").catch(() => ({ default: null })),
    ]);
    return {
      spec: decodeSpec(spec.default),
      trace: trace.default ? decodeTrace(trace.default) : null,
      snapshot: snapshot.default ? decodeSnapshot(snapshot.default) : null,
    };
  }
  throw new Error("no lore payload embedded in this page");
}

const rootEl = document.getElementById("root")!;
rootEl.dataset.loreFormat = FORMAT_SENTINEL;
const root = createRoot(rootEl);
loadPayloads()
  .then(({ spec, trace, snapshot }) => {
    root.render(
      <StrictMode>
        <App spec={spec} trace={trace} snapshot={snapshot} />
      </StrictMode>,
    );
  })
  .catch((err: unknown) => {
    root.render(<pre className="load-error">{String(err)}</pre>);
  });
