import { Badge, Button, Separator, useThemeContext } from "@metaphor-cloud/ui";
import { useCallback, useEffect, useState } from "react";

import { getQueue, getSystem, health, pauseQueue, readEvents, resumeQueue } from "./engine";
import { notify, setTray } from "./host";
import type { Queue, SetupProgress, System } from "./types";
import DashboardView from "./views/Dashboard";
import Findings from "./views/Findings";
import Models from "./views/Models";
import Repositories from "./views/Repositories";
import Runs from "./views/Runs";
import SettingsView from "./views/Settings";
import SystemView from "./views/System";

type Status =
  | { state: "starting" }
  | { state: "ready"; version: string }
  | { state: "failed"; reason: string };

const VIEWS = [
  "Overview",
  "Work",
  "Repositories",
  "Runs",
  "Models",
  "Settings",
  "System",
] as const;
type View = (typeof VIEWS)[number];

const LOUD = new Set(["critical", "high"]);

export default function App() {
  const [status, setStatus] = useState<Status>({ state: "starting" });
  const [view, setView] = useState<View>("Overview");
  const [scanning, setScanning] = useState(false);
  const [system, setSystem] = useState<System | null>(null);
  const [queue, setQueue] = useState<Queue | null>(null);
  const [version, setVersion] = useState(0);
  const [setup, setSetup] = useState<SetupProgress | null>(null);
  const { resolved, cycleTheme } = useThemeContext();

  const refreshQueue = useCallback(async () => {
    try {
      setQueue(await getQueue());
    } catch {
      // The queue is a detail. A failure here must not blank the window.
    }
  }, []);

  useEffect(() => {
    const abort = new AbortController();

    async function connect() {
      try {
        const info = await health();
        setStatus({ state: "ready", version: info.version });
        setSystem(await getSystem());
        await refreshQueue();
        await readEvents((event) => {
          if (event.kind === "scan.started") setScanning(true);
          if (event.kind === "scan.finished") setScanning(false);
          if (event.kind.startsWith("queue.")) void refreshQueue();
          if (event.kind === "setup.progress") setSetup(event.data as SetupProgress);
          if (event.kind === "setup.finished") setSetup(null);
          if (event.kind === "config.reloaded") setVersion((value) => value + 1);
          if (event.kind === "run.finished" || event.kind === "run.skipped") {
            void refreshQueue();
            setVersion((value) => value + 1);
          }
          if (event.kind === "finding.new") {
            const data = event.data as { severity: string; title: string; slug: string };
            setVersion((value) => value + 1);
            if (LOUD.has(data.severity)) {
              void notify(`${data.severity}: ${data.title}`, data.slug);
            }
          }
        }, abort.signal);
      } catch (cause) {
        if (abort.signal.aborted) return;
        setStatus({
          state: "failed",
          reason: cause instanceof Error ? cause.message : String(cause),
        });
      }
    }

    void connect();
    return () => abort.abort();
  }, [refreshQueue]);

  async function togglePause() {
    setQueue(queue?.paused ? await resumeQueue() : await pauseQueue());
  }

  return (
    <div className="flex h-full flex-col bg-bg text-text-primary">
      <header className="flex items-center gap-3 border-b border-border bg-bg-elevated px-4 py-2">
        <span className="text-sm font-semibold tracking-tight">reviewrig</span>
        <Separator orientation="vertical" className="h-4" />
        <nav className="flex flex-1 gap-1 overflow-x-auto">
          {VIEWS.map((name) => (
            <button
              key={name}
              onClick={() => setView(name)}
              className={
                name === view
                  ? "rounded-md bg-accent/15 px-2.5 py-1 text-xs font-medium text-accent"
                  : "rounded-md px-2.5 py-1 text-xs text-text-secondary transition-colors hover:bg-bg-card-hover hover:text-text-primary"
              }
            >
              {name}
            </button>
          ))}
        </nav>
        {queue && (
          <Button size="sm" variant="ghost" onClick={() => void togglePause()}>
            {queue.paused ? "Resume" : "Pause"}
            {queue.pending > 0 && (
              <Badge variant="outline" className="ml-1.5">
                {queue.pending}
              </Badge>
            )}
          </Button>
        )}
        <Button
          size="sm"
          variant="ghost"
          aria-label="Switch theme"
          onClick={cycleTheme}
        >
          {resolved === "dark" ? "☾" : "☀"}
        </Button>
        <span
          className={
            status.state === "failed"
              ? "text-xs text-danger"
              : "text-xs tabular-nums text-text-tertiary"
          }
        >
          {status.state === "starting" && "Starting"}
          {status.state === "ready" && `Engine ${status.version}`}
          {status.state === "failed" && status.reason}
        </span>
      </header>

      <main className="flex-1 overflow-auto px-5 py-5">
        {view === "Overview" && <DashboardView version={version} />}
        {view === "Work" && <Findings version={version} onCounts={setTray} />}
        {view === "Repositories" && <Repositories scanning={scanning} version={version} />}
        {view === "Runs" && <Runs version={version} />}
        {view === "Models" && <Models setup={setup} />}
        {view === "Settings" && <SettingsView version={version} />}
        {view === "System" && <SystemView system={system} />}
      </main>
    </div>
  );
}
