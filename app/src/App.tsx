import { Badge, Button, useThemeContext } from "@metaphor-cloud/ui";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  getActivity,
  getModels,
  getOnboarding,
  getQueue,
  getSystem,
  health,
  pauseQueue,
  readEvents,
  resumeQueue,
  stopModels,
} from "./engine";
import Logo from "./Logo";
import Now from "./parts/Now";
import { notify, onTrayAction, setTray, setTrayActions, type TrayAction } from "./host";
import type { Activity, Queue, SetupProgress, Step, System } from "./types";
import Work from "./views/Work";
import OnboardingView from "./views/Onboarding";
import Runs from "./views/Runs";
import TranscriptView from "./views/Transcript";
import SettingsView from "./views/Settings";

type Status =
  | { state: "starting" }
  | { state: "ready"; version: string }
  | { state: "failed"; reason: string };

const VIEWS = ["Work", "Transcript", "Runs", "Settings"] as const;
type View = (typeof VIEWS)[number];

/** One mark per view. A sidebar is read by shape before it is read by word. */
const MARK: Record<View, string> = { Work: "◈", Transcript: "❝", Runs: "≡", Settings: "⚙" };

function PlayMark() {
  return (
    <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
      <path d="M2 1 L9 5 L2 9 Z" fill="currentColor" />
    </svg>
  );
}

function PauseMark() {
  return (
    <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
      <rect x="2" y="1" width="2.4" height="8" rx="0.6" fill="currentColor" />
      <rect x="5.8" y="1" width="2.4" height="8" rx="0.6" fill="currentColor" />
    </svg>
  );
}

const LOUD = new Set(["critical", "high"]);

