/** The optional engine, and the models that only it can run.
 *
 * The engine Auger brings holds the whole model in memory, which caps a laptop at a
 * 30B dense reviewer. This one streams a sparse model's experts from disk and keeps only
 * the dense layers resident, so the same machine can run a model an order of magnitude
 * larger: slower per token, with far more in it.
 *
 * It is off until asked for, and the two things people find out too late are said before
 * the download rather than after: it needs Python 3, and it cannot embed.
 */

import { Alert, AlertDescription, Badge, Button, Input } from "@metaphor-cloud/ui";
import { useCallback, useEffect, useState } from "react";

import { fetchColiModel, getColi, installColi, searchColiModels } from "../engine";
import type { Coli, Found } from "../types";
import { Mono } from "../ui";

export default function SecondEngine({ onQueued }: { onQueued: () => void }) {
  const [coli, setColi] = useState<Coli | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<Found[] | null>(null);

  const load = useCallback(async () => {
    try {
      setColi(await getColi());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function install() {
    setBusy("install");
    setError("");
    try {
      setColi(await installColi());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  }

  async function fetchOne(repo: string) {
    setBusy(repo);
    setError("");
    try {
      await fetchColiModel(repo);
      onQueued();
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  }

  async function look() {
    setBusy("search");
    setError("");
    try {
      setFound((await searchColiModels(query)).results);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  }

  if (coli === null) return <p className="text-xs text-text-secondary">Loading</p>;

  const installed = coli.installed !== "";
  return (
    <>
      {error && (
        <Alert variant="danger" className="mb-3">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <div className="mb-3 flex items-baseline gap-2">
        <span className="text-xs text-text-primary">{coli.name}</span>
        <Badge variant={installed ? "success" : "outline"}>
          {installed ? "installed" : "not installed"}
        </Badge>
        {!installed && (
          <Button
            size="sm"
            variant="secondary"
            className="ml-auto"
            disabled={busy !== "" || coli.problems.length > 0}
            onClick={() => void install()}
          >
            {busy === "install" ? "Installing" : "Install"}
          </Button>
        )}
        {installed && <Mono>{coli.installed}</Mono>}
      </div>

      {coli.problems.length > 0 && (
        <Alert variant="warning" className="mb-3">
          <AlertDescription>
            <ul className="list-disc pl-4">
              {coli.problems.map((one) => (
                <li key={one}>{one}</li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      )}

      <p className="mb-3 text-[11px] leading-relaxed text-text-secondary">
        It answers chat only: {coli.cannot_serve.join(" and ")} stay with the engine Auger
        brings, so both run at once. These models are format conversions published by
        individuals rather than releases from the people who trained them, and the size
        below is disk, not memory: this engine streams what it needs and holds the rest
        on disk, so what it needs resident is its own plan rather than the file sizes.
      </p>

      <ul className="divide-y divide-border-subtle">
        {coli.models.map((model) => (
          <li key={model.repo} className="flex items-baseline gap-2 py-2">
            <div className="min-w-0 flex-1">
              <div className="flex items-baseline gap-2">
                <span className="text-xs text-text-primary">{model.name}</span>
                <span className="text-[11px] tabular-nums text-text-secondary">
                  {model.disk_gb} GB
                </span>
                {model.downloaded && <Badge variant="success">on this machine</Badge>}
              </div>
              <p className="text-[11px] text-text-secondary">{model.description}</p>
              <Mono title={`published by ${model.uploader}`}>{model.repo}</Mono>
            </div>
            <Button
              size="sm"
              variant="ghost"
              className="h-6 shrink-0"
              title={
                installed
                  ? "Fetch these weights and review with them. It goes on the download queue, where it can be paused."
                  : "Install the engine first."
              }
              disabled={!installed || busy !== "" || model.downloaded}
              onClick={() => void fetchOne(model.repo)}
            >
              {busy === model.repo ? "Queueing" : model.downloaded ? "Fetched" : "Fetch"}
            </Button>
          </li>
        ))}
      </ul>

      {installed && (
        <div className="mt-4 border-t border-border-subtle pt-3">
          <p className="mb-2 text-[11px] text-text-secondary">
            The list above is a shortlist. This searches Hugging Face for anything else in
            the same format; whether a repository really holds weights this engine can read
            is checked when it is fetched.
          </p>
          <div className="flex gap-2">
            <Input
              className="h-7 max-w-sm"
              value={query}
              placeholder="A model, or a family"
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void look();
              }}
            />
            <Button
              size="sm"
              variant="secondary"
              disabled={busy !== "" || query.trim() === ""}
              onClick={() => void look()}
            >
              {busy === "search" ? "Looking" : "Search"}
            </Button>
          </div>
          {found !== null && found.length === 0 && (
            <p className="mt-2 text-xs text-text-tertiary">Nothing under that name.</p>
          )}
          {found !== null && found.length > 0 && (
            <ul className="mt-2 divide-y divide-border-subtle">
              {found.map((one) => (
                <li key={one.id} className="flex items-baseline gap-2 py-1.5">
                  <Mono>{one.id}</Mono>
                  <span className="text-[11px] tabular-nums text-text-tertiary">
                    {one.downloads} downloads
                  </span>
                  {one.gated && <Badge variant="warning">gated</Badge>}
                  <Button
                    size="sm"
                    variant="ghost"
                    className="ml-auto h-6"
                    disabled={busy !== ""}
                    onClick={() => void fetchOne(one.id)}
                  >
                    {busy === one.id ? "Queueing" : "Fetch"}
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </>
  );
}
