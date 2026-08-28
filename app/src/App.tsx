import { Badge, Button, useThemeContext } from "@metaphor-cloud/ui";
import { useCallback, useEffect, useState } from "react";

import { getOnboarding, getQueue, getSystem, health, pauseQueue, readEvents, resumeQueue } from "./engine";
import { notify, setTray } from "./host";
import type { Queue, SetupProgress, System } from "./types";
import MapView from "./views/Map";
import OnboardingView from "./views/Onboarding";
import Runs from "./views/Runs";
import SettingsView from "./views/Settings";

type Status =
  | { state: "starting" }
  | { state: "ready"; version: string }
  | { state: "failed"; reason: string };

const VIEWS = ["Map", "Runs", "Settings"] as const;
type View = (typeof VIEWS)[number];

/** One mark per view. A sidebar is read by shape before it is read by word. */
const MARK: Record<View, string> = { Map: "◈", Runs: "≡", Settings: "⚙" };

const LOUD = new Set(["critical", "high"]);

export default function App() {
  const [status, setStatus] = useState<Status>({ state: "starting" });
  const [view, setView] = useState<View>("Map");
  const [system, setSystem] = useState<System | null>(null);
  const [queue, setQueue] = useState<Queue | null>(null);
  const [version, setVersion] = useState(0);
  const [setup, setSetup] = useState<SetupProgress | null>(null);
  const [onboarded, setOnboarded] = useState<boolean | null>(null);
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
        setOnboarded((await getOnboarding()).done);
        await refreshQueue();
        await readEvents((event) => {
          if (event.kind.startsWith("queue.") || event.kind === "scan.finished") {
            void refreshQueue();
            setVersion((value) => value + 1);
          }
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

  if (onboarded === false) {
    return (
      <OnboardingView
        setup={setup}
        onDone={() => {
          setOnboarded(true);
          setVersion((value) => value + 1);
        }}
      />
    );
  }

  return (
    <div className="flex h-full bg-bg text-text-primary">
      <aside className="flex w-[13.5rem] shrink-0 flex-col border-r border-border bg-bg-elevated">
        <div className="flex items-center gap-2 px-4 py-3">
          <span className="text-sm font-semibold tracking-tight">reviewrig</span>
        </div>

        <nav className="flex flex-1 flex-col gap-0.5 px-2">
          {VIEWS.map((name) => (
            <button
              key={name}
              onClick={() => setView(name)}
              className={
                name === view
                  ? "flex items-center gap-2.5 rounded-md bg-accent-glow px-3 py-1.5 text-xs font-medium text-accent"
                  : "flex items-center gap-2.5 rounded-md px-3 py-1.5 text-xs text-text-secondary transition-colors hover:bg-bg-card-hover hover:text-text-primary"
              }
            >
              <span className="w-3 text-center opacity-70">{MARK[name]}</span>
              {name}
            </button>
          ))}
        </nav>

        <div className="space-y-2 border-t border-border px-3 py-3">
          {queue && (
            <Button
              size="sm"
              variant="ghost"
              className="w-full justify-start"
              onClick={() => void togglePause()}
            >
              {queue.paused ? "Resume reviews" : "Pause reviews"}
              {queue.pending > 0 && (
                <Badge variant="outline" className="ml-auto">
                  {queue.pending}
                </Badge>
              )}
            </Button>
          )}
          <Button
            size="sm"
            variant="ghost"
            className="w-full justify-start"
            aria-label="Switch theme"
            onClick={cycleTheme}
          >
            <span className="w-3 text-center opacity-70">{resolved === "dark" ? "☾" : "☀"}</span>
            {resolved === "dark" ? "Dark" : "Light"}
          </Button>
          <p
            className={
              status.state === "failed"
                ? "px-3 text-[11px] text-danger"
                : "px-3 text-[11px] text-text-tertiary"
            }
          >
            {status.state === "starting" && "Starting"}
            {status.state === "ready" && `Engine ${status.version}`}
            {status.state === "failed" && status.reason}
          </p>
        </div>
      </aside>

      <main className="min-w-0 flex-1 overflow-hidden">
        {view === "Map" && (
          <MapView version={version} onCounts={setTray} onOpenRuns={() => setView("Runs")} />
        )}
        {view === "Runs" && (
          <div className="h-full overflow-auto px-5 py-5">
            <Runs version={version} />
          </div>
        )}
        {view === "Settings" && (
          <div className="h-full overflow-auto px-5 py-5">
            <SettingsView version={version} setup={setup} system={system} />
          </div>
        )}
      </main>
    </div>
  );
}