export default function App() {
  const [status, setStatus] = useState<Status>({ state: "starting" });
  const [view, setView] = useState<View>("Work");
  const [system, setSystem] = useState<System | null>(null);
  const [queue, setQueue] = useState<Queue | null>(null);
  const [activity, setActivity] = useState<Activity | null>(null);
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

  // Asked for once on connect, and once more whenever a run ends. In between, the
  // progress events keep it current, because a window that polled for this would be a
  // second behind whatever it is meant to be showing.
  const refreshActivity = useCallback(async () => {
    try {
      setActivity(await getActivity());
    } catch {
      // The bar is a detail. A failure here must not blank the window.
    }
  }, []);

  // Boot runs in the background, so the queue becomes ready a moment after health
  // answers. Ask again until it does, then stop.
  useEffect(() => {
    if (queue?.ready) return;
    const timer = setInterval(() => void refreshQueue(), 1500);
    return () => clearInterval(timer);
  }, [queue?.ready, refreshQueue]);

  useEffect(() => {
    const abort = new AbortController();

    async function connect() {
      try {
        const info = await health();
        setStatus({ state: "ready", version: info.version });
        setSystem(await getSystem());
        setOnboarded((await getOnboarding()).done);
        await refreshQueue();
        await refreshActivity();
        await readEvents((event) => {
          if (event.kind === "run.progress") {
            // The engine names one step at a time. The bar holds the rest, so two
            // repositories reviewed at once do not take turns disappearing.
            const step = event.data as unknown as Step;
            setActivity((current) => {
              const base = current ?? { steps: [], pending: 0, paused: false, ready: true, workers: 1, last: null };
              const rest = base.steps.filter((one) => one.id !== step.id);
              return {
                ...base,
                steps: step.phase === "done" ? rest : [...rest, step].sort((a, b) => a.id - b.id),
              };
            });
            return;
          }
          if (event.kind.startsWith("queue.") || event.kind === "scan.finished") {
            void refreshQueue();
            setVersion((value) => value + 1);
          }
          if (event.kind === "setup.progress") setSetup(event.data as SetupProgress);
          if (event.kind === "setup.finished") setSetup(null);
          if (event.kind === "config.reloaded") setVersion((value) => value + 1);
          // The first download takes minutes, so System follows it rather than
          // showing whatever the state was when the window opened.
          if (event.kind === "image.state") void getSystem().then(setSystem);
          if (event.kind === "run.finished" || event.kind === "run.skipped") {
            void refreshQueue();
            void refreshActivity();
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
  }, [refreshQueue, refreshActivity]);

  const [refused, setRefused] = useState<string | null>(null);

  const [loaded, setLoaded] = useState(false);

  // The tray works with the window hidden, and a hidden window still runs, so it does
  // the asking. Its menu has to say what the engine is actually doing.
  useEffect(() => {
    void setTrayActions(queue?.ready === true && !queue.paused, queue?.ready === true, loaded);
  }, [queue?.ready, queue?.paused, loaded]);

  const refreshModels = useCallback(async () => {
    try {
      const body = await getModels();
      setLoaded(body.backends.some((one) => one.ours));
    } catch {
      // The tray is a detail. A failure here must not blank the window.
    }
  }, []);

  useEffect(() => {
    void refreshModels();
    const timer = setInterval(() => void refreshModels(), 15000);
    return () => clearInterval(timer);
  }, [refreshModels, version]);

  // One subscription for the life of the window. The handler changes every render, so
  // the listener reads it from a box rather than being registered again each time.
  const onAction = useRef<(action: TrayAction) => void>(() => undefined);
  useEffect(() => {
    let stop = () => undefined as void;
    let gone = false;
    void onTrayAction((action) => onAction.current(action)).then((off) => {
      if (gone) off();
      else stop = off;
    });
    return () => {
      gone = true;
      stop();
    };
  }, []);

  onAction.current = (action) => {
    if (action === "reviewing") void togglePause();
    if (action === "unload") void stopModels().then(() => refreshModels());
  };

  async function togglePause() {
    try {
      setQueue(queue?.paused ? await resumeQueue() : await pauseQueue());
      setRefused(null);
    } catch (cause) {
      // The engine refuses to start with no model, because a queue that runs without
      // one only produces a failed run per repository.
      setRefused(cause instanceof Error ? cause.message : String(cause));
      void refreshQueue();
    }
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
        <div className="flex items-center gap-2 px-4 py-3 text-accent">
          <Logo size={17} />
          <span className="text-sm font-semibold tracking-tight text-text-primary">Auger</span>
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
          {queue && !queue.ready && (
            // The first walk takes seconds. Until the workers exist there is nothing
            // to start or stop, and a button that says otherwise is a lie. It does not
            // say "Starting" either: this button says "Start" once it works, and the
            // same word for the state and for the action reads as work in progress.
            <Button size="sm" variant="ghost" className="w-full justify-start" disabled>
              <span className="w-3 text-center opacity-70">·</span>
              Loading
            </Button>
          )}
          {queue?.ready && (
            <Button
              size="sm"
              variant="ghost"
              // Stopped is the state the window opens in, so the control that changes
              // it carries the accent. Running is the quiet state.
              className={`w-full justify-start ${queue.paused ? "text-accent" : ""}`}
              title={
                queue.models_ready === false
                  ? (queue.models_reason ?? "no model answers yet")
                  : queue.paused
                    ? "Start. Nothing runs until you press this."
                    : "Finish what is running, then stop pulling work."
              }
              onClick={() => void togglePause()}
            >
              <span className="w-3 text-center">
                {queue.paused ? <PlayMark /> : <PauseMark />}
              </span>
              {queue.paused ? "Start" : "Working"}
              {queue.pending > 0 && (
                <Badge variant="outline" className="ml-auto">
                  {queue.pending}
                </Badge>
              )}
            </Button>
          )}
          {refused && (
            <p className="px-3 text-[11px] leading-snug text-warning">
              {refused}{" "}
              <button className="underline" onClick={() => setView("Settings")}>
                Open Models
              </button>
            </p>
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

      <main className="flex min-w-0 flex-1 flex-col overflow-hidden overscroll-none">
        <div className="min-h-0 flex-1 overflow-hidden">
          {view === "Work" && (
            <Work version={version} onCounts={setTray} onOpenRuns={() => setView("Runs")} />
          )}
          {view === "Transcript" && <TranscriptView version={version} />}
          {view === "Runs" && (
            <div className="h-full overscroll-none overflow-auto px-5 py-5">
              <Runs version={version} />
            </div>
          )}
          {view === "Settings" && (
            <div className="h-full overscroll-none overflow-auto px-5 py-5">
              <SettingsView version={version} setup={setup} system={system} />
            </div>
          )}
        </div>
        {/* Below the view, not inside it: "is it working" is not a question about the
            page you happen to be on. */}
        <Now activity={activity} />
      </main>
    </div>
  );
}
