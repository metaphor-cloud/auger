import { describe, expect, it } from "vitest";

import type { Download } from "../types";
import { bytes, controls, fraction, line, rate, remaining } from "./transfers";

const NOW = 1_700_000_000;

function job(over: Partial<Download> = {}): Download {
  return {
    id: "d1",
    label: "qwen3.6-35b-a3b",
    kind: "weights",
    destination: "/models/qwen3.6-35b-a3b",
    state: "running",
    error: "",
    started: NOW - 60,
    updated: NOW,
    total_bytes: 21_100_000_000,
    received_bytes: 2_110_000_000,
    moved: 2_110_000_000,
    moving_since: NOW - 100,
    files: 46,
    files_done: 4,
    current: "model-00004.safetensors",
    items: [],
    ...over,
  };
}

describe("bytes", () => {
  it("uses the unit a person would use for that size", () => {
    expect(bytes(0)).toBe("0 B");
    expect(bytes(512)).toBe("512 B");
    expect(bytes(21_100_000_000)).toBe("21.1 GB");
    expect(bytes(429_300_000_000)).toBe("429.3 GB");
  });
});

describe("fraction", () => {
  it("is a share of the total, held inside its bounds", () => {
    expect(fraction(job())).toBeCloseTo(0.1, 5);
    expect(fraction(job({ total_bytes: 0 }))).toBe(0);
    expect(fraction(job({ received_bytes: 99e9 }))).toBe(1);
  });
});

describe("rate", () => {
  it("is what has moved since this attempt began, not since the job was created", () => {
    expect(rate(job(), NOW)).toBeCloseTo(21_100_000, 0);
  });

  it("says nothing while paused, queued or barely started", () => {
    expect(rate(job({ state: "paused" }), NOW)).toBe(0);
    expect(rate(job({ state: "queued", moving_since: 0 }), NOW)).toBe(0);
    expect(rate(job({ moving_since: NOW - 0.5 }), NOW)).toBe(0);
    expect(rate(job({ moved: 0 }), NOW)).toBe(0);
  });
});

describe("remaining", () => {
  it("scales the unit with how long is left", () => {
    expect(remaining(job({ received_bytes: 21_000_000_000 }), NOW)).toBe("5s left");
    expect(remaining(job({ received_bytes: 20_000_000_000 }), NOW)).toBe("52s left");
    expect(remaining(job(), NOW)).toBe("15m left");
  });

  it("says nothing rather than guessing", () => {
    expect(remaining(job({ state: "paused" }), NOW)).toBe("");
    expect(remaining(job({ total_bytes: 0 }), NOW)).toBe("");
    expect(remaining(job({ received_bytes: 21_100_000_000 }), NOW)).toBe("");
  });
});

describe("line", () => {
  it("says where it is up to and how fast", () => {
    expect(line(job(), NOW)).toBe("2.1 GB of 21.1 GB · file 5 of 46 · 21 MB/s · 15m left");
  });

  it("says paused rather than showing a rate that is not happening", () => {
    expect(line(job({ state: "paused" }), NOW)).toBe(
      "2.1 GB of 21.1 GB · file 5 of 46 · paused",
    );
  });

  it("distinguishes waiting its turn from stopped", () => {
    expect(line(job({ state: "queued" }), NOW)).toContain("waiting its turn");
  });

  it("carries the reason when it failed", () => {
    expect(line(job({ state: "failed", error: "does not match its checksum" }), NOW)).toBe(
      "does not match its checksum",
    );
  });

  it("reports what landed when it is done", () => {
    expect(line(job({ state: "done" }), NOW)).toBe("21.1 GB in 46 files");
  });
});

describe("controls", () => {
  it("offers pause while it moves and continue while it does not", () => {
    expect(controls(job())).toEqual(["pause", "cancel"]);
    expect(controls(job({ state: "queued" }))).toEqual(["pause", "cancel"]);
    expect(controls(job({ state: "paused" }))).toEqual(["resume", "cancel"]);
  });

  it("offers only clearing once it is over", () => {
    for (const state of ["done", "failed", "cancelled"]) {
      expect(controls(job({ state }))).toEqual(["forget"]);
    }
  });
});
