/** What the rig is saying to the models, as it happens.
 *
 * A reviewer that runs all day is otherwise invisible: findings appear, and nothing
 * says what was asked or what came back. This is the window into the work.
 */

import { Badge, Button } from "@metaphor-cloud/ui";
import { useCallback, useEffect, useRef, useState } from "react";

import { getTranscript } from "../engine";
import type { Turn } from "../types";
import { Mono } from "../ui";

const JOB_COLOUR: Record<string, string> = {
  review: "#4c9df0",
  triage: "#a78bfa",
  embed: "#22d3ee",
  rerank: "#4ade80",
};

function clock(at: number) {
  return new Date(at * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function Exchange({ turn }: { turn: Turn }) {
  const [open, setOpen] = useState(false);
  const colour = JOB_COLOUR[turn.job_class] ?? "var(--color-text-tertiary)";
  return (
    <li className="border-b border-border-subtle">
      <button
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-baseline gap-2.5 px-4 py-1.5 text-left transition-colors hover:bg-bg-card-hover"
      >
        <Mono>{clock(turn.at)}</Mono>
        <span className="w-14 shrink-0 text-[10px] uppercase tracking-wider" style={{ color: colour }}>
          {turn.job_class}
        </span>
        <span className="min-w-0 flex-1 truncate text-xs text-text-secondary">
          {turn.error ? turn.error : turn.answer.replace(/\s+/g, " ").slice(0, 160) || "no answer"}
        </span>
        {turn.repo && <span className="shrink-0 text-[10px] text-text-tertiary">{turn.repo}</span>}
        {turn.error && <Badge variant="danger">failed</Badge>}
        <span className="w-24 shrink-0 text-right text-[10px] tabular-nums text-text-tertiary">
          {turn.prompt_tokens + turn.completion_tokens > 0
            ? `${turn.prompt_tokens}→${turn.completion_tokens}`
            : ""}
        </span>
        <span className="w-12 shrink-0 text-right text-[10px] tabular-nums text-text-tertiary">
          {(turn.duration_ms / 1000).toFixed(1)}s
        </span>
      </button>
      {open && (
        <div className="grid gap-3 px-4 pb-4 lg:grid-cols-2">
          <div>
            <p className="mb-1 text-[10px] uppercase tracking-wider text-text-tertiary">
              Asked · {turn.backend} · {turn.model}
              {turn.clipped ? " · clipped" : ""}
            </p>
            <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded border border-border-subtle bg-bg p-2 font-mono text-[11px] leading-relaxed text-text-secondary">
              {turn.prompt}
            </pre>
          </div>
          <div>
            <p className="mb-1 text-[10px] uppercase tracking-wider text-text-tertiary">Answered</p>
            <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded border border-border-subtle bg-bg p-2 font-mono text-[11px] leading-relaxed text-text-primary">
              {turn.error ?? turn.answer}
            </pre>
          </div>
        </div>
      )}
    </li>
  );
}

export default function TranscriptView({ version }: { version: number }) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [follow, setFollow] = useState(true);
  const [depth, setDepth] = useState(0);
  const foot = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    const body = await getTranscript(0, 120);
    setTurns(body.turns);
    setDepth(body.depth);
  }, []);

  useEffect(() => {
    void load();
  }, [load, version]);

  // The transcript moves while a review runs, and no run event marks a single
  // exchange, so this view asks for itself.
  useEffect(() => {
    if (!follow) return;
    const timer = setInterval(() => void load(), 2000);
    return () => clearInterval(timer);
  }, [follow, load]);

  useEffect(() => {
    if (follow) foot.current?.scrollIntoView({ block: "end" });
  }, [turns, follow]);

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center gap-3 border-b border-border px-4 py-2">
        <span className="text-xs text-text-secondary">
          {depth === 0 ? "Nothing yet" : `${depth} exchanges held`}
        </span>
        <span className="text-[11px] text-text-tertiary">
          In memory only. It holds your code, so it never reaches the disk.
        </span>
        <div className="ml-auto flex gap-1">
          <Button size="sm" variant="ghost" onClick={() => void load()}>
            Refresh
          </Button>
          <Button
            size="sm"
            variant={follow ? "secondary" : "ghost"}
            onClick={() => setFollow((value) => !value)}
          >
            {follow ? "Following" : "Follow"}
          </Button>
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-auto">
        {turns.length === 0 && (
          <p className="px-4 py-8 text-center text-xs text-text-tertiary">
            Nothing has been asked yet. Press Start reviewing, and every exchange appears
            here as it happens.
          </p>
        )}
        <ul>
          {turns.map((turn) => (
            <Exchange key={turn.id} turn={turn} />
          ))}
        </ul>
        <div ref={foot} />
      </div>
    </div>
  );
}
