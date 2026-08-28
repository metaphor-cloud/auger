import { useEffect, useState } from "react";

import { getSystem, health, readEvents } from "./engine";
import type { System } from "./types";
import Models from "./views/Models";
import Repositories from "./views/Repositories";
import SystemView from "./views/System";
import "./App.css";

type Status =
  | { state: "starting" }
  | { state: "ready"; version: string }
  | { state: "failed"; reason: string };

const VIEWS = ["Repositories", "Models", "System"] as const;
type View = (typeof VIEWS)[number];

export default function App() {
  const [status, setStatus] = useState<Status>({ state: "starting" });
  const [view, setView] = useState<View>("Repositories");
  const [scanning, setScanning] = useState(false);
  const [system, setSystem] = useState<System | null>(null);

  useEffect(() => {
    const abort = new AbortController();

    async function connect() {
      try {
        const info = await health();
        setStatus({ state: "ready", version: info.version });
        setSystem(await getSystem());
        await readEvents((event) => {
          if (event.kind === "scan.started") setScanning(true);
          if (event.kind === "scan.finished") setScanning(false);
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
  }, []);

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
        <span className={`engine engine-${status.state}`}>
          {status.state === "starting" && "Starting"}
          {status.state === "ready" && `Engine ${status.version}`}
          {status.state === "failed" && status.reason}
        </span>
      </header>
      {system?.sandbox.degraded && <p className="banner">{system.sandbox.warning}</p>}
      <main>
        {view === "Repositories" && <Repositories scanning={scanning} />}
        {view === "Models" && <Models />}
        {view === "System" && <SystemView system={system} />}
      </main>
    </div>
  );
}
