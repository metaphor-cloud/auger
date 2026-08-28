import { Alert, AlertDescription, Badge, StatCard, Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@metaphor-cloud/ui";
import { useCallback, useEffect, useState } from "react";

import { getDashboard } from "../engine";
import type { Dashboard } from "../types";
import { Fact, Facts, Mono, PageTitle, SeverityBadge, Section, StatusBadge } from "../ui";

const SEVERITIES = ["critical", "high", "medium", "low", "info"];
const BAR: Record<string, string> = {
  critical: "bg-danger",
  high: "bg-warning",
  medium: "bg-accent",
  low: "bg-text-tertiary",
  info: "bg-text-tertiary",
};

function when(value: string | null) {
  return value ? value.replace("T", " ").slice(0, 16) : "never";
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

  if (error)
    return (
      <Alert variant="danger">
        <AlertDescription>{error}</AlertDescription>
      </Alert>
    );
  if (data === null) return <p className="text-xs text-text-secondary">Loading</p>;

  const open = data.findings.total ?? 0;
  const worst = SEVERITIES.find((name) => (data.findings[name] ?? 0) > 0);
  const skipped = Object.entries(data.skipped_reasons);
  const doing = data.paused
    ? "Paused. Queued work waits."
    : data.in_flight.length
      ? `Reviewing ${data.in_flight.length} of ${data.workers}`
      : data.pending
        ? `${data.pending} waiting`
        : "Idle. Watching for changes.";

  return (
    <>
      <PageTitle title="Overview" description={doing} />

      {data.warnings.map((warning) => (
        <Alert variant="warning" className="mb-3" key={warning}>
          <AlertDescription>{warning}</AlertDescription>
        </Alert>
      ))}

      <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="open findings"
          value={open}
          color={worst === "critical" ? "danger" : worst === "high" ? "warning" : "default"}
        />
        <StatCard label="repositories watched" value={`${data.enabled}/${data.repositories}`} />
        <StatCard
          label="models running"
          value={`${data.models_up}/${data.models_total}`}
          color={data.models_up === 0 ? "danger" : "success"}
        />
        <StatCard label="runs today" value={data.runs_today} />
      </div>

      <Section title="Findings" description={`${data.suppressed} suppressed, ${data.dismissed} dismissed by triage.`}>
        {open === 0 ? (
          <p className="text-xs text-text-secondary">Nothing open.</p>
        ) : (
          <div className="flex max-w-lg flex-col gap-1.5">
            {SEVERITIES.filter((name) => data.findings[name]).map((name) => (
              <div className="flex items-center gap-3" key={name}>
                <span className="w-16 text-xs text-text-secondary">{name}</span>
                <span
                  className={`h-2 rounded-sm ${BAR[name]}`}
                  style={{ width: `${Math.max(3, (data.findings[name] / open) * 100)}%` }}
                />
                <span className="text-xs tabular-nums text-text-secondary">
                  {data.findings[name]}
                </span>
              </div>
            ))}
          </div>
        )}
      </Section>

      {data.busiest.length > 0 && (
        <Section title="Needs attention">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Repository</TableHead>
                <TableHead className="text-right">Open</TableHead>
                <TableHead>Worst</TableHead>
                <TableHead>Last run</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.busiest.map((repo) => (
                <TableRow key={repo.path}>
                  <TableCell title={repo.path}>{repo.name}</TableCell>
                  <TableCell className="text-right tabular-nums">{repo.open_findings}</TableCell>
                  <TableCell>
                    <SeverityBadge severity={repo.worst_severity} />
                  </TableCell>
                  <TableCell>
                    <Mono>{when(repo.last_run_at)}</Mono>
                    {repo.last_status && (
                      <span className="ml-2">
                        <StatusBadge status={repo.last_status} />
                      </span>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Section>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Section title="Work">
          <Facts>
            <Fact label="Runs">
              {Object.entries(data.runs_by_status).map(([status, count]) => (
                <span className="mr-2" key={status}>
                  <Badge variant="outline">
                    {count} {status}
                  </Badge>
                </span>
              )) || "none yet"}
            </Fact>
            {skipped.length > 0 && (
              <Fact label="Skipped because">
                {skipped.map(([reason, count]) => `${reason} ×${count}`).join(", ")}
              </Fact>
            )}
            <Fact label="Tokens">
              <Mono>
                {(data.prompt_tokens / 1000).toFixed(1)}k in,{" "}
                {(data.completion_tokens / 1000).toFixed(1)}k out
              </Mono>
            </Fact>
            <Fact label="Last run">
              <Mono>{when(data.last_run_at)}</Mono>
            </Fact>
          </Facts>
        </Section>

        <Section title="Rig">
          <Facts>
            <Fact label="Sandbox">{data.sandbox.backend}</Fact>
            <Fact label="Code index">
              {data.indexed_files} files, {data.chunks} chunks
            </Fact>
            <Fact label="Call graph">{data.codegraph ? "CodeGraph on" : "off"}</Fact>
            {data.excluded > 0 && <Fact label="Excluded">{data.excluded} repositories</Fact>}
          </Facts>
        </Section>
      </div>
    </>
  );
}
