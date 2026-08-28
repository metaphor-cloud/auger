import { useCallback, useEffect, useState } from "react";

import { checkModels, getCatalog, getModels, setupModels, startModels } from "../engine";
import type { BackendList, Catalog, SetupProgress } from "../types";

const JOB_CLASSES = ["review", "triage", "embed", "rerank"];

export default function Models({ setup }: { setup: SetupProgress | null }) {
  const [data, setData] = useState<BackendList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"" | "check" | "start" | "setup">("");
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [choice, setChoice] = useState("");

  const load = useCallback(async (fetcher: () => Promise<BackendList>) => {
    try {
      setData(await fetcher());
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, []);

  useEffect(() => {
    void load(getModels);
    void getCatalog().then((body) => {
      setCatalog(body);
      setChoice(body.recommended);
    });
  }, [load]);

  async function act(which: "check" | "start") {
    setBusy(which);
    await load(which === "check" ? checkModels : startModels);
    setBusy("");
  }

  async function runSetup() {
    setBusy("setup");
    try {
      const outcome = await setupModels(choice);
      if (!outcome.ok) setError(outcome.error);
      await load(getModels);
      setCatalog(await getCatalog());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  }

  const ready = data?.backends.some((backend) => backend.up) ?? false;

  return (
    <section>
      <header className="view-header">
        <div>
          <h2>Models</h2>
          <p className="muted">A job asks for a job class. The profile picks the backend.</p>
        </div>
        <button onClick={() => act("check")} disabled={busy !== ""}>
          {busy === "check" ? "Checking" : "Check"}
        </button>
        <button onClick={() => act("start")} disabled={busy !== ""}>
          {busy === "start" ? "Starting" : "Start managed"}
        </button>
      </header>

      {error && <p className="error">{error}</p>}

      {catalog && !ready && (
        <div className="setup">
          <h3>Set up</h3>
          <p className="muted">
            The rig fetches its own model runtime and weights. Nothing else to install.
            This machine can hold about {catalog.usable_memory_gb} GB.
          </p>
          <div className="setup-row">
            <select value={choice} onChange={(event) => setChoice(event.target.value)}>
              {catalog.models
                .filter((model) => model.job_class === "review")
                .map((model) => (
                  <option key={model.name} value={model.name} disabled={!model.fits}>
                    {model.name} — {model.description}
                    {model.fits ? "" : " (too large for this machine)"}
                  </option>
                ))}
            </select>
            <button onClick={() => void runSetup()} disabled={busy !== ""}>
              {busy === "setup" ? "Setting up" : "Set up"}
            </button>
          </div>
          {setup && (
            <p className="muted mono">
              {setup.total
                ? `${setup.name} ${(setup.fraction * 100).toFixed(1)}% (${(
                    setup.received / 1e9
                  ).toFixed(1)} GB)`
                : setup.message}
            </p>
          )}
        </div>
      )}
      {data === null ? (
        <p className="muted">Loading</p>
      ) : (
        <>
          <table>
            <thead>
              <tr>
                <th>Backend</th>
                <th>Model</th>
                <th>State</th>
                <th>Address</th>
                <th className="numeric">Requests</th>
                <th className="numeric">Tokens</th>
              </tr>
            </thead>
            <tbody>
              {data.backends.map((backend) => (
                <tr key={backend.name}>
                  <td>
                    {backend.name}
                    {backend.hosted && <span className="badge hosted">hosted</span>}
                  </td>
                  <td className="muted">{backend.model || "any"}</td>
                  <td>
                    <span className={`badge ${backend.up ? "mode-complete" : ""}`}>
                      {backend.up ? "Running" : "Stopped"}
                    </span>
                  </td>
                  <td className="muted path" title={backend.reason ?? backend.url}>
                    {backend.url}
                  </td>
                  <td className="numeric">{backend.requests}</td>
                  <td className="numeric">
                    {backend.prompt_tokens + backend.completion_tokens}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <h3>Active profile</h3>
          <dl className="facts">
            {JOB_CLASSES.map((jobClass) => (
              <div key={jobClass} style={{ display: "contents" }}>
                <dt>{jobClass}</dt>
                <dd className="mono">{data.active_profile_backends[jobClass] ?? "unset"}</dd>
              </div>
            ))}
          </dl>

          {!data.allow_hosted && (
            <p className="muted" style={{ marginTop: "1rem" }}>
              Hosted models are off. Turning one on sends your code off this machine.
            </p>
          )}
        </>
      )}
    </section>
  );
}
