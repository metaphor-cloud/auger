import {
  Alert,
  AlertDescription,
  Badge,
  Button,
  EmptyState,
  Input,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Textarea,
} from "@metaphor-cloud/ui";
import { useCallback, useEffect, useState } from "react";

import {
  addNote,
  getFindings,
  getNotes,
  getRepositories,
  recordItem,
  setFindingStatus,
} from "../engine";
import type { Finding, Note, Repository } from "../types";
import { Mono, PageTitle, SeverityBadge } from "../ui";

const ORDER = ["critical", "high", "medium", "low", "info"];

/** What the tracker calls a state, and what the store calls it. */
const STATES = [
  { value: "open", label: "Open" },
  { value: "doing", label: "Doing" },
  { value: "resolved", label: "Done" },
  { value: "suppressed", label: "Dropped" },
] as const;

type State = (typeof STATES)[number]["value"];

function fileLabel(finding: Finding) {
  if (!finding.file) return "";
  return finding.line ? `${finding.file}:${finding.line}` : finding.file;
}

function repoLabel(path: string) {
  return path.split("/").slice(-2).join("/");
}

function Journal({ fingerprint }: { fingerprint: string }) {
  const [notes, setNotes] = useState<Note[] | null>(null);
  const [draft, setDraft] = useState("");

  useEffect(() => {
    void getNotes(fingerprint).then((body) => setNotes(body.notes));
  }, [fingerprint]);

  async function append() {
    const body = await addNote(fingerprint, draft);
    setNotes(body.notes);
    setDraft("");
  }

  return (
    <div className="mt-3">
      {notes && notes.length > 0 && (
        <ul className="mb-2 space-y-1 border-l border-border-subtle pl-3">
          {notes.map((note) => (
            <li key={note.id}>
              <Mono>
                {note.written_at.replace("T", " ").slice(5, 16)} · {note.author}
              </Mono>{" "}
              <span className="text-text-primary">{note.text}</span>
            </li>
          ))}
        </ul>
      )}
      <div className="flex items-start gap-2">
        <Textarea
          rows={2}
          className="text-xs"
          value={draft}
          placeholder="What happened. The next session reads this."
          onChange={(event) => setDraft(event.target.value)}
        />
        <Button size="sm" variant="secondary" disabled={!draft.trim()} onClick={() => void append()}>
          Add note
        </Button>
      </div>
    </div>
  );
}

function RecordForm({
  repositories,
  onRecorded,
}: {
  repositories: Repository[];
  onRecorded: (existed: boolean) => void;
}) {
  const [repo, setRepo] = useState(repositories[0]?.path ?? "");
  const [title, setTitle] = useState("");
  const [detail, setDetail] = useState("");

  async function save() {
    const body = await recordItem({ repo_path: repo, title, detail });
    setTitle("");
    setDetail("");
    onRecorded(body.existed);
  }

  return (
    <div className="mb-4 space-y-2 rounded-md border border-border-subtle p-3">
      <div className="flex items-center gap-2">
        <Select value={repo} onValueChange={setRepo}>
          <SelectTrigger className="w-64">
            <SelectValue placeholder="Repository" />
          </SelectTrigger>
          <SelectContent>
            {repositories.map((one) => (
              <SelectItem key={one.path} value={one.path}>
                {repoLabel(one.path)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Input
          value={title}
          placeholder="What needs doing"
          onChange={(event) => setTitle(event.target.value)}
        />
        <Button disabled={!repo || !title.trim()} onClick={() => void save()}>
          Record
        </Button>
      </div>
      <Textarea
        rows={2}
        className="text-xs"
        value={detail}
        placeholder="Detail, so a session that finds this later knows what you meant."
        onChange={(event) => setDetail(event.target.value)}
      />
    </div>
  );
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
  const [repositories, setRepositories] = useState<Repository[]>([]);
  const [showClosed, setShowClosed] = useState(false);
  const [showDismissed, setShowDismissed] = useState(false);
  const [recording, setRecording] = useState(false);
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const body = await getFindings(
        undefined,
        showClosed ? "resolved,suppressed" : "open,doing",
        showDismissed,
        search,
      );
      setFindings(body.findings);
      setCounts(body.counts);
      onCounts(body.counts.total ?? 0, (body.counts.critical ?? 0) + (body.counts.high ?? 0));
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, [showClosed, showDismissed, search, onCounts]);

  useEffect(() => {
    void load();
  }, [load, version]);

  useEffect(() => {
    void getRepositories().then((body) => setRepositories(body.repositories));
  }, [version]);

  async function changeState(finding: Finding, state: State) {
    await setFindingStatus([finding.fingerprint], state);
    await load();
  }

  const summary =
    ORDER.filter((name) => counts[name])
      .map((name) => `${counts[name]} ${name}`)
      .join(", ") || "Nothing open";

  return (
    <>
      <PageTitle
        title={showClosed ? "Closed" : "Work"}
        description={`${summary}. A review writes here, and so does an agent.`}
      >
        <Input
          className="w-56"
          value={search}
          placeholder="Search"
          onChange={(event) => setSearch(event.target.value)}
        />
        <Button size="sm" variant="secondary" onClick={() => setRecording((value) => !value)}>
          {recording ? "Close" : "Record"}
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setShowDismissed((value) => !value)}>
          {showDismissed ? "Hide dismissed" : "Show dismissed"}
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setShowClosed((value) => !value)}>
          {showClosed ? "Show open" : "Show closed"}
        </Button>
      </PageTitle>

      {error && (
        <Alert variant="danger" className="mb-3">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {message && (
        <Alert variant="warning" className="mb-3">
          <AlertDescription>{message}</AlertDescription>
        </Alert>
      )}

      {recording && (
        <RecordForm
          repositories={repositories}
          onRecorded={(existed) => {
            setMessage(existed ? "That work was already recorded. Its item is in the list." : null);
            void load();
          }}
        />
      )}

      {findings === null && <p className="text-xs text-text-secondary">Loading</p>}
      {findings !== null && findings.length === 0 && (
        <EmptyState
          title={search ? "Nothing matches" : showClosed ? "Nothing closed" : "Nothing open"}
          description={
            search
              ? "Search covers the title and the detail of every item."
              : "The rig reports here as it reviews. Record your own work with the Record button, or from an agent through the tracker."
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
              {finding.status === "doing" && <Badge variant="default">doing</Badge>}
              <span className="min-w-0 flex-1 truncate">{finding.title}</span>
              {finding.file && <Mono>{fileLabel(finding)}</Mono>}
              <span className="text-xs text-text-tertiary">{repoLabel(finding.repo_path)}</span>
              {finding.source !== "model" && <Badge variant="outline">{finding.source}</Badge>}
              {finding.triage === "false" && <Badge variant="outline">dismissed</Badge>}
              {finding.times_seen > 1 && (
                <span className="text-xs text-text-tertiary">seen {finding.times_seen}×</span>
              )}
            </button>
            {open === finding.fingerprint && (
              <div className="max-w-3xl px-1 pb-4 text-xs leading-relaxed">
                {finding.detail && <p className="mb-2 text-text-primary">{finding.detail}</p>}
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
                <div className="flex gap-1">
                  {STATES.map((state) => (
                    <Button
                      key={state.value}
                      size="sm"
                      variant={finding.status === state.value ? "secondary" : "ghost"}
                      onClick={() => void changeState(finding, state.value)}
                    >
                      {state.label}
                    </Button>
                  ))}
                </div>
                <Journal fingerprint={finding.fingerprint} />
              </div>
            )}
          </li>
        ))}
      </ul>
    </>
  );
}
