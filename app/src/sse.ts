/**
 * Server-sent event parsing.
 *
 * The UI reads the stream with fetch, not EventSource, because EventSource cannot send
 * the Authorization header and a token in a URL leaks into logs and history.
 */

export type ServerEvent = { kind: string; data: unknown };

/** Split a growing buffer into whole events. Returns the events and the unparsed tail. */
export function takeEvents(buffer: string): { events: ServerEvent[]; rest: string } {
  const events: ServerEvent[] = [];
  let rest = buffer.replace(/\r\n/g, "\n");
  let boundary = rest.indexOf("\n\n");
  while (boundary !== -1) {
    const event = parseEvent(rest.slice(0, boundary));
    if (event) events.push(event);
    rest = rest.slice(boundary + 2);
    boundary = rest.indexOf("\n\n");
  }
  return { events, rest };
}

function parseEvent(block: string): ServerEvent | null {
  let kind = "message";
  const dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (line === "" || line.startsWith(":")) continue;
    const separator = line.indexOf(":");
    const field = separator === -1 ? line : line.slice(0, separator);
    const value = separator === -1 ? "" : line.slice(separator + 1).replace(/^ /, "");
    if (field === "event") kind = value;
    if (field === "data") dataLines.push(value);
  }
  if (dataLines.length === 0) return null;
  const raw = dataLines.join("\n");
  try {
    return { kind, data: JSON.parse(raw) };
  } catch {
    return { kind, data: raw };
  }
}
