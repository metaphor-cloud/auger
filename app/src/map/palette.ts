/** How the map says what a finding is, without words.
 *
 * Colour carries severity, because that is the question a person asks first. A short
 * tag carries the category, because a second colour scale would fight the first one.
 */

export type Severity = "critical" | "high" | "medium" | "low" | "info";

export const SEVERITY: Record<string, { colour: string; glow: string; label: string }> = {
  critical: { colour: "#f43f5e", glow: "244, 63, 94", label: "Critical" },
  high: { colour: "#fb923c", glow: "251, 146, 60", label: "High" },
  medium: { colour: "#facc15", glow: "250, 204, 21", label: "Medium" },
  low: { colour: "#38bdf8", glow: "56, 189, 248", label: "Low" },
  info: { colour: "#94a3b8", glow: "148, 163, 184", label: "Info" },
};

export const SEVERITY_RANK: Record<string, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
  info: 4,
};

export const CATEGORY: Record<string, { tag: string; label: string; colour: string }> = {
  security: { tag: "SEC", label: "Security", colour: "#f472b6" },
  correctness: { tag: "COR", label: "Correctness", colour: "#a78bfa" },
  performance: { tag: "PRF", label: "Performance", colour: "#22d3ee" },
  quality: { tag: "QLT", label: "Quality", colour: "#4ade80" },
  style: { tag: "STY", label: "Style", colour: "#cbd5e1" },
  task: { tag: "TSK", label: "Task", colour: "#fbbf24" },
};

export const STATES: Record<string, string> = {
  open: "Open",
  doing: "Doing",
  resolved: "Done",
  suppressed: "Dropped",
};

export function severityOf(name: string) {
  return SEVERITY[name] ?? SEVERITY.info;
}

export function categoryOf(name: string) {
  return CATEGORY[name] ?? CATEGORY.quality;
}
