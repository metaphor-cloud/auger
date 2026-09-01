/** The words and the arithmetic behind the downloads panel.
 *
 * Tested here rather than in the component, because "how long is left" is the number
 * people trust least and the one most often wrong.
 */

import type { Download } from "../types";

/** Bytes, in the unit a person would use for that size. */
export function bytes(count: number): string {
  if (count <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const step = Math.min(units.length - 1, Math.floor(Math.log10(count) / 3));
  const value = count / 1000 ** step;
  // A decimal earns its place at gigabytes and above, where the whole number hides
  // hundreds of megabytes, and below ten of any unit. Elsewhere it is noise.
  const decimals = step >= 3 || (value < 10 && step > 0) ? 1 : 0;
  return `${value.toFixed(decimals)} ${units[step]}`;
}

export function fraction(job: Download): number {
  if (job.total_bytes <= 0) return 0;
  return Math.min(1, Math.max(0, job.received_bytes / job.total_bytes));
}

/** Bytes per second since this job last started or continued, or 0 when it cannot be
 *  said yet. A rate averaged over a paused hour would be a lie. */
export function rate(job: Download, now: number): number {
  if (job.state !== "running" || job.moving_since <= 0) return 0;
  const seconds = now - job.moving_since;
  if (seconds < 1 || job.moved <= 0) return 0;
  return job.moved / seconds;
}

/** How long is left, in words, or "" when there is nothing honest to say.
 *
 * A rate needs a second of movement before it means anything, and a total of zero is a
 * server that sent no length. Silence beats a made-up number in both cases.
 */
export function remaining(job: Download, now: number): string {
  const speed = rate(job, now);
  if (speed <= 0 || job.total_bytes <= 0) return "";
  const left = job.total_bytes - job.received_bytes;
  if (left <= 0) return "";
  const seconds = Math.round(left / speed);
  if (seconds < 90) return `${seconds}s left`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 90) return `${minutes}m left`;
  const hours = seconds / 3600;
  return hours < 48 ? `${hours.toFixed(1)}h left` : `${Math.round(hours / 24)}d left`;
}

/** The one line under a job's name: where it is up to, and how fast. */
export function line(job: Download, now: number): string {
  if (job.state === "failed") return job.error || "failed";
  if (job.state === "cancelled") return "cancelled";
  if (job.state === "done") return `${bytes(job.total_bytes)} in ${job.files} files`;
  const parts = [`${bytes(job.received_bytes)} of ${bytes(job.total_bytes)}`];
  if (job.files > 1) parts.push(`file ${Math.min(job.files_done + 1, job.files)} of ${job.files}`);
  if (job.state === "paused") return `${parts.join(" · ")} · paused`;
  if (job.state === "queued") return `${parts.join(" · ")} · waiting its turn`;
  const speed = rate(job, now);
  if (speed > 0) parts.push(`${bytes(speed)}/s`);
  const left = remaining(job, now);
  if (left) parts.push(left);
  return parts.join(" · ");
}

/** Which control a job in this state offers, and what it is called. */
export function controls(job: Download): ("pause" | "resume" | "cancel" | "forget")[] {
  if (job.state === "running" || job.state === "queued") return ["pause", "cancel"];
  if (job.state === "paused") return ["resume", "cancel"];
  return ["forget"];
}

export const CONTROL_LABEL: Record<string, string> = {
  pause: "Pause",
  resume: "Continue",
  cancel: "Drop",
  forget: "Clear",
};

export const CONTROL_TITLE: Record<string, string> = {
  pause: "Stop fetching and keep every byte already written.",
  resume: "Carry on from where it stopped.",
  cancel: "Throw away what has been fetched so far.",
  forget: "Take this off the list. Nothing on disk changes.",
};
