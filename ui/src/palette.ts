import type { Spec } from "./types";

// Categorical slots from the validated reference palette (fixed order, never
// cycled). Class color follows the entity *family*: a class inherits its
// root-most registered ancestor's slot, so GoverningLawClause reads as a
// Clause. Every node also carries its class name as text — identity is never
// color-alone — and families past the 8 slots fold to neutral.
const LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"];
const DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"];
const NEUTRAL = { light: "#898781", dark: "#898781" };

export interface ClassColors {
  /** class name → CSS color, per mode; exposed as CSS vars in App. */
  light: Map<string, string>;
  dark: Map<string, string>;
  /** root family per class, for the legend (one entry per family). */
  families: Map<string, string>;
}

export function assignClassColors(spec: Spec): ClassColors {
  const rootOf = new Map<string, string>();
  for (const cls of spec.classes) {
    rootOf.set(cls.name, cls.parents.length ? cls.parents[cls.parents.length - 1] : cls.name);
  }
  const slotOf = new Map<string, number>();
  for (const cls of spec.classes) {
    const root = rootOf.get(cls.name)!;
    if (!slotOf.has(root)) slotOf.set(root, slotOf.size);
  }
  const light = new Map<string, string>();
  const dark = new Map<string, string>();
  for (const cls of spec.classes) {
    const slot = slotOf.get(rootOf.get(cls.name)!)!;
    light.set(cls.name, slot < LIGHT.length ? LIGHT[slot] : NEUTRAL.light);
    dark.set(cls.name, slot < DARK.length ? DARK[slot] : NEUTRAL.dark);
  }
  return { light, dark, families: rootOf };
}

/** CSS-var name for a class's series color (defined by App per mode). */
export function classVar(cls: string): string {
  return `--series-${cls.replace(/[^a-zA-Z0-9_-]/g, "_")}`;
}
