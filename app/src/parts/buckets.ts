/** Runs and findings, gathered into equal slices of time.
 *
 * A rig that reviews all day makes hundreds of runs. Drawn one by one they overlap into
 * a smear, so the strip counts them per slice and draws a column instead. The counting
 * is here, with no React and no colour, so the numbers can be checked on their own.
 */

export type Mark = { at: string; status: string };
export type FindingMark = { at: string; severity: string };

export type Slice = {
  /** Milliseconds since the epoch at the start of the slice. */
  start: number;
  ok: number;
  failed: number;
  skipped: number;
  running: number;
  total: number;
};

export type Window = {
  from: number;
  to: number;
  slices: Slice[];
  findings: { at: number; severity: string }[];
  ok: number;
  failed: number;
  skipped: number;
  running: number;
  total: number;
};

const HOUR = 3600_000;

function time(value: string): number {
  // The engine writes UTC without a zone on some rows, so an absent zone means UTC.
  const text = /(?:Z|[+-]\d\d:\d\d)$/.test(value) ? value : `${value}Z`;
  const stamp = Date.parse(text);
  return Number.isNaN(stamp) ? 0 : stamp;
}

export function gather(
  runs: Mark[],
  findings: FindingMark[],
  now: number,
  hours = 12,
  count = 90,
): Window {
  const to = now;
  const from = to - hours * HOUR;
  const width = (to - from) / count;
  const slices: Slice[] = Array.from({ length: count }, (_, index) => ({
    start: from + index * width,
    ok: 0,
    failed: 0,
    skipped: 0,
    running: 0,
    total: 0,
  }));

  const totals = { ok: 0, failed: 0, skipped: 0, running: 0, total: 0 };
  for (const run of runs) {
    const at = time(run.at);
    if (at < from || at > to) continue;
    const index = Math.min(count - 1, Math.max(0, Math.floor((at - from) / width)));
    const slice = slices[index];
    const status = run.status === "ok" || run.status === "failed" || run.status === "skipped"
      ? run.status
      : "running";
    slice[status] += 1;
    slice.total += 1;
    totals[status] += 1;
    totals.total += 1;
  }

  const marks = findings
    .map((finding) => ({ at: time(finding.at), severity: finding.severity }))
    .filter((finding) => finding.at >= from && finding.at <= to)
    .sort((first, second) => first.at - second.at);

  return { from, to, slices, findings: marks, ...totals };
}

/** The tallest column, which sets the scale. Never zero, so nothing divides by it. */
export function peak(window: Window): number {
  return Math.max(1, ...window.slices.map((slice) => slice.total));
}

/** How tall a count draws, as a fraction of the tallest column.
 *
 * The square root, not the count. One burst of forty runs would otherwise flatten every
 * other slice to a hairline, and a quiet hour with two runs in it is still a fact.
 */
export function scale(count: number, tallest: number): number {
  return count <= 0 ? 0 : Math.sqrt(count) / Math.sqrt(tallest);
}
