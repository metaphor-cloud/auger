/** Finding a model that is not on the recommended list.
 *
 * The list is the expectation, and this is everything else. It searches for what the
 * rig can actually run: one GGUF file that `llama-server` can load. A repository split
 * across shards, or published as safetensors, is not offered, because fetching it
 * would produce something that never starts.
 */

import {
  Alert,
  AlertDescription,
  Badge,
  Button,
  Input,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@metaphor-cloud/ui";
import { useState } from "react";

import { fetchModel, modelFiles, searchModels } from "../engine";
import type { FileResults, Found, SearchResults } from "../types";
import { Mono } from "../ui";

const JOBS = [
  { value: "review", label: "Reviewer" },
  { value: "verify", label: "Second opinion" },
  { value: "embed", label: "Embedder" },
];

function count(value: number) {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${Math.round(value / 1000)}k`;
  return String(value);
}

export default function ModelSearch({ onFetched }: { onFetched: () => void }) {
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<SearchResults | null>(null);
  const [chosen, setChosen] = useState<Found | null>(null);
  const [files, setFiles] = useState<FileResults | null>(null);
  const [job, setJob] = useState("review");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function run<T>(what: string, work: () => Promise<T>): Promise<T | null> {
    setBusy(what);
    setError(null);
    try {
      return await work();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      return null;
    } finally {
      setBusy("");
    }
  }

  async function search() {
    const body = await run("search", () => searchModels(query));
    if (body) {
      setFound(body);
      setChosen(null);
      setFiles(null);
    }
  }

  async function open(one: Found) {
    setChosen(one);
    const body = await run("files", () => modelFiles(one.id));
    setFiles(body);
  }

  async function take(filename: string) {
    if (!chosen) return;
    const body = await run("fetch", () => fetchModel(chosen.id, filename, job));
    if (body && !body.ok) setError(body.error);
    if (body?.ok) onFetched();
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <Input
          value={query}
          placeholder="qwen coder, devstral, a family you trust"
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") void search();
          }}
        />
        <Select value={job} onValueChange={setJob}>
          <SelectTrigger className="w-44">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {JOBS.map((one) => (
              <SelectItem key={one.value} value={one.value}>
                {one.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button disabled={!query.trim() || busy !== ""} onClick={() => void search()}>
          {busy === "search" ? "Looking" : "Find"}
        </Button>
      </div>

      {error && (
        <Alert variant="danger">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {found && found.results.length === 0 && (
        <p className="text-xs text-text-tertiary">
          Nothing that Auger can run. It only offers a model published as one GGUF file.
        </p>
      )}

      {found && found.results.length > 0 && (
        <ul className="divide-y divide-border-subtle rounded-md border border-border-subtle">
          {found.results.map((one) => (
            <li key={one.id}>
              <button
                onClick={() => void open(one)}
                className={`flex w-full items-center gap-2 px-3 py-1.5 text-left transition-colors hover:bg-bg-card-hover ${
                  chosen?.id === one.id ? "bg-bg-card-hover" : ""
                }`}
              >
                <span className="min-w-0 flex-1 truncate text-xs text-text-primary">{one.id}</span>
                {one.gated && (
                  <Badge variant="warning" title="Its publisher requires a licence acceptance">
                    gated
                  </Badge>
                )}
                <Mono>{count(one.downloads)} pulls</Mono>
                <Mono>{one.updated}</Mono>
              </button>
            </li>
          ))}
        </ul>
      )}

      {found && !found.token && found.results.some((one) => one.gated) && (
        <p className="text-[11px] text-warning">
          A gated model needs its licence accepted on Hugging Face and a token in{" "}
          <Mono>{found.token_env}</Mono>. Without one, Auger can only reach somebody
          else&apos;s copy of those weights.
        </p>
      )}

      {chosen && files && (
        <div className="rounded-md border border-border-subtle p-3">
          <p className="mb-2 text-xs text-text-secondary">
            {chosen.id} · this machine can hold about {files.usable_memory_gb} GB
          </p>
          {files.files.length === 0 && (
            <p className="text-xs text-text-tertiary">
              Nothing here is one loadable file. It is probably split across shards.
            </p>
          )}
          <ul className="space-y-1">
            {files.files.map((one) => (
              <li key={one.name} className="flex items-center gap-2">
                <Mono>{one.name}</Mono>
                <span className="text-[11px] text-text-tertiary">{one.gigabytes} GB</span>
                {!one.fits && <span className="text-[11px] text-warning">too large</span>}
                {one.downloaded && <span className="text-[11px] text-success">on disk</span>}
                <Button
                  size="sm"
                  variant="ghost"
                  className="ml-auto"
                  disabled={busy !== "" || !one.fits}
                  onClick={() => void take(one.name)}
                >
                  {busy === "fetch" ? "Fetching" : one.downloaded ? "Use" : "Fetch"}
                </Button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
