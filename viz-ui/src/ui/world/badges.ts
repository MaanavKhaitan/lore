import type { RelationSpec } from "../../types";

/** Relation characteristics → plain-English badges. No formal vocabulary:
 * "chains" not transitive, "no cycles" not irreflexive, "two-way" not
 * symmetric/inverse. Order matters — strongest guarantees first. */
export function relationBadges(rel: RelationSpec): string[] {
  const badges: string[] = [];
  if (rel.maxPerTarget !== null) {
    badges.push(rel.maxPerTarget === 1 ? "at most 1 per target" : `at most ${rel.maxPerTarget} per target`);
  }
  if (rel.many) badges.push("zero or more");
  if (rel.inverse) badges.push(`two-way with ${rel.inverse.split(".")[1]}`);
  if (rel.symmetric) badges.push("two-way");
  if (rel.transitive) badges.push("chains");
  if (rel.asymmetric) {
    badges.push("one-way only"); // implies no self-links; don't also say it
  } else if (rel.irreflexive) {
    badges.push(rel.transitive ? "no cycles" : "no self-links");
  }
  if (rel.severity === "flag") badges.push("advisory");
  return badges;
}
