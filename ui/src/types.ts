// TS mirrors of the format-1 payloads produced by lore/viz (spec.py,
// record.py) and lore/session.py's snapshot codec.
export const SPEC_FORMAT = 1;
export const TRACE_FORMAT = 1;
// Stamped onto the DOM by main.tsx and asserted by the Python test suite
// against lore.viz.SPEC_FORMAT, so a format bump without an `npm run build`
// fails loudly. Keep it a plain string literal — minification keeps those.
export const FORMAT_SENTINEL = "lore-spec-format:1";

export type Severity = "reject" | "flag";
export type Source = "seed" | "asserted" | "derived";

// --- spec (World tab) ---------------------------------------------------------

export interface AttrField {
  kind: "attr";
  name: string;
  attr: string; // "Owner.field"
  type: string;
  oneOf: (string | number | boolean | null)[] | null;
  severity: Severity;
  required: boolean;
  default: string | null;
  inheritedFrom: string | null;
}

export interface RelationField {
  kind: "relation";
  name: string;
  predicate: string; // "Owner.field"
  target: string;
  many: boolean;
  required: boolean;
  inheritedFrom: string | null;
}

export type Field = AttrField | RelationField;

export interface ClassSpec {
  name: string;
  parents: string[]; // registered ancestors, nearest first
  doc: string | null;
  fields: Field[];
}

export interface RelationSpec {
  predicate: string;
  owner: string;
  field: string;
  target: string;
  many: boolean;
  maxPerTarget: number | null;
  severity: Severity;
  transitive: boolean;
  symmetric: boolean;
  asymmetric: boolean;
  irreflexive: boolean;
  inverse: string | null;
}

export interface RuleSpec {
  name: string;
  message: string;
  plain: string;
  doc: string | null;
  severity: Severity;
  target: string;
}

export interface Spec {
  format: number;
  kind: "lore-spec";
  lore: string;
  fingerprint: string;
  classes: ClassSpec[];
  relations: RelationSpec[];
  inversePairs: [string, string][];
  disjointPairs: [string, string][];
  rules: RuleSpec[];
  goals: RuleSpec[];
}

// --- facts (shared by trace events and snapshot blobs) --------------------------

export interface TypeFact {
  kind: "type";
  node: string;
  class: string;
  source: Source;
  step: number;
}

export interface EdgeFact {
  kind: "edge";
  subject: string;
  predicate: string;
  object: string;
  source: Source;
  step: number;
}

export interface AttrFact {
  kind: "attr";
  subject: string;
  attr: string;
  value: unknown;
  repr?: boolean; // true when the value is a repr() fallback, not round-trippable
  source: Source;
  step: number;
}

export type Fact = TypeFact | EdgeFact | AttrFact;

// --- trace (Timeline tab) --------------------------------------------------------

export interface Violation {
  check: string; // "max_per_target", "rule:<name>", "goal:<name>", ...
  severity: Severity;
  message: string;
  subjects: string[];
  provenance: Fact[];
}

export interface VerdictPayload {
  ok: boolean;
  repairPrompt: string;
  violations: Violation[];
}

export interface TraceEvent {
  type:
    | "session_start"
    | "propose"
    | "commit"
    | "rollback"
    | "check"
    | "check_goals"
    | "snapshot"
    | "restore";
  facts?: Fact[];
  step?: number | null;
  verdict?: VerdictPayload;
}

export interface Trace {
  format: number;
  kind: "lore-trace";
  lore: string;
  fingerprint: string;
  events: TraceEvent[];
}

export interface Snapshot {
  format: number;
  lore: string;
  fingerprint: string;
  facts: Fact[];
}
