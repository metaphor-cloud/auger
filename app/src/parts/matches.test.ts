import { describe, expect, it } from "vitest";

import { matches, type Filters } from "./matches";
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

  it("shows everything when no category pill is on", () => {
    // Deselecting every pill empties the list if an empty set means "match nothing",
    // and an empty list reads as a broken view rather than as a filter.
    const none = { ...all, categories: new Set<string>() };
    expect(matches(finding({ category: "security" }), none)).toBe(true);
    expect(matches(finding({ category: "style" }), none)).toBe(true);
  });

  it("shows every state when no state pill is on", () => {
    const none = { ...all, states: new Set<string>() };
    expect(matches(finding({ status: "resolved" }), none)).toBe(true);
    expect(matches(finding({ status: "suppressed" }), none)).toBe(true);
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
