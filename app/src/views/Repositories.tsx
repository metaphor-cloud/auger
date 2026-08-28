import { useCallback, useEffect, useState } from "react";

import { getRepositories, rescan } from "../engine";
import type { Mode, Repository } from "../types";

const MODE_LABEL: Record<Mode, string> = {
  off: "Off",
  draft: "Draft",
  complete: "Complete",
};

export default function Repositories({ scanning }: { scanning: boolean }) {
  const [repositories, setRepositories] = useState<Repository[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setRepositories((await getRepositories()).repositories);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, scanning]);

  async function onRescan() {
    setBusy(true);
    try {
      setRepositories((await rescan()).repositories);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  const enabled = repositories?.filter((one) => one.policy.enabled).length ?? 0;

  return (
    <section>
      <header className="view-header">
        <div>
          <h2>Repositories</h2>
          <p className="muted">
            {repositories === null
              ? "Loading"
              : `${repositories.length} found, ${enabled} under review`}
          </p>
        </div>
        <button onClick={onRescan} disabled={busy || scanning}>
          {busy || scanning ? "Scanning" : "Rescan"}
        </button>
      </header>

      {error && <p className="error">{error}</p>}

      {repositories !== null && repositories.length === 0 && (
        <p className="empty">
          No repositories found. Add a root to <code>~/.reviewrig/config.toml</code>.
        </p>
      )}

      {repositories !== null && repositories.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Repository</th>
              <th>Organisation</th>
              <th>Mode</th>
              <th className="numeric">Priority</th>
              <th>Path</th>
            </tr>
          </thead>
          <tbody>
            {repositories.map((repository) => (
              <tr key={repository.path} className={repository.policy.enabled ? "" : "disabled"}>
                <td>{repository.name}</td>
                <td className="muted">{repository.org_key ?? "no remote"}</td>
                <td>
                  <span className={`badge mode-${repository.policy.enabled ? repository.policy.mode : "off"}`}>
                    {repository.policy.enabled ? MODE_LABEL[repository.policy.mode] : "Disabled"}
                  </span>
                </td>
                <td className="numeric">{repository.policy.priority}</td>
                <td className="muted path" title={repository.path}>
                  {repository.path}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
