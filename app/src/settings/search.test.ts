import { describe, expect, it } from "vitest";

import { visible } from "./search";

describe("visible", () => {
  it("shows everything when nothing is typed", () => {
    expect(visible("", "Quiet hours")).toBe(true);
    expect(visible("   ", undefined)).toBe(true);
  });

  it("matches the label whatever the case", () => {
    expect(visible("QUIET", "Quiet hours")).toBe(true);
  });

  it("matches the help sentence, not only the label", () => {
    expect(visible("audits", "Quiet hours", "No full audits during these hours.")).toBe(true);
  });

  it("finds a row by the config key behind it", () => {
    // Searching the key is how somebody arrives from the config file or from a log.
    expect(
      visible("verify_poll_seconds", "Check for findings to judge", undefined, "verify_poll_seconds"),
    ).toBe(true);
  });

  it("hides a row that matches nothing", () => {
    expect(visible("kubernetes", "Quiet hours", "No full audits during these hours.")).toBe(false);
  });
});
