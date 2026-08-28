/** What needs attention, and what the rig has been doing.
 *
 * A rank is the answer to "what do I act on now", so the list is the surface and the
 * one picture sits above it, on the axis where the data actually varies: time.
 */

import {
  Badge,
  Button,
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  Input,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  Textarea,
} from "@metaphor-cloud/ui";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  addNote,
  changeExclusion,
  getFindings,
  getNotes,
  getRepositories,
  getRuns,
  markOpened,
  recordItem,
  requestReview,
  setFindingStatus,
} from "../engine";
import { CATEGORY, SEVERITY_RANK, STATES, categoryOf, severityOf } from "../palette";
import Timeline from "../parts/Timeline";
import type { Finding, Note, Repository, Run } from "../types";
import { Mono } from "../ui";

/** How many findings one repository lists before the rest are counted, not drawn. */
const PER_REPO = 50;
const RUN_LIMIT = 400;

const CATEGORY_NAMES = Object.keys(CATEGORY);
const STATE_NAMES = ["open", "doing", "resolved", "suppressed"];

function repoName(path: string) {
  return path.split("/").slice(-2).join("/");
}

/** How long it has been there, in the shortest form that is still true. */
function age(stamp: string) {
  const at = Date.parse(/(?:Z|[+-]\d\d:\d\d)$/.test(stamp) ? stamp : `${stamp}Z`);
  if (Number.isNaN(at)) return "";
  const minutes = Math.max(0, Math.round((Date.now() - at) / 60_000));
  if (minutes < 60) return `${minutes}m`;
  if (minutes < 60 * 24) return `${Math.round(minutes / 60)}h`;
  return `${Math.round(minutes / (60 * 24))}d`;
}

function Chip({
  on,
  colour,
  children,
  onClick,
}: {
  on: boolean;
  colour?: string;
  children: React.ReactNode;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className="rounded-full border px-2.5 py-0.5 text-[11px] transition-colors"
      style={{
        borderColor: on ? (colour ?? "var(--color-accent)") : "var(--color-border-subtle)",
        color: on ? (colour ?? "var(--color-accent)") : "var(--color-text-tertiary)",
        background: on ? `${colour ?? "#4c9df0"}18` : "transparent",
      }}
    >
      {children}
    </button>
  );
}

function Journal({ fingerprint }: { fingerprint: string }) {
  const [notes, setNotes] = useState<Note[]>([]);
  const [draft, setDraft] = useState("");

  useEffect(() => {
    void getNotes(fingerprint).then((body) => setNotes(body.notes));
  }, [fingerprint]);

  return (
    <div>
      <p className="mb-1 text-[11px] uppercase tracking-wider text-text-tertiary">Journal</p>
      {notes.length === 0 && <p className="text-xs text-text-tertiary">Nothing written yet.</p>}
      <ul className="space-y-1 border-l border-border-subtle pl-3">
        {notes.map((note) => (
          <li key={note.id} className="text-xs">
            <Mono>
              {note.written_at.replace("T", " ").slice(5, 16)} · {note.author}
            </Mono>{" "}
            {note.text}
          </li>
        ))}
      </ul>
      <div className="mt-2 flex gap-2">
        <Input
          value={draft}
          placeholder="What happened"
          onChange={(event) => setDraft(event.target.value)}
        />
        <Button
          size="sm"
          variant="secondary"
          disabled={!draft.trim()}
          onClick={() =>
            void addNote(fingerprint, draft).then((body) => {
              setNotes(body.notes);
              setDraft("");
            })
          }
        >
          Add
        </Button>
      </div>
    </div>
  );
}

