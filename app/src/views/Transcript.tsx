/** What Auger is saying to the models, as it happens.
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

/** What one line of the transcript says when it is collapsed.
 *
 * A turn that called a tool carries no text, and printing nothing for it reads as a
 * model that failed. It did not: it asked to run something, and that is the most
 * interesting thing on the line.
 */
function summary(turn: Turn): string {
  if (turn.error) return turn.error;
  const said = turn.answer.replace(/\s+/g, " ").trim();
  if (turn.tools.length > 0) {
    const called = toolLine(turn.tools);
    return said ? `${called} — ${said.slice(0, 80)}` : called;
  }
  return said.slice(0, 160) || "thinking, no text";
}

/** What the model asked to run, with what. Every command is `run_command`, so the
 * arguments are the part that says anything. */
function toolLine(tools: string[]): string {
  return "called " + tools.join(" · ");
}

/** What was asked, without repeating a whole review for every step of it.
 *
 * Each turn of a tool loop resends everything before it, so printing the prompt in
 * full prints the same diff again for every command the model ran. The end is the
 * part that is new: the results it just read.
 */
const TAIL = 2500;

function asked(turn: Turn): string {
  if (turn.tools.length === 0 || turn.prompt.length <= TAIL) return turn.prompt;
  return (
    `[the first ${turn.prompt.length - TAIL} characters repeat the turn before this one]\n\n` +
    turn.prompt.slice(-TAIL)
  );
}

/** How close to the bottom still counts as the bottom. A line of the list, near
 * enough, so a pixel of drift does not read as the reader scrolling away. */
const NEAR_BOTTOM = 40;

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
          {summary(turn)}
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
              {asked(turn)}
            </pre>
          </div>
          <div>
            <p className="mb-1 text-[10px] uppercase tracking-wider text-text-tertiary">
              {turn.tools.length > 0 ? "Called" : "Answered"}
            </p>
            <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded border border-border-subtle bg-bg p-2 font-mono text-[11px] leading-relaxed text-text-primary">
              {turn.error ?? turn.answer ?? ""}
              {turn.tools.length > 0 &&
                (turn.answer ? "\n\n" : "") + turn.tools.map((one) => `called ${one}`).join("\n")}
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
  const list = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    const body = await getTranscript(0, 120);
    setTurns(body.turns);
    setDepth(body.depth);
  }, []);

  useEffect(() => {
    void load();
  }, [load, version]);

  // The transcript moves while a review runs, and no run event marks a single
  // exchange, so this view asks for itself. It keeps asking whether or not the view is
  // following: reading an older exchange is not a reason to stop collecting new ones.
  useEffect(() => {
    const timer = setInterval(() => void load(), 2000);
    return () => clearInterval(timer);
  }, [load]);

  useEffect(() => {
    if (follow) foot.current?.scrollIntoView({ block: "end" });
  }, [turns, follow]);

  // Scrolling away from the bottom stops the view following, and scrolling back
  // resumes it. Reading one exchange while a review writes the next should not be a
  // fight with the scrollbar, and having to reach for a button first is the same
  // fight with an extra step.
  const onScroll = useCallback(() => {
    const element = list.current;
    if (!element) return;
    const room = element.scrollHeight - element.scrollTop - element.clientHeight;
    setFollow((was) => (was === room < NEAR_BOTTOM ? was : room < NEAR_BOTTOM));
  }, []);

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
            variant={follow ? "ghost" : "secondary"}
            onClick={() => {
              setFollow(true);
              foot.current?.scrollIntoView({ block: "end" });
            }}
            disabled={follow}
          >
            {follow ? "Following" : "Jump to latest"}
          </Button>
        </div>
      </header>

      <div ref={list} onScroll={onScroll} className="min-h-0 flex-1 overscroll-none overflow-auto">
        {turns.length === 0 && (
          <p className="px-4 py-8 text-center text-xs text-text-tertiary">
            Nothing has been asked yet. Press Start, and every exchange appears here as
            it happens.
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
