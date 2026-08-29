import { EmptyState, Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@metaphor-cloud/ui";
import { useCallback, useEffect, useState } from "react";

import { getRuns } from "../engine";
import type { Run } from "../types";
import { Mono, PageTitle, StatusBadge } from "../ui";

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
    <>
      <PageTitle
        title="Runs"
        description="Every attempt, including the ones that were skipped and why."
      />

      {runs === null && <p className="text-xs text-text-secondary">Loading</p>}
      {runs !== null && runs.length === 0 && (
        <EmptyState
          title="Nothing has run yet"
          description="A repository is reviewed when its commit moves and nothing else is working in it."
        />
      )}
      {runs !== null && runs.length > 0 && (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Started</TableHead>
              <TableHead>Repository</TableHead>
              <TableHead>Kind</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Reason</TableHead>
              <TableHead className="text-right">Findings</TableHead>
              <TableHead className="text-right">Tokens</TableHead>
              <TableHead className="text-right">Time</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {runs.map((run) => (
              <TableRow key={run.id}>
                <TableCell>
                  <Mono>{run.started_at.replace("T", " ").slice(5, 16)}</Mono>
                </TableCell>
                <TableCell>{run.repo_path.split("/").slice(-2).join("/")}</TableCell>
                <TableCell className="text-text-secondary">{run.kind}</TableCell>
                <TableCell>
                  <StatusBadge status={run.status} />
                </TableCell>
                <TableCell className="text-text-secondary" title={run.error ?? ""}>
                  {run.reason ?? ""}
                  {run.attempts > 1 && ` ×${run.attempts}`}
                </TableCell>
                <TableCell className="text-right tabular-nums">{run.finding_count}</TableCell>
                <TableCell className="text-right tabular-nums">
                  {run.prompt_tokens + run.completion_tokens}
                </TableCell>
                <TableCell className="text-right tabular-nums text-text-secondary">
                  {seconds(run)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </>
  );
}