function RecordDialog({
  repositories,
  open,
  onClose,
}: {
  repositories: Repository[];
  open: boolean;
  onClose: (recorded: boolean) => void;
}) {
  const [repo, setRepo] = useState("");
  const [title, setTitle] = useState("");
  const [detail, setDetail] = useState("");
  const [existed, setExisted] = useState(false);

  async function save() {
    const body = await recordItem({ repo_path: repo, title, detail });
    setExisted(body.existed);
    setTitle("");
    setDetail("");
    onClose(true);
  }

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose(false)}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="text-sm">Record an item</DialogTitle>
        </DialogHeader>
        <div className="space-y-2 px-4 pb-4">
          {existed && (
            <p className="text-xs text-warning">
              That work was already recorded. Its item is in the list.
            </p>
          )}
          <Select value={repo} onValueChange={setRepo}>
            <SelectTrigger>
              <SelectValue placeholder="Repository" />
            </SelectTrigger>
            <SelectContent>
              {repositories.map((one) => (
                <SelectItem key={one.path} value={one.path}>
                  {repoName(one.path)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Input
            value={title}
            placeholder="What needs doing"
            onChange={(event) => setTitle(event.target.value)}
          />
          <Textarea
            rows={3}
            value={detail}
            placeholder="Detail, so a session that finds this later knows what you meant."
            onChange={(event) => setDetail(event.target.value)}
          />
          <Button disabled={!repo || !title.trim()} onClick={() => void save()}>
            Record
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function Row({
  finding,
  chosen,
  onPick,
}: {
  finding: Finding;
  chosen: boolean;
  onPick: () => void;
}) {
  const severity = severityOf(finding.severity);
  const category = categoryOf(finding.category);
  const closed = finding.status === "resolved" || finding.status === "suppressed";
  return (
    <button
      onClick={onPick}
      className={`flex w-full items-center gap-2.5 border-l-2 py-1.5 pl-3 pr-2 text-left transition-colors ${
        chosen ? "bg-bg-card-hover" : "hover:bg-bg-card-hover"
      }`}
      style={{ borderColor: severity.colour, opacity: closed ? 0.5 : 1 }}
    >
      <span
        className={`h-1.5 w-1.5 shrink-0 rounded-full ${finding.opened_at === null ? "rr-breathe" : ""}`}
        style={{
          background: finding.opened_at === null ? "var(--color-accent)" : "transparent",
        }}
      />
      <span
        className="w-8 shrink-0 font-mono text-[9px] tracking-wider"
        style={{ color: category.colour }}
        title={category.label}
      >
        {category.tag}
      </span>
      <span className="min-w-0 flex-1 truncate text-xs text-text-primary">{finding.title}</span>
      {finding.status === "doing" && <Badge variant="default">doing</Badge>}
      {finding.file && (
        <Mono>
          {finding.line ? `${finding.file}:${finding.line}` : finding.file}
        </Mono>
      )}
      {finding.source !== "model" && (
        <span className="shrink-0 text-[10px] text-text-tertiary">{finding.source}</span>
      )}
      <span className="w-8 shrink-0 text-right text-[10px] tabular-nums text-text-tertiary">
        {age(finding.last_seen_at)}
      </span>
    </button>
  );
}

export default function Work({
  version,
  onCounts,
  onOpenRuns,
}: {
  version: number;
  onCounts: (open: number, critical: number) => void;
  onOpenRuns: () => void;
}) {
  const [repositories, setRepositories] = useState<Repository[]>([]);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [categories, setCategories] = useState<Set<string>>(new Set(CATEGORY_NAMES));
  const [states, setStates] = useState<Set<string>>(new Set(["open", "doing"]));
  const [dismissed, setDismissed] = useState(false);
  const [hours, setHours] = useState(0.5);
  const [readAt, setReadAt] = useState(() => Date.now());
  const [search, setSearch] = useState("");
  const [chosen, setChosen] = useState<Finding | null>(null);
  const [recording, setRecording] = useState(false);
  const [closed, setClosed] = useState<Set<string>>(new Set());

  const load = useCallback(async () => {
    const [repoBody, findingBody, runBody] = await Promise.all([
      getRepositories(),
      getFindings(undefined, STATE_NAMES.join(","), true),
      getRuns(undefined, RUN_LIMIT),
    ]);
    setRepositories(repoBody.repositories);
    setFindings(findingBody.findings);
    setRuns(runBody.runs);
    setReadAt(Date.now());
    onCounts(
      findingBody.counts.total ?? 0,
      (findingBody.counts.critical ?? 0) + (findingBody.counts.high ?? 0),
    );
  }, [onCounts]);

  useEffect(() => {
    void load();
  }, [load, version]);

  const shown = useMemo(() => {
    const wanted = search.trim().toLowerCase();
    return findings.filter(
      (one) =>
        categories.has(one.category) &&
        states.has(one.status) &&
        (dismissed || one.triage !== "false") &&
        (wanted === "" ||
          one.title.toLowerCase().includes(wanted) ||
          one.detail.toLowerCase().includes(wanted) ||
          one.file.toLowerCase().includes(wanted)),
    );
  }, [findings, categories, states, dismissed, search]);

  /** Grouped by repository, worst first, and inside a group the same rule again. */
  const groups = useMemo(() => {
    // An excluded repository is one the user has said they do not want to see. Its
    // findings stay in the database, and they stay out of this list.
    const watched = new Set(
      repositories.filter((one) => one.policy.enabled).map((one) => one.path),
    );
    const byRepo = new Map<string, Finding[]>();
    for (const one of shown) {
      if (!watched.has(one.repo_path)) continue;
      const list = byRepo.get(one.repo_path) ?? [];
      list.push(one);
      byRepo.set(one.repo_path, list);
    }
    const rank = (finding: Finding) => SEVERITY_RANK[finding.severity] ?? 5;
    return [...byRepo.entries()]
      .map(([path, list]) => ({
        path,
        name: repoName(path),
        findings: list.sort(
          (first, second) =>
            rank(first) - rank(second) ||
            Number(first.opened_at !== null) - Number(second.opened_at !== null) ||
            second.last_seen_at.localeCompare(first.last_seen_at),
        ),
        worst: Math.min(...list.map(rank)),
        unread: list.filter((one) => one.opened_at === null).length,
      }))
      .sort(
        (first, second) =>
          first.worst - second.worst ||
          second.findings.length - first.findings.length ||
          first.name.localeCompare(second.name),
      );
  }, [shown, repositories]);

  async function pick(finding: Finding) {
    setChosen(finding);
    if (finding.opened_at === null) {
      await markOpened(finding.fingerprint);
      setFindings((current) =>
        current.map((one) =>
          one.fingerprint === finding.fingerprint
            ? { ...one, opened_at: new Date().toISOString() }
            : one,
        ),
      );
    }
  }

  async function exclude(path: string) {
    await changeExclusion(path, false);
    await load();
  }

  async function changeState(finding: Finding, state: string) {
    await setFindingStatus([finding.fingerprint], state as "open");
    setChosen({ ...finding, status: state });
    await load();
  }

  const unread = shown.filter((one) => one.opened_at === null).length;
  const run = chosen ? runs.find((one) => one.id === chosen.run_id) : undefined;
  const quiet = repositories.length - groups.length;

  return (
    <div className="flex h-full flex-col">
      <Timeline
        runs={runs.map((one) => ({ at: one.started_at, status: one.status }))}
        findings={findings.map((one) => ({
          at: one.first_seen_at,
          severity: one.severity,
        }))}
        now={readAt}
        hours={hours}
        onHours={setHours}
        onOpenRuns={onOpenRuns}
      />

      <header className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-2">
        <span className="text-xs text-text-secondary">
          {shown.length} open in {groups.length} repositories
          {unread > 0 ? ` · ${unread} new` : ""}
          {quiet > 0 ? ` · ${quiet} clear` : ""}
        </span>
        <span className="mx-1 h-4 w-px bg-border" />
        {CATEGORY_NAMES.map((name) => (
          <Chip
            key={name}
            on={categories.has(name)}
            colour={CATEGORY[name].colour}
            onClick={() =>
              setCategories((current) => {
                const next = new Set(current);
                if (next.has(name)) next.delete(name);
                else next.add(name);
                return next;
              })
            }
          >
            {CATEGORY[name].label}
          </Chip>
        ))}
        <span className="mx-1 h-4 w-px bg-border" />
        {STATE_NAMES.map((name) => (
          <Chip
            key={name}
            on={states.has(name)}
            onClick={() =>
              setStates((current) => {
                const next = new Set(current);
                if (next.has(name)) next.delete(name);
                else next.add(name);
                return next;
              })
            }
          >
            {STATES[name]}
          </Chip>
        ))}
        <Chip on={dismissed} onClick={() => setDismissed((value) => !value)}>
          Dismissed
        </Chip>
        <div className="ml-auto flex items-center gap-2">
          <Input
            className="h-7 w-44"
            value={search}
            placeholder="Filter"
            onChange={(event) => setSearch(event.target.value)}
          />
          <Button size="sm" variant="secondary" onClick={() => setRecording(true)}>
            Record
          </Button>
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-auto">
        {groups.length === 0 && (
          <p className="px-4 py-8 text-center text-xs text-text-tertiary">
            {repositories.length === 0
              ? "No repository yet. Add a root in Settings."
              : "Nothing open under these filters."}
          </p>
        )}
        {groups.map((group) => (
          <section key={group.path}>
            <header className="sticky top-0 z-10 flex items-baseline gap-2 border-b border-border-subtle bg-bg px-4 py-1.5">
              <button
                className="flex items-baseline gap-2 text-left"
                onClick={() =>
                  setClosed((current) => {
                    const next = new Set(current);
                    if (next.has(group.path)) next.delete(group.path);
                    else next.add(group.path);
                    return next;
                  })
                }
              >
                <span className="w-2 text-[10px] text-text-tertiary">
                  {closed.has(group.path) ? "▸" : "▾"}
                </span>
                <span className="text-xs font-medium text-text-primary">{group.name}</span>
                <span className="text-[11px] text-text-tertiary">
                  {group.findings.length}
                  {group.unread > 0 ? ` · ${group.unread} new` : ""}
                </span>
              </button>
              <Button
                size="sm"
                variant="ghost"
                className="ml-auto h-6"
                onClick={() => void requestReview(group.path)}
              >
                Review now
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="h-6 text-text-tertiary"
                title="Never review this repository. It stays listed in Settings."
                onClick={() => void exclude(group.path)}
              >
                Exclude
              </Button>
            </header>
            <ul
              className={`divide-y divide-border-subtle/60 ${
                closed.has(group.path) ? "hidden" : ""
              }`}
            >
              {group.findings.slice(0, PER_REPO).map((finding) => (
                <li key={finding.fingerprint}>
                  <Row
                    finding={finding}
                    chosen={chosen?.fingerprint === finding.fingerprint}
                    onPick={() => void pick(finding)}
                  />
                </li>
              ))}
              {group.findings.length > PER_REPO && (
                <li className="px-4 py-1.5 text-[11px] text-text-tertiary">
                  {group.findings.length - PER_REPO} more, not listed. Narrow the filters.
                </li>
              )}
            </ul>
          </section>
        ))}
      </div>

      <RecordDialog
        repositories={repositories}
        open={recording}
        onClose={(recorded) => {
          setRecording(false);
          if (recorded) void load();
        }}
      />

      <Sheet open={chosen !== null} onOpenChange={(open) => !open && setChosen(null)}>
        <SheetContent className="w-[30rem] overflow-y-auto">
          {chosen && (
            <>
              <SheetHeader>
                <SheetTitle className="text-sm">{chosen.title}</SheetTitle>
              </SheetHeader>
              <div className="space-y-4 px-4 pb-6 text-xs">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="outline" style={{ color: severityOf(chosen.severity).colour }}>
                    {chosen.severity}
                  </Badge>
                  <Badge variant="outline" style={{ color: categoryOf(chosen.category).colour }}>
                    {categoryOf(chosen.category).label}
                  </Badge>
                  <Badge variant="outline">{chosen.source}</Badge>
                  <Mono>{repoName(chosen.repo_path)}</Mono>
                </div>
                {chosen.file && (
                  <Mono>{chosen.line ? `${chosen.file}:${chosen.line}` : chosen.file}</Mono>
                )}
                {chosen.detail && <p className="text-text-primary">{chosen.detail}</p>}
                {chosen.suggestion && (
                  <p className="text-text-primary">
                    <span className="font-medium">Fix:</span> {chosen.suggestion}
                  </p>
                )}
                <div className="flex flex-wrap gap-1">
                  {Object.entries(STATES).map(([value, label]) => (
                    <Button
                      key={value}
                      size="sm"
                      variant={chosen.status === value ? "secondary" : "ghost"}
                      onClick={() => void changeState(chosen, value)}
                    >
                      {label}
                    </Button>
                  ))}
                </div>
                <Journal fingerprint={chosen.fingerprint} />
                <div className="border-t border-border-subtle pt-3">
                  <p className="mb-1 text-[11px] uppercase tracking-wider text-text-tertiary">
                    The run that found it
                  </p>
                  {run ? (
                    <p className="flex items-center gap-2">
                      <Mono>
                        {run.kind} · {run.started_at.replace("T", " ").slice(0, 16)} · {run.status}
                      </Mono>
                      <Button size="sm" variant="ghost" onClick={onOpenRuns}>
                        Open runs
                      </Button>
                    </p>
                  ) : (
                    <p className="text-text-tertiary">
                      {chosen.run_id ? (
                        <>
                          <Mono>{chosen.run_id}</Mono>{" "}
                          <Button size="sm" variant="ghost" onClick={onOpenRuns}>
                            Find it in runs
                          </Button>
                        </>
                      ) : (
                        "Recorded by hand, not by a run."
                      )}
                    </p>
                  )}
                </div>
                <div className="border-t border-border-subtle pt-3">
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => void requestReview(chosen.repo_path)}
                  >
                    Review this repository now
                  </Button>
                </div>
              </div>
            </>
          )}
        </SheetContent>
      </Sheet>
    </div>
  );
}
