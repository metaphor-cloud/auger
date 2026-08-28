import { Alert, AlertDescription, Badge, Button, EmptyState, Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@metaphor-cloud/ui";
import { useCallback, useEffect, useState } from "react";

import { getRepositories, requestReview, requestScan, rescan } from "../engine";
import type { Mode, Repository } from "../types";
import { Mono, PageTitle } from "../ui";

const MODE_LABEL: Record<Mode, string> = { off: "Off", draft: "Draft", complete: "Complete" };

export default function Repositories({
  scanning,
  version,
}: {
  scanning: boolean;
  version: number;
}) {
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
  }, [load, scanning, version]);

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
    <>
      <PageTitle
        title="Repositories"
        description={
          repositories === null
            ? "Loading"
            : `${repositories.length} found, ${enabled} under review`
        }
      >
        <Button size="sm" variant="secondary" onClick={onRescan} disabled={busy || scanning}>
          {busy || scanning ? "Scanning" : "Rescan"}
        </Button>
      </PageTitle>

      {error && (
        <Alert variant="danger" className="mb-3">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {repositories !== null && repositories.length === 0 && (
        <EmptyState
          title="No repositories found"
          description="Add a root in Settings and the rig walks it for git checkouts."
        />
      )}

      {repositories !== null && repositories.length > 0 && (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Repository</TableHead>
              <TableHead>Organisation</TableHead>
              <TableHead>Mode</TableHead>
              <TableHead className="text-right">Priority</TableHead>
              <TableHead>Path</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {repositories.map((repository) => (
              <TableRow key={repository.path} className={repository.policy.enabled ? "" : "opacity-60"}>
                <TableCell>{repository.name}</TableCell>
                <TableCell className="text-text-secondary">
                  {repository.org_key ?? "no remote"}
                </TableCell>
                <TableCell>
                  <Badge
                    variant={
                      !repository.policy.enabled
                        ? "outline"
                        : repository.policy.mode === "complete"
                          ? "success"
                          : "default"
                    }
                  >
                    {repository.policy.enabled ? MODE_LABEL[repository.policy.mode] : "Excluded"}
                  </Badge>
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {repository.policy.priority}
                </TableCell>
                <TableCell className="max-w-[22rem] truncate">
                  <Mono title={repository.path}>{repository.path}</Mono>
                </TableCell>
                <TableCell className="whitespace-nowrap text-right">
                  <Button
                    size="sm"
                    variant="ghost"
                    title="Review the latest commit now"
                    onClick={() => void requestReview(repository.path)}
                  >
                    Review
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    title="Run a security scan on this repository"
                    onClick={() => void requestScan(repository.path)}
                  >
                    Scan
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </>
  );
}
