import { describe, expect, it } from "vitest";

import { gather, peak } from "./buckets";

const NOW = Date.parse("2026-08-28T12:00:00Z");

function at(minutesAgo: number) {
  return new Date(NOW - minutesAgo * 60_000).toISOString();
}

describe("the run window", () => {
  it("counts every run inside the window, by status", () => {
    const window = gather(
      [
        { at: at(10), status: "ok" },
        { at: at(11), status: "failed" },
        { at: at(12), status: "skipped" },
        { at: at(13), status: "running" },
      ],
      [],
      NOW,
    );
    expect(window.total).toBe(4);
    expect(window.ok).toBe(1);
    expect(window.failed).toBe(1);
    expect(window.skipped).toBe(1);
    expect(window.running).toBe(1);
  });

  it("drops what happened before the window", () => {
    const window = gather([{ at: at(60 * 20), status: "ok" }], [], NOW, 12);
    expect(window.total).toBe(0);
  });

  it("puts what happened at the same minute in the same slice", () => {
    const window = gather(
      [
        { at: at(30), status: "ok" },
        { at: at(30), status: "failed" },
      ],
      [],
      NOW,
      12,
      90,
    );
    const busy = window.slices.filter((slice) => slice.total > 0);
    expect(busy).toHaveLength(1);
    expect(busy[0].total).toBe(2);
  });

  it("reads a stamp the engine wrote with no zone as UTC", () => {
    const window = gather([{ at: "2026-08-28T11:30:00", status: "ok" }], [], NOW);
    expect(window.total).toBe(1);
  });

  it("keeps findings in the order they appeared", () => {
    const window = gather(
      [],
      [
        { at: at(10), severity: "high" },
        { at: at(200), severity: "medium" },
      ],
      NOW,
    );
    expect(window.findings.map((one) => one.severity)).toEqual(["medium", "high"]);
  });

  it("has a scale even when nothing has run", () => {
    expect(peak(gather([], [], NOW))).toBe(1);
  });

  it("treats an unreadable stamp as outside the window", () => {
    const window = gather([{ at: "not a time", status: "ok" }], [], NOW);
    expect(window.total).toBe(0);
  });
});
