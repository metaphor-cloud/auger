import { describe, expect, it } from "vitest";

import { clicked, matches, type Filters } from "./matches";
import type { Finding } from "../types";

function finding(over: Partial<Finding> = {}): Finding {
  return {
    fingerprint: "abc",
    repo_path: "/repo",
    source: "diff_review",
    severity: "high",
    title: "A race in the queue",
    detail: "Two workers take the same task.",
    suggestion: "",
    file: "src/queue.py",
    line: 12,
    confidence: 0.9,
    status: "open",
    category: "correctness",
    opened_at: null,
    triage: null,
    first_seen_at: "2026-08-29T00:00:00Z",
    last_seen_at: "2026-08-29T00:00:00Z",
    times_seen: 1,
    run_id: null,
    ...over,
  } as Finding;
}

const all: Filters = {
  categories: new Set(["correctness"]),
  states: new Set(["open"]),
  dismissed: false,
  search: "",
};

describe("matches", () => {
  it("keeps a finding that every filter names", () => {
    expect(matches(finding(), all)).toBe(true);
  });

  it("shows nothing when no category pill is on", () => {
    // A pill that is off hides its kind. Turning the last one off cannot bring every
    // kind back, or the pills would be saying the opposite of what they show.
    const none = { ...all, categories: new Set<string>() };
    expect(matches(finding({ category: "correctness" }), none)).toBe(false);
    expect(matches(finding({ category: "security" }), none)).toBe(false);
  });

  it("shows nothing when no state pill is on", () => {
    const none = { ...all, states: new Set<string>() };
    expect(matches(finding({ status: "open" }), none)).toBe(false);
    expect(matches(finding({ status: "resolved" }), none)).toBe(false);
  });

  it("still hides a category that is switched off while others are on", () => {
    expect(matches(finding({ category: "style" }), all)).toBe(false);
  });

  it("hides what a model called false unless asked for", () => {
    expect(matches(finding({ triage: "false" }), all)).toBe(false);
    expect(matches(finding({ triage: "false" }), { ...all, dismissed: true })).toBe(true);
  });

  it("searches the title, the detail, and the path", () => {
    for (const wanted of ["RACE", " queue.py ", "same task"]) {
      expect(matches(finding(), { ...all, search: wanted })).toBe(true);
    }
    expect(matches(finding(), { ...all, search: "nothing like it" })).toBe(false);
  });
});

describe("clicked", () => {
  const everyState = new Set(["open", "doing", "resolved", "suppressed"]);

  it("turns off the pill that was clicked and leaves the rest alone", () => {
    expect([...clicked(everyState, "open")]).toEqual(["doing", "resolved", "suppressed"]);
  });

  it("turns the pill back on when it is off", () => {
    expect([...clicked(new Set(["open"]), "doing")]).toEqual(["open", "doing"]);
  });

  it("never changes a pill other than the one clicked", () => {
    // The report this rule exists to answer: clicking one pill made the others vanish.
    for (const name of [...everyState]) {
      const next = clicked(everyState, name);
      for (const other of everyState) {
        if (other !== name) expect(next.has(other)).toBe(true);
      }
      expect(next.has(name)).toBe(false);
      expect(clicked(next, name)).toEqual(everyState);
    }
  });
});
