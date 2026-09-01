import { describe, expect, it } from "vitest";

import type { Activity, Run, Step } from "../types";
import { counted, fraction, kindOf, line, phaseOf, rate, reading, resting, since } from "./progress";

const NOW = 1_700_000_000;

function step(over: Partial<Step> = {}): Step {
  return {
    id: 1,
    repo: "/somewhere/alpha",
    slug: "example.com/acme/alpha",
    kind: "diff_review",
    started: NOW - 90,
    phase: "asking",
    phase_started: NOW - 65,
    detail: "",
    done: 0,
    total: 0,
    tokens: 0,
    tokens_started: 0,
    run: "abc123",
    ...over,
  };
}

function activity(over: Partial<Activity> = {}): Activity {
  return { steps: [], pending: 0, paused: false, ready: true, workers: 2, last: null, ...over };
}

function run(over: Partial<Run> = {}): Run {
  return {
    id: "abc123",
    repo_path: "/somewhere/alpha",
    kind: "diff_review",
    status: "ok",
    reason: null,
    base: null,
    head: "HEAD",
    started_at: "2026-01-01T00:00:00",
    finished_at: "2026-01-01T00:00:00",
    duration_ms: 4000,
    finding_count: 0,
    prompt_tokens: 0,
    completion_tokens: 0,
    backend: "review",
    error: null,
    attempts: 1,
    ...over,
  };
}

describe("since", () => {
  it("counts seconds while a person is deciding whether to worry", () => {
    expect(since(NOW - 0, NOW)).toBe("0s");
    expect(since(NOW - 42, NOW)).toBe("42s");
  });

  it("switches to minutes and hours", () => {
    expect(since(NOW - 65, NOW)).toBe("1m 05s");
    expect(since(NOW - 3600 - 120, NOW)).toBe("1h 02m");
  });

  it("never counts backwards from a clock that disagrees", () => {
    expect(since(NOW + 30, NOW)).toBe("0s");
  });
});

describe("phases", () => {
  it("says what a phase is in words", () => {
    expect(phaseOf(step({ phase: "embed" }))).toBe("embedding code");
    expect(kindOf(step())).toBe("review");
  });

  it("shows a phase it has no word for rather than nothing", () => {
    expect(phaseOf(step({ phase: "sharpening" }))).toBe("sharpening");
    expect(kindOf(step({ kind: "something_new" }))).toBe("something_new");
  });
});

describe("fraction", () => {
  it("is null when the phase cannot be counted", () => {
    expect(fraction(step())).toBeNull();
  });

  it("holds inside its bounds when the counts disagree", () => {
    expect(fraction(step({ done: 5, total: 10 }))).toBe(0.5);
    expect(fraction(step({ done: 99, total: 10 }))).toBe(1);
    expect(fraction(step({ done: -3, total: 10 }))).toBe(0);
  });
});

describe("rate", () => {
  it("says nothing until there is enough to say", () => {
    expect(rate(step(), NOW)).toBe("");
    expect(rate(step({ tokens: 4, tokens_started: NOW - 0.4 }), NOW)).toBe("");
  });

  it("is tokens over the time they took", () => {
    expect(rate(step({ tokens: 140, tokens_started: NOW - 10 }), NOW)).toBe("14.0/s");
  });
});

describe("line", () => {
  it("is the phase, the count, and how long it has been going", () => {
    expect(line(step({ tokens: 240, tokens_started: NOW - 20 }), NOW)).toBe(
      "asking the model · 240 tokens · 1m 05s",
    );
  });

  it("names the tool a tool phase is running", () => {
    expect(line(step({ phase: "tool", detail: "run_command", phase_started: NOW - 3 }), NOW)).toBe(
      "running a tool · run_command · 3s",
    );
  });

  it("counts a countable phase", () => {
    const embedding = step({ phase: "embed", done: 320, total: 4000, phase_started: NOW - 30 });
    expect(counted(embedding)).toBe("320/4000");
    expect(line(embedding, NOW)).toBe("embedding code · 320/4000 · 30s");
  });
});

describe("resting", () => {
  it("says stopped when the user stopped it, and how much is waiting", () => {
    expect(resting(activity({ paused: true }), NOW)).toBe("Stopped");
    expect(resting(activity({ paused: true, pending: 4 }), NOW)).toBe("Stopped · 4 waiting");
  });

  it("says a queue is waiting for a worker rather than saying nothing", () => {
    expect(resting(activity({ pending: 7 }), NOW)).toBe("7 waiting for a free worker");
  });

  it("says what finished last when there is nothing to review", () => {
    const at = Date.parse("2026-01-01T00:00:00Z") / 1000;
    const said = resting(
      activity({ last: run({ finished_at: "2026-01-01T00:00:00", finding_count: 3 }) }),
      at + 120,
    );
    expect(said).toBe("Watching · nothing to review · last 2m 00s ago, 3 found");
  });

  it("distinguishes a clean run from a failed one", () => {
    const at = Date.parse("2026-01-01T00:00:00Z") / 1000;
    expect(resting(activity({ last: run() }), at + 5)).toContain("nothing found");
    expect(resting(activity({ last: run({ status: "failed" }) }), at + 5)).toContain("failed");
  });

  it("says it is starting before the workers exist", () => {
    expect(resting(activity({ ready: false }), NOW)).toBe("Starting up");
    expect(resting(null, NOW)).toBe("Connecting");
  });
});

describe("reading the prompt", () => {
  it("is what a rising count with no answer yet means", () => {
    const prompt = step({ phase: "asking", tokens: 0, done: 20_000, total: 46_794 });
    expect(reading(prompt)).toBe(true);
    expect(phaseOf(prompt)).toBe("reading the prompt");
    expect(counted(prompt)).toBe("20000/46794 tokens read");
    expect(fraction(prompt)).toBeCloseTo(0.4274, 3);
  });

  it("gives way to the answer as soon as one token exists", () => {
    const answering = step({ phase: "asking", tokens: 1, done: 0, total: 0 });
    expect(reading(answering)).toBe(false);
    expect(phaseOf(answering)).toBe("asking the model");
    expect(counted(answering)).toBe("1 tokens");
  });

  it("does not claim a countable phase elsewhere is a prompt", () => {
    expect(reading(step({ phase: "embed", done: 3, total: 9 }))).toBe(false);
  });
});
