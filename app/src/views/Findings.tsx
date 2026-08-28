import { Alert, AlertDescription, Badge, Button, EmptyState } from "@metaphor-cloud/ui";
import { useCallback, useEffect, useState } from "react";

import { getFindings, setFindingStatus } from "../engine";
import type { Finding } from "../types";
import { Mono, PageTitle, SeverityBadge } from "../ui";

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
  const [showDismissed, setShowDismissed] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const body = await getFindings(
        undefined,
        showSuppressed ? "suppressed" : "open",
        showDismissed,
      );
      setFindings(body.findings);
      setCounts(body.counts);
      onCounts(body.counts.total ?? 0, (body.counts.critical ?? 0) + (body.counts.high ?? 0));
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, [showSuppressed, showDismissed, onCounts]);

  useEffect(() => {
    void load();
  }, [load, version]);

  async function changeStatus(finding: Finding) {
    const next = finding.status === "suppressed" ? "open" : "suppressed";
    await setFindingStatus([finding.fingerprint], next);
    await load();
  }

  const summary =
    ORDER.filter((name) => counts[name])
      .map((name) => `${counts[name]} ${name}`)
      .join(", ") || "Nothing open";

  return (
    <>
      <PageTitle title={showSuppressed ? "Suppressed" : "Findings"} description={summary}>
        <Button size="sm" variant="ghost" onClick={() => setShowDismissed((value) => !value)}>
          {showDismissed ? "Hide dismissed" : "Show dismissed"}
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setShowSuppressed((value) => !value)}>
          {showSuppressed ? "Show open" : "Show suppressed"}
        </Button>
      </PageTitle>

      {error && (
        <Alert variant="danger" className="mb-3">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {findings === null && <p className="text-xs text-text-secondary">Loading</p>}
      {findings !== null && findings.length === 0 && (
        <EmptyState
          title={showSuppressed ? "Nothing suppressed" : "No open findings"}
          description={
            showSuppressed
              ? "Findings you suppress stay out of the list until you bring them back."
              : "The rig reports here as it reviews. Runs shows what it has looked at."
          }
        />
      )}

      <ul className="divide-y divide-border-subtle">
        {(findings ?? []).map((finding) => (
          <li key={finding.fingerprint}>
            <button
              className="flex w-full items-baseline gap-3 rounded-md px-1 py-2 text-left transition-colors hover:bg-bg-card-hover"
              onClick={() => setOpen(open === finding.fingerprint ? null : finding.fingerprint)}
            >
              <SeverityBadge severity={finding.severity} />
              <span className="min-w-0 flex-1 truncate">{finding.title}</span>
              <Mono>{fileLabel(finding)}</Mono>
              <span className="text-xs text-text-tertiary">{repoLabel(finding.repo_path)}</span>
              {finding.source !== "model" && <Badge variant="outline">{finding.source}</Badge>}
              {finding.triage === "false" && <Badge variant="outline">dismissed</Badge>}
              {finding.times_seen > 1 && (
                <span className="text-xs text-text-tertiary">seen {finding.times_seen}×</span>
              )}
            </button>
            {open === finding.fingerprint && (
              <div className="max-w-3xl px-1 pb-4 text-xs leading-relaxed">
                <p className="mb-2 text-text-primary">{finding.detail}</p>
                {finding.suggestion && (
                  <p className="mb-2 text-text-primary">
                    <span className="font-medium">Fix:</span> {finding.suggestion}
                  </p>
                )}
                <p className="mb-3">
                  <Mono>
                    {finding.source}
                    {finding.triage ? ` · triage ${finding.triage}` : ""} · confidence{" "}
                    {finding.confidence.toFixed(2)} · last seen {finding.last_seen_at}
                  </Mono>
                </p>
                <Button size="sm" variant="secondary" onClick={() => void changeStatus(finding)}>
                  {finding.status === "suppressed" ? "Bring back" : "Suppress"}
                </Button>
              </div>
            )}
          </li>
        ))}
      </ul>
    </>
  );
}
