import { useCallback, useEffect, useState } from "react";

import { getDashboard } from "../engine";
import type { Dashboard } from "../types";

const SEVERITIES = ["critical", "high", "medium", "low", "info"];

function when(value: string | null) {
  return value ? value.replace("T", " ").slice(0, 16) : "never";
}

function Tile({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className={`tile${tone ? ` tile-${tone}` : ""}`}>
      <span className="tile-value">{value}</span>
      <span className="tile-label">{label}</span>
    </div>
  );
}

export default function DashboardView({ version }: { version: number }) {
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await getDashboard());
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, version]);

  if (error) return <p className="error">{error}</p>;
  if (data === null) return <p className="muted">Loading</p>;

  const open = data.findings.total ?? 0;
  const worst = SEVERITIES.find((name) => (data.findings[name] ?? 0) > 0);
  const skipped = Object.entries(data.skipped_reasons);

  return (
    <section>
      <header className="view-header">
        <div>
          <h2>Overview</h2>
          <p className="muted">
            {data.paused
              ? "Paused. Queued work waits."
              : data.in_flight.length
                ? `Reviewing ${data.in_flight.length} of ${data.workers}`
                : data.pending
                  ? `${data.pending} waiting`
                  : "Idle. Watching for changes."}
          </p>
        </div>
      </header>

      {data.warnings.map((warning) => (
        <p className="banner" key={warning}>
          {warning}
        </p>
      ))}

      <div className="tiles">
        <Tile label="open findings" value={String(open)} tone={worst ? `sev-${worst}` : undefined} />
        <Tile label="repositories watched" value={`${data.enabled}/${data.repositories}`} />
        <Tile label="models running" value={`${data.models_up}/${data.models_total}`} />
        <Tile label="runs today" value={String(data.runs_today)} />
      </div>

      <h3>Findings</h3>
      {open === 0 ? (
        <p className="muted">Nothing open.</p>
      ) : (
        <div className="bars">
          {SEVERITIES.filter((name) => data.findings[name]).map((name) => (
            <div className="bar-row" key={name}>
              <span className="bar-label">{name}</span>
              <span
                className={`bar sev-${name}`}
                style={{ width: `${Math.max(4, (data.findings[name] / open) * 100)}%` }}
              />
              <span className="bar-count">{data.findings[name]}</span>
            </div>
          ))}
        </div>
      )}
      <p className="muted">
        {data.suppressed} suppressed, {data.dismissed} dismissed by triage.
      </p>

      {data.busiest.length > 0 && (
        <>
          <h3>Needs attention</h3>
          <table>
            <thead>
              <tr>
                <th>Repository</th>
                <th className="numeric">Open</th>
                <th>Worst</th>
                <th>Last run</th>
              </tr>
            </thead>
            <tbody>
              {data.busiest.map((repo) => (
                <tr key={repo.path}>
                  <td title={repo.path}>{repo.name}</td>
                  <td className="numeric">{repo.open_findings}</td>
                  <td>
                    <span className={`badge sev-${repo.worst_severity}`}>
                      {repo.worst_severity}
                    </span>
                  </td>
                  <td className="muted mono">
                    {when(repo.last_run_at)}
                    {repo.last_status ? ` · ${repo.last_status}` : ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      <h3>Work</h3>
      <dl className="facts">
        <dt>Runs</dt>
        <dd>
          {Object.entries(data.runs_by_status)
            .map(([status, count]) => `${count} ${status}`)
            .join(", ") || "none yet"}
        </dd>
        {skipped.length > 0 && (
          <>
            <dt>Skipped because</dt>
            <dd>{skipped.map(([reason, count]) => `${reason} ×${count}`).join(", ")}</dd>
          </>
        )}
        <dt>Tokens</dt>
        <dd className="mono">
          {(data.prompt_tokens / 1000).toFixed(1)}k in, {(data.completion_tokens / 1000).toFixed(1)}k
          out
        </dd>
        <dt>Last run</dt>
        <dd className="mono">{when(data.last_run_at)}</dd>
      </dl>

      <h3>Rig</h3>
      <dl className="facts">
        <dt>Sandbox</dt>
        <dd>{data.sandbox.backend}</dd>
        <dt>Code index</dt>
        <dd>
          {data.indexed_files} files, {data.chunks} chunks
        </dd>
        <dt>Call graph</dt>
        <dd>{data.codegraph ? "CodeGraph on" : "off"}</dd>
        {data.excluded > 0 && (
          <>
            <dt>Excluded</dt>
            <dd>{data.excluded} repositories</dd>
          </>
        )}
      </dl>
    </section>
  );
}
