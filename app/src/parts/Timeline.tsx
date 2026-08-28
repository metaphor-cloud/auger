/** What the rig has been doing, over time.
 *
 * This is the one picture in the window, because time is the one axis where the data
 * varies. A column per slice of time, stacked by outcome, and a finding marked below at
 * the moment it first appeared.
 */

import { useMemo, useState } from "react";

import { severityOf } from "../palette";
import { gather, peak, scale, type FindingMark, type Mark } from "./buckets";

/** What the strip can show. The first is the default: a person watching their own
    machine wants the last few minutes, not the last few hours. */
const WINDOWS = [
  { hours: 0.25, label: "15m" },
  { hours: 0.5, label: "30m" },
  { hours: 2, label: "2h" },
  { hours: 12, label: "12h" },
  { hours: 72, label: "3d" },
];

const HEIGHT = 74;
const BARS = 52;
const MARKS = 12;

const COLOUR: Record<string, string> = {
  ok: "#34c98a",
  failed: "#f0616d",
  skipped: "#3f5468",
  running: "#4c9df0",
};

function clock(stamp: number) {
  return new Date(stamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export default function Timeline({
  runs,
  findings,
  now,
  hours,
  onHours,
  onOpenRuns,
}: {
  runs: Mark[];
  findings: FindingMark[];
  /** When the data was read. The window ends there, so the columns hold still
      between reads instead of creeping sideways on every render. */
  now: number;
  hours: number;
  onHours: (hours: number) => void;
  onOpenRuns: () => void;
}) {
  const [over, setOver] = useState<number | null>(null);
  const window = useMemo(() => gather(runs, findings, now, hours), [runs, findings, now, hours]);
  const tallest = peak(window);
  const span = window.to - window.from;
  const failing = window.total ? Math.round((window.failed / window.total) * 100) : 0;

  const slices = window.slices;
  const width = 100 / slices.length;
  const shown = over === null ? null : slices[over];

  return (
    <section className="border-b border-border bg-bg-elevated px-4 pb-2 pt-3">
      <div className="mb-1.5 flex items-center gap-3 text-[11px]">
        <span className="uppercase tracking-wider text-text-tertiary">Runs</span>
        {window.total === 0 ? (
          <span className="text-text-tertiary">nothing in this window</span>
        ) : (
          <span className="flex items-center gap-2.5 text-text-secondary">
            <span>{window.total} runs</span>
            <span style={{ color: COLOUR.ok }}>{window.ok} ok</span>
            <span style={{ color: COLOUR.skipped }}>{window.skipped} skipped</span>
            <span style={{ color: COLOUR.failed }}>
              {window.failed} failed
              {failing > 0 ? ` · ${failing}%` : ""}
            </span>
          </span>
        )}
        <span className="ml-auto flex items-center gap-1">
          {WINDOWS.map((one) => (
            <button
              key={one.hours}
              onClick={() => onHours(one.hours)}
              className={
                one.hours === hours
                  ? "rounded px-1.5 py-0.5 text-accent"
                  : "rounded px-1.5 py-0.5 text-text-tertiary transition-colors hover:text-text-secondary"
              }
            >
              {one.label}
            </button>
          ))}
          <button
            onClick={onOpenRuns}
            className="ml-1 rounded px-1.5 py-0.5 text-text-tertiary transition-colors hover:text-text-secondary"
          >
            All runs
          </button>
        </span>
      </div>

      <div className="relative" style={{ height: HEIGHT }} onMouseLeave={() => setOver(null)}>
        <svg width="100%" height={HEIGHT} preserveAspectRatio="none" viewBox="0 0 100 74">
          {/* The columns. Failures sit at the top of the stack, where the eye lands. */}
          {slices.map((slice, index) => {
            const x = index * width;
            // The whole column is scaled by its own total, and each part keeps its
            // share of that height, so the stack still reads as proportions.
            const column = scale(slice.total, tallest) * BARS;
            let y = BARS;
            return (
              <g key={slice.start}>
                {(["skipped", "ok", "running", "failed"] as const).map((status) => {
                  const height =
                    slice[status] > 0
                      ? Math.max(1.2, (slice[status] / slice.total) * column)
                      : 0;
                  if (height <= 0) return null;
                  y -= height;
                  return (
                    <rect
                      key={status}
                      x={x + width * 0.12}
                      y={y}
                      width={width * 0.76}
                      height={height}
                      fill={COLOUR[status]}
                      opacity={over === null || over === index ? 0.95 : 0.45}
                    />
                  );
                })}
                <rect
                  x={x}
                  y={0}
                  width={width}
                  height={74}
                  fill="transparent"
                  onMouseEnter={() => setOver(index)}
                />
              </g>
            );
          })}

          <line x1={0} y1={BARS} x2={100} y2={BARS} stroke="var(--color-border)" strokeWidth={0.3} />

          {/* Every finding, at the moment it first appeared. */}
          {window.findings.map((finding, index) => (
            <rect
              key={`${finding.at}-${index}`}
              x={((finding.at - window.from) / span) * 100 - 0.16}
              y={BARS + 5}
              width={0.32}
              height={MARKS}
              rx={0.16}
              fill={severityOf(finding.severity).colour}
              opacity={0.9}
            />
          ))}
        </svg>

        <div className="pointer-events-none absolute inset-x-0 bottom-0 flex justify-between text-[10px] text-text-tertiary">
          <span>{clock(window.from)}</span>
          {shown ? (
            <span className="text-text-secondary">
              {clock(shown.start)} · {shown.total} run{shown.total === 1 ? "" : "s"}
              {shown.failed ? ` · ${shown.failed} failed` : ""}
              {shown.skipped ? ` · ${shown.skipped} skipped` : ""}
            </span>
          ) : (
            <span>{window.findings.length} findings appeared</span>
          )}
          <span>now</span>
        </div>
      </div>
    </section>
  );
}
