/** What is being fetched, and the controls on it.
 *
 * Weights are tens of gigabytes and sometimes hundreds, over dozens of files. A
 * transfer that size is a state rather than an event: it needs to be visible, and it
 * needs a stop button that does not throw the bytes away.
 */

import { Badge, Button, Progress } from "@metaphor-cloud/ui";
import { useCallback, useEffect, useState } from "react";

import { actOnDownload, getDownloads } from "../engine";
import type { Download } from "../types";
import { CONTROL_LABEL, CONTROL_TITLE, controls, fraction, line } from "./transfers";

/** How often the panel asks. Bytes move continuously and the engine publishes at a
 *  bounded rate; a second is the same order and keeps the arithmetic honest. */
const TICK = 1000;

const TONE: Record<string, "default" | "success" | "warning" | "danger" | "outline"> = {
  running: "default",
  queued: "outline",
  paused: "warning",
  done: "success",
  failed: "danger",
  cancelled: "outline",
};

function One({ job, onAct }: { job: Download; onAct: () => void }) {
  const [busy, setBusy] = useState("");
  const now = Date.now() / 1000;

  async function act(action: "pause" | "resume" | "cancel" | "forget") {
    setBusy(action);
    try {
      await actOnDownload(job.id, action);
      onAct();
    } finally {
      setBusy("");
    }
  }

  return (
    <li className="border-b border-border-subtle py-2 last:border-0">
      <div className="flex items-baseline gap-2">
        <span className="text-xs text-text-primary">{job.label}</span>
        <Badge variant={TONE[job.state] ?? "outline"}>{job.state}</Badge>
        {job.kind === "runtime" && (
          <span className="text-[10px] uppercase tracking-wider text-text-tertiary">engine</span>
        )}
        <span className="ml-auto flex gap-1">
          {controls(job).map((action) => (
            <Button
              key={action}
              size="sm"
              variant={action === "resume" ? "secondary" : "ghost"}
              className="h-6"
              title={CONTROL_TITLE[action]}
              disabled={busy !== ""}
              onClick={() => void act(action)}
            >
              {CONTROL_LABEL[action]}
            </Button>
          ))}
        </span>
      </div>
      {job.state !== "done" && job.state !== "cancelled" && job.state !== "failed" && (
        <Progress value={fraction(job) * 100} className="my-1.5 h-1" />
      )}
      <p
        className={
          job.state === "failed"
            ? "text-[11px] tabular-nums text-danger"
            : "text-[11px] tabular-nums text-text-secondary"
        }
      >
        {line(job, now)}
      </p>
      {job.current && job.state === "running" && (
        <p className="truncate font-mono text-[10px] text-text-tertiary">{job.current}</p>
      )}
    </li>
  );
}

export default function Downloads({ version = 0 }: { version?: number }) {
  const [jobs, setJobs] = useState<Download[] | null>(null);

  const load = useCallback(async () => {
    try {
      setJobs((await getDownloads()).downloads);
    } catch {
      // The panel is a detail. A failure here must not blank the view.
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, version]);

  // While anything moves, ask every second. Idle, the list only changes when the user
  // starts something, and this view is open for minutes at a time.
  const moving = (jobs ?? []).some((one) => one.state === "running" || one.state === "queued");
  useEffect(() => {
    const timer = setInterval(() => void load(), moving ? TICK : TICK * 10);
    return () => clearInterval(timer);
  }, [load, moving]);

  if (jobs === null) return <p className="text-xs text-text-secondary">Loading</p>;
  if (jobs.length === 0) {
    return <p className="text-xs text-text-tertiary">Nothing is being fetched.</p>;
  }

  return (
    <>
      <ul>
        {jobs.map((job) => (
          <One key={job.id} job={job} onAct={() => void load()} />
        ))}
      </ul>
      <p className="mt-2 text-[11px] text-text-tertiary">
        Pause keeps every byte already written and continues from there. Closing Auger
        keeps them too: the list is forgotten, the partial files are not, so asking for
        the same model again carries on where it stopped.
      </p>
    </>
  );
}
