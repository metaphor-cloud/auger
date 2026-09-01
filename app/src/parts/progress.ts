/** The words and the arithmetic behind the live bar.
 *
 * A run that is working and a run that has hung look the same in a window that shows
 * neither. This turns one step of a run into a line somebody can read at a glance: what
 * it is doing, how long it has been doing it, and how fast the answer is arriving.
 *
 * It is plain because it is tested. The component draws what this returns.
 */

import type { Activity, Step, Waiting } from "../types";

/** What each phase is called, in the words a person would use.
 *
 * The engine reports keys. An unknown key is shown as it came, because a phase the
 * window has no word for is still better than no phase at all.
 */
export const PHASE: Record<string, string> = {
  starting: "starting",
  diff: "reading the change",
  index: "indexing files",
  embed: "embedding code",
  retrieve: "gathering related code",
  rerank: "ranking context",
  scan: "running the scanner",
  outline: "reading the shape of it",
  reading: "reading files",
  asking: "asking the model",
  tool: "running a tool",
  parsing: "reading the answer",
  repairing: "asking for a readable answer",
  verifying: "second opinion",
  saving: "writing findings down",
  posting: "posting the review",
  done: "finished",
};

/** What each job is called. */
export const KIND: Record<string, string> = {
  diff_review: "review",
  audit: "audit",
  pr_review: "pull request",
  verify: "verify",
};

/** Whether the model is still reading the prompt rather than writing an answer.
 *
 * Both happen inside one phase, and on a large prompt the reading is the longer half:
 * a count that is rising while no token has been written yet is the difference between
 * a slow model and a stuck one.
 */
export function reading(step: Step): boolean {
  return step.phase === "asking" && step.tokens === 0 && step.total > 0;
}

export function phaseOf(step: Step): string {
  if (reading(step)) return "reading the prompt";
  return PHASE[step.phase] ?? step.phase;
}

export function kindOf(step: Step): string {
  return KIND[step.kind] ?? step.kind;
}

/** How long since a moment, in the shortest form that is still true.
 *
 * Seconds up to a minute, because that is the range where a person is deciding whether
 * to worry. Both arguments are seconds since the epoch.
 */
export function since(at: number, now: number): string {
  const seconds = Math.max(0, Math.floor(now - at));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${String(seconds % 60).padStart(2, "0")}s`;
  return `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, "0")}m`;
}

/** How far through a countable phase, from 0 to 1, or null when it cannot be counted. */
export function fraction(step: Step): number | null {
  if (step.total <= 0) return null;
  return Math.min(1, Math.max(0, step.done / step.total));
}

/** How fast the answer is arriving, or "" before there is enough to say.
 *
 * A rate is what says the model is working rather than stuck: a count that is large but
 * still says nothing about whether it moved since the last look.
 */
export function rate(step: Step, now: number): string {
  if (step.tokens <= 0 || step.tokens_started <= 0) return "";
  const seconds = now - step.tokens_started;
  if (seconds < 1) return "";
  return `${(step.tokens / seconds).toFixed(1)}/s`;
}

/** The count beside a phase, when there is one worth showing. */
export function counted(step: Step): string {
  if (step.tokens > 0) return `${step.tokens} tokens`;
  if (reading(step)) return `${step.done}/${step.total} tokens read`;
  if (step.total > 0) return `${step.done}/${step.total}`;
  return "";
}

/** One step as a single line: what, where, how long. */
export function line(step: Step, now: number): string {
  const parts = [phaseOf(step), since(step.phase_started, now)];
  const count = counted(step);
  if (count) parts.splice(1, 0, count);
  if (step.detail) parts.splice(1, 0, step.detail);
  return parts.join(" · ");
}

/** Why a task is being held back, in the words a person would use. */
export const HELD: Record<string, string> = {
  agent_running: "an agent is working in them",
  busy: "they are being worked in",
  machine_in_use: "somebody is at the keyboard",
  model_down: "no model is answering",
  in_flight: "another review of them is running",
};

export function heldFor(reason: string): string {
  return HELD[reason] ?? reason.replace(/_/g, " ");
}

/** The reason the most tasks are waiting for, and how many. */
export function commonest(waiting: Waiting[]): { reason: string; count: number } | null {
  if (waiting.length === 0) return null;
  const counts = new Map<string, number>();
  for (const one of waiting) counts.set(one.reason, (counts.get(one.reason) ?? 0) + 1);
  const [reason, count] = [...counts.entries()].sort((first, second) => second[1] - first[1])[0];
  return { reason, count };
}

/** What the bar says when nothing is in flight.
 *
 * Stopped is a state the user chose, so it is said plainly rather than dressed up as
 * activity. The case that reads as a hang is a queue that is not moving, and it nearly
 * always is not moving for a reason the engine already knows: a coding agent is in the
 * repository, or somebody is at the keyboard. Saying "waiting for a free worker" when
 * every worker is free is the sentence that makes a working rig look broken.
 */
export function resting(activity: Activity | null, now: number): string {
  if (activity === null) return "Connecting";
  if (!activity.ready) return "Starting up";
  if (activity.paused) {
    return activity.pending > 0 ? `Stopped · ${activity.pending} waiting` : "Stopped";
  }
  const waiting = activity.waiting ?? [];
  const held = commonest(waiting);
  if (held !== null) {
    const soonest = Math.min(...waiting.map((one) => one.until));
    const again = soonest > now ? `, retry in ${since(now, soonest)}` : "";
    const rest = activity.pending - waiting.length;
    const others = rest > 0 ? ` · ${rest} queued` : "";
    return `${held.count} held back: ${heldFor(held.reason)}${again}${others}`;
  }
  if (activity.pending > 0) return `${activity.pending} waiting for a free worker`;
  return `Watching · nothing to review${lastly(activity, now)}`;
}

/** What finished last, so a quiet rig still says when it last did something. */
export function lastly(activity: Activity | null, now: number): string {
  const run = activity?.last;
  if (!run || !run.finished_at) return "";
  const at = Date.parse(/(?:Z|[+-]\d\d:\d\d)$/.test(run.finished_at) ? run.finished_at : `${run.finished_at}Z`);
  if (Number.isNaN(at)) return "";
  const found =
    run.status === "ok"
      ? run.finding_count === 0
        ? "nothing found"
        : `${run.finding_count} found`
      : run.status;
  return ` · last ${since(at / 1000, now)} ago, ${found}`;
}
