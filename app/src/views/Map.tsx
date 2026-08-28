/** One surface for the repositories, what was found in them, and what ran.
 *
 * The four lists this replaced showed four slices of one thing. A person asks "what is
 * wrong, and where", which is a shape, not four tables.
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
import {
  Background,
  BackgroundVariant,
  Controls,
  ReactFlow,
  type Edge,
  type Node,
  useEdgesState,
  useNodesState,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  addNote,
  getFindings,
  getNotes,
  getRepositories,
  getRuns,
  markOpened,
  recordItem,
  requestReview,
  setFindingStatus,
} from "../engine";
import { layout } from "../map/layout";
import { NODE_TYPES } from "../map/nodes";
import { CATEGORY, SEVERITY_RANK, categoryOf, severityOf, STATES } from "../map/palette";
import type { Finding, Note, Repository, Run } from "../types";
import { Mono } from "../ui";

/** How many findings one repository shows before the rest are counted, not drawn. */
const PER_REPO = 30;
const RUN_STRIP = 10;

const CATEGORY_NAMES = Object.keys(CATEGORY);
const STATE_NAMES = ["open", "doing", "resolved", "suppressed"];

function repoName(path: string) {
  return path.split("/").slice(-2).join("/");
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
              That work was already recorded. Its item is on the map.
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

export default function MapView({
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
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [categories, setCategories] = useState<Set<string>>(new Set(CATEGORY_NAMES));
  const [states, setStates] = useState<Set<string>>(new Set(["open", "doing"]));
  const [dismissed, setDismissed] = useState(false);
  const [chosen, setChosen] = useState<Finding | null>(null);
  const [recording, setRecording] = useState(false);
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);

  const load = useCallback(async () => {
    const [repoBody, findingBody, runBody] = await Promise.all([
      getRepositories(),
      getFindings(undefined, STATE_NAMES.join(","), true),
      getRuns(),
    ]);
    setRepositories(repoBody.repositories);
    setFindings(findingBody.findings);
    setRuns(runBody.runs.slice(0, RUN_STRIP));
    onCounts(
      findingBody.counts.total ?? 0,
      (findingBody.counts.critical ?? 0) + (findingBody.counts.high ?? 0),
    );
  }, [onCounts]);

  useEffect(() => {
    void load();
  }, [load, version]);

  const shown = useMemo(
    () =>
      findings.filter(
        (one) =>
          categories.has(one.category) &&
          states.has(one.status) &&
          (dismissed || one.triage !== "false"),
      ),
    [findings, categories, states, dismissed],
  );

  useEffect(() => {
    const byRepo = new Map<string, Finding[]>();
    for (const one of shown) {
      const list = byRepo.get(one.repo_path) ?? [];
      list.push(one);
      byRepo.set(one.repo_path, list);
    }

    const built: Node[] = [];
    const links: Edge[] = [];
    for (const repository of repositories) {
      const mine = (byRepo.get(repository.path) ?? []).sort(
        (a, b) => (SEVERITY_RANK[a.severity] ?? 5) - (SEVERITY_RANK[b.severity] ?? 5),
      );
      const open = expanded.has(repository.path);
      built.push({
        id: repository.path,
        type: "repo",
        position: { x: 0, y: 0 },
        data: {
          name: repoName(repository.path),
          path: repository.path,
          open: mine.length,
          worst: mine[0]?.severity ?? "info",
          expanded: open,
          enabled: repository.policy.enabled,
          unread: mine.filter((one) => one.opened_at === null).length,
        },
      });
      if (!open) continue;
      for (const finding of mine.slice(0, PER_REPO)) {
        built.push({
          id: finding.fingerprint,
          type: "finding",
          position: { x: 0, y: 0 },
          data: {
            title: finding.title,
            severity: finding.severity,
            category: finding.category,
            status: finding.status,
            file: finding.line ? `${finding.file}:${finding.line}` : finding.file,
            unread: finding.opened_at === null,
            notes: 0,
          },
        });
        links.push({
          id: `${repository.path}->${finding.fingerprint}`,
          source: repository.path,
          target: finding.fingerprint,
          animated: finding.opened_at === null,
          style: { stroke: severityOf(finding.severity).colour, strokeWidth: 1.2, opacity: 0.5 },
        });
      }
      if (mine.length > PER_REPO) {
        const id = `${repository.path}#more`;
        built.push({
          id,
          type: "finding",
          position: { x: 0, y: 0 },
          data: {
            title: `${mine.length - PER_REPO} more, not drawn`,
            severity: "info",
            category: "quality",
            status: "open",
            file: "narrow the filters to see them",
            unread: false,
            notes: 0,
          },
        });
        links.push({
          id: `${repository.path}->more`,
          source: repository.path,
          target: id,
          style: { stroke: "var(--color-border)", strokeWidth: 1, opacity: 0.4 },
        });
      }
    }
    setNodes(layout(built, links));
    setEdges(links);
  }, [repositories, shown, expanded, setNodes, setEdges]);

  async function onNodeClick(_: unknown, node: Node) {
    if (node.type === "repo") {
      setExpanded((current) => {
        const next = new Set(current);
        if (next.has(node.id)) next.delete(node.id);
        else next.add(node.id);
        return next;
      });
      return;
    }
    const finding = findings.find((one) => one.fingerprint === node.id);
    if (!finding) return;
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

  async function changeState(finding: Finding, state: string) {
    await setFindingStatus([finding.fingerprint], state as "open");
    setChosen({ ...finding, status: state });
    await load();
  }

  const unread = findings.filter((one) => one.opened_at === null).length;
  const run = chosen ? runs.find((one) => one.id === chosen.run_id) : undefined;

  return (
    <div className="flex h-full flex-col">
      <header className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-2">
        <span className="text-xs text-text-secondary">
          {repositories.length} repositories · {shown.length} shown
          {unread > 0 ? ` · ${unread} new` : ""}
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
        <div className="ml-auto flex gap-1">
          <Button size="sm" variant="secondary" onClick={() => setRecording(true)}>
            Record
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setExpanded(new Set(repositories.map((one) => one.path)))}
          >
            Expand all
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setExpanded(new Set())}>
            Collapse
          </Button>
        </div>
      </header>

      <div className="relative min-h-0 flex-1">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onNodeClick={(event, node) => void onNodeClick(event, node)}
          nodeTypes={NODE_TYPES}
          fitView
          minZoom={0.15}
          proOptions={{ hideAttribution: true }}
        >
          <Background variant={BackgroundVariant.Dots} gap={22} size={1} color="var(--color-border-subtle)" />
          <Controls showInteractive={false} className="!bg-bg-card !shadow-none" />
        </ReactFlow>
        {repositories.length === 0 && (
          <div className="pointer-events-none absolute inset-0 grid place-items-center">
            <p className="text-xs text-text-secondary">
              No repository yet. Add a root in Settings.
            </p>
          </div>
        )}
      </div>

      <footer className="border-t border-border bg-bg-elevated px-4 py-2">
        <div className="flex items-center gap-2">
          <span className="text-[11px] uppercase tracking-wider text-text-tertiary">Recent runs</span>
          <div className="flex min-w-0 flex-1 gap-1 overflow-x-auto">
            {runs.map((one) => (
              <span
                key={one.id}
                title={`${repoName(one.repo_path)} · ${one.kind} · ${one.reason ?? one.status}`}
                className="flex shrink-0 items-center gap-1.5 rounded border border-border-subtle px-2 py-0.5 text-[11px]"
              >
                <span
                  className="h-1.5 w-1.5 rounded-full"
                  style={{
                    background:
                      one.status === "ok"
                        ? "#4ade80"
                        : one.status === "failed"
                          ? "#f43f5e"
                          : "#64748b",
                  }}
                />
                <span className="text-text-secondary">{repoName(one.repo_path)}</span>
                <Mono>{one.started_at.replace("T", " ").slice(11, 16)}</Mono>
              </span>
            ))}
            {runs.length === 0 && <span className="text-[11px] text-text-tertiary">Nothing yet</span>}
          </div>
          <Button size="sm" variant="ghost" onClick={onOpenRuns}>
            All runs
          </Button>
        </div>
      </footer>

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
                  <Badge
                    variant="outline"
                    style={{ color: severityOf(chosen.severity).colour }}
                  >
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
                    <p>
                      <Mono>
                        {run.kind} · {run.started_at.replace("T", " ").slice(0, 16)} ·{" "}
                        {run.status}
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
