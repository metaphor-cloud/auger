import { useCallback, useEffect, useState } from "react";

import { getFindings, setFindingStatus } from "../engine";
import type { Finding } from "../types";

const ORDER = ["critical", "high", "medium", "low", "info"];

function fileLabel(finding: Finding) {
  return finding.line ? `${finding.file}:${finding.line}` : finding.file;
}

function repoLabel(path: string) {
  return path.split("/").slice(-2).join("/");
}

export default function Findings({
  version,
  onCounts,
}: {
  version: number;
  onCounts: (open: number, critical: number) => void;
}) {
  const [findings, setFindings] = useState<Finding[] | null>(null);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [showSuppressed, setShowSuppressed] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const body = await getFindings(undefined, showSuppressed ? "suppressed" : "open");
      setFindings(body.findings);
      setCounts(body.counts);
      onCounts(body.counts.total ?? 0, (body.counts.critical ?? 0) + (body.counts.high ?? 0));
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, [showSuppressed, onCounts]);

  useEffect(() => {
    void load();
  }, [load, version]);

  async function changeStatus(finding: Finding) {
    const next = finding.status === "suppressed" ? "open" : "suppressed";
    await setFindingStatus([finding.fingerprint], next);
    await load();
  }

  return (
    <section>
      <header className="view-header">
        <div>
          <h2>{showSuppressed ? "Suppressed" : "Findings"}</h2>
          <p className="muted">
            {ORDER.filter((name) => counts[name])
              .map((name) => `${counts[name]} ${name}`)
              .join(", ") || "Nothing open"}
          </p>
        </div>
        <button onClick={() => setShowSuppressed((value) => !value)}>
          {showSuppressed ? "Show open" : "Show suppressed"}
        </button>
      </header>

      {error && <p className="error">{error}</p>}
      {findings === null && <p className="muted">Loading</p>}
      {findings !== null && findings.length === 0 && (
        <p className="empty">
          {showSuppressed ? "Nothing suppressed." : "No open findings."}
        </p>
      )}

      <ul className="findings">
        {(findings ?? []).map((finding) => (
          <li key={finding.fingerprint}>
            <button
              className="finding-head"
              onClick={() => setOpen(open === finding.fingerprint ? null : finding.fingerprint)}
            >
              <span className={`badge sev-${finding.severity}`}>{finding.severity}</span>
              <span className="finding-title">{finding.title}</span>
              <span className="muted mono">{fileLabel(finding)}</span>
              <span className="muted">{repoLabel(finding.repo_path)}</span>
              {finding.times_seen > 1 && (
                <span className="muted">seen {finding.times_seen}×</span>
              )}
            </button>
            {open === finding.fingerprint && (
              <div className="finding-body">
                <p>{finding.detail}</p>
                {finding.suggestion && (
                  <p>
                    <strong>Fix:</strong> {finding.suggestion}
                  </p>
                )}
                <p className="muted mono">
                  {finding.source} · confidence {finding.confidence.toFixed(2)} · last seen{" "}
                  {finding.last_seen_at}
                </p>
                <button onClick={() => void changeStatus(finding)}>
                  {finding.status === "suppressed" ? "Bring back" : "Suppress"}
                </button>
              </div>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
