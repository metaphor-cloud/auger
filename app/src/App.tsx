import { useEffect, useState } from "react";

import { engineFetch, engineInfo, readEvents, type EngineInfo } from "./engine";
import type { ServerEvent } from "./sse";
import "./App.css";

type Status = { state: "starting" } | { state: "ready"; version: string } | { state: "failed"; reason: string };

export default function App() {
  const [status, setStatus] = useState<Status>({ state: "starting" });
  const [events, setEvents] = useState<ServerEvent[]>([]);

  useEffect(() => {
    const abort = new AbortController();
    let info: EngineInfo | null = null;

    async function connect() {
      try {
        info = await engineInfo();
        const response = await engineFetch(info, "/health");
        if (!response.ok) throw new Error(`health check returned ${response.status}`);
        const health = (await response.json()) as { version: string };
        setStatus({ state: "ready", version: health.version });
        await readEvents(info, (event) => setEvents((seen) => [event, ...seen].slice(0, 50)), abort.signal);
      } catch (error) {
        if (abort.signal.aborted) return;
        setStatus({ state: "failed", reason: error instanceof Error ? error.message : String(error) });
      }
    }

    void connect();
    return () => abort.abort();
  }, []);

  return (
    <main>
      <h1>reviewrig</h1>
      <p className="status" data-state={status.state}>
        {status.state === "starting" && "Waiting for the engine"}
        {status.state === "ready" && `Engine ${status.version} is running`}
        {status.state === "failed" && `Engine unavailable: ${status.reason}`}
      </p>
      <h2>Events</h2>
      {events.length === 0 ? (
        <p className="empty">No events yet.</p>
      ) : (
        <ul className="events">
          {events.map((event, index) => (
            <li key={index}>
              <code>{event.kind}</code> {JSON.stringify(event.data)}
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
