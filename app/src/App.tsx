import { useCallback, useEffect, useState } from "react";

import { getQueue, getSystem, health, pauseQueue, readEvents, resumeQueue } from "./engine";
import { setTray, notify } from "./host";
import type { Queue, SetupProgress, System } from "./types";
import Findings from "./views/Findings";
import DashboardView from "./views/Dashboard";
import Models from "./views/Models";
import Repositories from "./views/Repositories";
import Runs from "./views/Runs";
import SettingsView from "./views/Settings";
import SystemView from "./views/System";
import "./App.css";

type Status =
  | { state: "starting" }
  | { state: "ready"; version: string }
  | { state: "failed"; reason: string };

const VIEWS = [
  "Overview",
  "Findings",
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
  // Every event that changes stored data bumps this, and each view reloads.
  const [version, setVersion] = useState(0);
  const [setup, setSetup] = useState<SetupProgress | null>(null);

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
    <div className="app">
      <header className="chrome">
        <h1>reviewrig</h1>
        <nav>
          {VIEWS.map((name) => (
            <button
              key={name}
              className={name === view ? "tab active" : "tab"}
              onClick={() => setView(name)}
            >
              {name}
            </button>
          ))}
        </nav>
        {queue && (
          <button className="tab" onClick={() => void togglePause()}>
            {queue.paused ? "Resume" : "Pause"}
            {queue.pending > 0 && <span className="pill">{queue.pending}</span>}
          </button>
        )}
        <span className={`engine engine-${status.state}`}>
          {status.state === "starting" && "Starting"}
          {status.state === "ready" && `Engine ${status.version}`}
          {status.state === "failed" && status.reason}
        </span>
      </header>
      {system?.config_error && (
        <p className="banner">
          Your config file was refused, so the rig is running on its defaults:{" "}
          {system.config_error}
        </p>
      )}
      {system?.sandbox.degraded && <p className="banner">{system.sandbox.warning}</p>}
      {queue?.paused && <p className="banner">Paused. Queued work waits.</p>}
      <main>
        {view === "Overview" && <DashboardView version={version} />}
        {view === "Findings" && <Findings version={version} onCounts={setTray} />}
        {view === "Repositories" && <Repositories scanning={scanning} />}
        {view === "Runs" && <Runs version={version} />}
        {view === "Models" && <Models setup={setup} />}
        {view === "Settings" && <SettingsView />}
        {view === "System" && <SystemView system={system} />}
      </main>
    </div>
  );
}
