/** What Auger is doing this second.
 *
 * Every other surface in the window is history: findings that were found, runs that
 * finished, exchanges that completed. A rig that spends four minutes inside one model
 * call looked identical to a rig that had hung. This bar is the difference.
 *
 * It sits below every view rather than inside one, because the question it answers -
 * "is it working" - is not a question about the view you happen to be on.
 */

import { useEffect, useState } from "react";

import type { Activity, Step } from "../types";
import { fraction, kindOf, line, resting } from "./progress";

/** How often the elapsed times are redrawn. The engine publishes a phase when it
 *  changes; the seconds ticking up in between are counted here, so a slow step still
 *  visibly moves.
 *
 *  Idle, the bar only counts how long ago the last run was, so it redraws rarely: this
 *  window is open all day. */
const TICK = 1000;
const IDLE_TICK = 10_000;

const PHASE_COLOUR: Record<string, string> = {
  asking: "#4c9df0",
  verifying: "#a78bfa",
  embed: "#22d3ee",
  index: "#22d3ee",
  rerank: "#4ade80",
  tool: "#f2b544",
  scan: "#f2b544",
};

function colourOf(step: Step): string {
  return PHASE_COLOUR[step.phase] ?? "var(--color-text-tertiary)";
}

/** A local clock, in seconds since the epoch. Fast while something runs, slow otherwise. */
function useClock(running: boolean): number {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    setNow(Date.now() / 1000);
    const timer = setInterval(() => setNow(Date.now() / 1000), running ? TICK : IDLE_TICK);
    return () => clearInterval(timer);
  }, [running]);
  return now;
}

function Running({ step, now }: { step: Step; now: number }) {
  const share = fraction(step);
  const colour = colourOf(step);
  return (
    <div className="flex min-w-0 items-center gap-2">
      <span
        className="h-1.5 w-1.5 shrink-0 rounded-full rr-breathe"
        style={{ background: colour }}
      />
      <span className="shrink-0 text-[11px] text-text-primary">{step.slug || step.kind}</span>
      <span className="shrink-0 text-[10px] uppercase tracking-wider" style={{ color: colour }}>
        {kindOf(step)}
      </span>
      <span className="min-w-0 truncate text-[11px] tabular-nums text-text-secondary">
        {line(step, now)}
      </span>
      {share !== null && (
        <span className="h-1 w-16 shrink-0 overflow-hidden rounded-full bg-border-subtle">
          <span
            className="block h-full rounded-full transition-[width] duration-500"
            style={{ width: `${Math.round(share * 100)}%`, background: colour }}
          />
        </span>
      )}
    </div>
  );
}

export default function Now({ activity }: { activity: Activity | null }) {
  const steps = activity?.steps ?? [];
  const now = useClock(steps.length > 0);

  return (
    <footer className="flex shrink-0 items-center gap-3 border-t border-border bg-bg-elevated px-4 py-1.5">
      {steps.length === 0 ? (
        <span className="truncate text-[11px] text-text-tertiary">{resting(activity, now)}</span>
      ) : (
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          {steps.map((step) => (
            <Running key={step.id} step={step} now={now} />
          ))}
        </div>
      )}
      {steps.length > 0 && activity && activity.pending > 0 && (
        <span className="ml-auto shrink-0 text-[11px] tabular-nums text-text-tertiary">
          {activity.pending} waiting
        </span>
      )}
    </footer>
  );
}
