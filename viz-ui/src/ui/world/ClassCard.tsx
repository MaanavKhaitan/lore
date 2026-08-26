import { textWidth } from "../../layout/dagreLayout";
import { classVar } from "../../palette";
import type { ClassSpec, Field } from "../../types";

const HEADER = 30;
const LINE = 17;
const PAD = 10;

function fieldLine(field: Field): string {
  if (field.kind === "relation") {
    return `${field.name} → ${field.target}${field.many ? " (zero or more)" : ""}`;
  }
  const oneOf = field.oneOf ? ` — one of ${field.oneOf.join(", ")}` : "";
  const optional = field.required ? "" : "?";
  return `${field.name}${optional}: ${field.type}${oneOf}`;
}

/** Inherited fields collapse to one muted summary line — the declaring class
 * shows them in full, and repeating them on every subclass buries what the
 * subclass adds. */
function visibleLines(cls: ClassSpec): { text: string; inherited: boolean }[] {
  const own = cls.fields.filter((f) => !f.inheritedFrom);
  const inherited = cls.fields.filter((f) => f.inheritedFrom);
  const lines = [{ text: "id", inherited: true }, ...own.map((f) => ({ text: fieldLine(f), inherited: false }))];
  if (inherited.length > 0) {
    const from = [...new Set(inherited.map((f) => f.inheritedFrom))].join(", ");
    lines.push({
      text: `+ ${inherited.length} field${inherited.length > 1 ? "s" : ""} from ${from}`,
      inherited: true,
    });
  }
  return lines;
}

export function cardSize(cls: ClassSpec): { width: number; height: number } {
  const lines = visibleLines(cls);
  const header = cls.parents.length ? `${cls.name} — a kind of ${cls.parents[0]}` : cls.name;
  const width =
    Math.max(textWidth(header, 13) + 16, ...lines.map((l) => textWidth(l.text, 11.5))) + 28;
  return { width, height: HEADER + lines.length * LINE + PAD };
}

interface ClassCardProps {
  cls: ClassSpec;
  x: number;
  y: number; // center
  highlighted: boolean;
  dimmed: boolean;
}

/** One entity class as an ERD-style card: header (+ "a kind of Parent"),
 * then id and every field — inherited ones marked with the class they come
 * from, enums spelled out, relations as arrows-in-text. */
export function ClassCard({ cls, x, y, highlighted, dimmed }: ClassCardProps) {
  const { width, height } = cardSize(cls);
  const color = `var(${classVar(cls.name)}, var(--muted))`;
  return (
    <g
      transform={`translate(${x - width / 2},${y - height / 2})`}
      className={["class-card", highlighted && "hl", dimmed && "dim"].filter(Boolean).join(" ")}
    >
      <rect width={width} height={height} rx="8" style={{ stroke: color }} />
      <rect width={width} height={HEADER - 4} rx="8" style={{ fill: color, opacity: 0.14, stroke: "none" }} />
      <text x={12} y={19} className="card-title">
        {cls.name}
        {cls.parents.length > 0 && <tspan className="card-parent"> — a kind of {cls.parents[0]}</tspan>}
      </text>
      {visibleLines(cls).map((line, i) => (
        <text
          key={line.text}
          x={12}
          y={HEADER + 13 + i * LINE}
          className={["card-field", line.inherited && "inherited"].filter(Boolean).join(" ")}
        >
          {line.text}
        </text>
      ))}
    </g>
  );
}
