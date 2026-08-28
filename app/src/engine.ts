/** Client for the engine. The host owns the port and the token and hands them over IPC. */

import { invoke } from "@tauri-apps/api/core";

import { takeEvents, type ServerEvent } from "./sse";

export type EngineInfo = { port: number; token: string };

export async function engineInfo(): Promise<EngineInfo> {
  return invoke<EngineInfo>("engine_info");
}

export function engineUrl(info: EngineInfo, path: string): string {
  return `http://127.0.0.1:${info.port}${path}`;
}

export async function engineFetch(info: EngineInfo, path: string): Promise<Response> {
  return fetch(engineUrl(info, path), {
    headers: { Authorization: `Bearer ${info.token}` },
  });
}

/** Read the event stream until the caller aborts it. */
export async function readEvents(
  info: EngineInfo,
  onEvent: (event: ServerEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  const response = await fetch(engineUrl(info, "/events"), {
    headers: { Authorization: `Bearer ${info.token}` },
    signal,
  });
  if (!response.ok || !response.body) {
    throw new Error(`event stream failed with status ${response.status}`);
  }
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) return;
    buffer += value;
    const { events, rest } = takeEvents(buffer);
    buffer = rest;
    events.forEach(onEvent);
  }
}
