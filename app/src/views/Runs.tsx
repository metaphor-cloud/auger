import { useCallback, useEffect, useState } from "react";

import { getRuns } from "../engine";
import type { Run } from "../types";

function seconds(run: Run) {
  return run.duration_ms === null ? "" : `${(run.duration_ms / 1000).toFixed(1)}s`;
}

export default function Runs({ version }: { version: number }) {
  const [runs, setRuns] = useState<Run[] | null>(null);

  const load = useCallback(async () => {
    setRuns((await getRuns()).runs);
  }, []);

  useEffect(() => {
    void load();
  }, [load, version]);

  return (
    <section>
      <header className="view-header">
        <div>
          <h2>Runs</h2>
          <p className="muted">Every attempt, including the ones that were skipped.</p>
        </div>
      </header>

      {runs === null && <p className="muted">Loading</p>}
      {runs !== null && runs.length === 0 && <p className="empty">Nothing has run yet.</p>}
      {runs !== null && runs.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Started</th>
              <th>Repository</th>
              <th>Status</th>
              <th>Reason</th>
              <th className="numeric">Findings</th>
              <th className="numeric">Tokens</th>
              <th className="numeric">Time</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.id}>
                <td className="muted mono">{run.started_at.replace("T", " ").slice(5, 16)}</td>
                <td>{run.repo_path.split("/").slice(-2).join("/")}</td>
                <td>
                  <span className={`badge run-${run.status}`}>{run.status}</span>
                </td>
                <td className="muted" title={run.error ?? ""}>
                  {run.reason ?? ""}
                  {run.attempts > 1 && ` ×${run.attempts}`}
                </td>
                <td className="numeric">{run.finding_count}</td>
                <td className="numeric">{run.prompt_tokens + run.completion_tokens}</td>
                <td className="numeric muted">{seconds(run)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
