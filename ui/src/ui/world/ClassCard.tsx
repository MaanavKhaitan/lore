import { textWidth } from "../../layout/dagreLayout";
import { classVar } from "../../palette";
import type { ClassSpec, Field } from "../../types";

const HEADER = 30;
const LINE = 17;
const PAD = 10;

/** predicate → " · at most 1 per target · no cycles" (from WorldTab). Badges
 * live on the field line, not the diagram edges — edges stay label-light. */
export type BadgeMap = Map<string, string>;

function fieldLine(field: Field, badges: BadgeMap): string {
  if (field.kind === "relation") {
    const suffix = badges.get(field.predicate);
    return (
      `${field.name} → ${field.target}` +
      (field.many ? " (zero or more)" : "") +
      (suffix ? ` · ${suffix}` : "")
    );
  }
  const oneOf = field.oneOf ? ` — one of ${field.oneOf.join(", ")}` : "";
  const optional = field.required ? "" : "?";
  return `${field.name}${optional}: ${field.type}${oneOf}`;
}

/** Inherited fields collapse to one muted summary line — the declaring class
 * shows them in full, and repeating them on every subclass buries what the
 * subclass adds. */
function visibleLines(cls: ClassSpec, badges: BadgeMap): { text: string; inherited: boolean }[] {
  const own = cls.fields.filter((f) => !f.inheritedFrom);
  const inherited = cls.fields.filter((f) => f.inheritedFrom);
  const lines = [
    { text: "id", inherited: true },
    ...own.map((f) => ({ text: fieldLine(f, badges), inherited: false })),
  ];
  if (inherited.length > 0) {
    const from = [...new Set(inherited.map((f) => f.inheritedFrom))].join(", ");
    lines.push({
      text: `+ ${inherited.length} field${inherited.length > 1 ? "s" : ""} from ${from}`,
      inherited: true,
    });
  }
  return lines;
}

export function cardSize(cls: ClassSpec, badges: BadgeMap): { width: number; height: number } {
  const lines = visibleLines(cls, badges);
  const header = cls.parents.length ? `${cls.name} — a kind of ${cls.parents[0]}` : cls.name;
  const width =
    Math.max(textWidth(header, 13) + 14, ...lines.map((l) => textWidth(l.text, 11.5))) + 40;
  return { width, height: HEADER + lines.length * LINE + PAD };
}

interface ClassCardProps {
  cls: ClassSpec;
  badges: BadgeMap;
  x: number;
  y: number; // center
  highlighted: boolean;
  dimmed: boolean;
}

/** One entity class as an ERD-style card: a class-colored dot, the header
 * (+ "a kind of Parent"), then id and every own field — enums spelled out,
 * relations as arrows-in-text with their plain-English constraints. */
export function ClassCard({ cls, badges, x, y, highlighted, dimmed }: ClassCardProps) {
  const { width, height } = cardSize(cls, badges);
  return (
    <g
      transform={`translate(${x - width / 2},${y - height / 2})`}
      className={["class-card", highlighted && "hl", dimmed && "dim"].filter(Boolean).join(" ")}
    >
      <rect width={width} height={height} rx="8" />
      <circle cx={16} cy={15} r={3.5} style={{ fill: `var(${classVar(cls.name)}, var(--muted))` }} />
      <text x={27} y={19} className="card-title">
        {cls.name}
        {cls.parents.length > 0 && <tspan className="card-parent"> — a kind of {cls.parents[0]}</tspan>}
      </text>
      {visibleLines(cls, badges).map((line, i) => (
        <text
          key={line.text}
          x={16}
          y={HEADER + 13 + i * LINE}
          className={["card-field", line.inherited && "inherited"].filter(Boolean).join(" ")}
        >
          {line.text}
        </text>
      ))}
    </g>
  );
}
