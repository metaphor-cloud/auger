import { describe, expect, it } from "vitest";

import { takeEvents } from "./sse";

describe("takeEvents", () => {
  it("reads one complete event", () => {
    const { events, rest } = takeEvents('event: hello\r\ndata: {"version":"0.1.0"}\r\n\r\n');
    expect(events).toEqual([{ kind: "hello", data: { version: "0.1.0" } }]);
    expect(rest).toBe("");
  });

  it("keeps a partial event for the next chunk", () => {
    const first = takeEvents("event: hello\ndata: {");
    expect(first.events).toHaveLength(0);
    const second = takeEvents(first.rest + '"a":1}\n\n');
    expect(second.events).toEqual([{ kind: "hello", data: { a: 1 } }]);
  });

  it("reads several events from one chunk", () => {
    const { events } = takeEvents("event: a\ndata: 1\n\nevent: b\ndata: 2\n\n");
    expect(events.map((e) => e.kind)).toEqual(["a", "b"]);
  });

  it("defaults the kind when the stream sends no event field", () => {
    const { events } = takeEvents("data: 1\n\n");
    expect(events).toEqual([{ kind: "message", data: 1 }]);
  });

  it("ignores a comment and a keep-alive", () => {
    const { events, rest } = takeEvents(": ping\n\n");
    expect(events).toHaveLength(0);
    expect(rest).toBe("");
  });

  it("returns text when the payload is not json", () => {
    const { events } = takeEvents("event: note\ndata: plain text\n\n");
    expect(events).toEqual([{ kind: "note", data: "plain text" }]);
  });
});
