/** Client for the engine. The host owns the port and the token and hands them over IPC. */

import { invoke } from "@tauri-apps/api/core";

import { takeEvents, type ServerEvent } from "./sse";
import type { RepositoryList } from "./types";

export type EngineInfo = { port: number; token: string };

let cached: EngineInfo | null = null;

export async function engineInfo(): Promise<EngineInfo> {
  cached ??= await invoke<EngineInfo>("engine_info");
  return cached;
}

export function engineUrl(info: EngineInfo, path: string): string {
  return `http://127.0.0.1:${info.port}${path}`;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const info = await engineInfo();
  const response = await fetch(engineUrl(info, path), {
    ...init,
    headers: { ...init.headers, Authorization: `Bearer ${info.token}` },
  });
  if (!response.ok) throw new Error(`${init.method ?? "GET"} ${path} returned ${response.status}`);
  return (await response.json()) as T;
}

export async function health(): Promise<{ status: string; version: string }> {
  return request("/health");
}

export async function getRepositories(): Promise<RepositoryList> {
  return request("/repositories");
}

export async function rescan(): Promise<RepositoryList> {
  return request("/scan", { method: "POST" });
}

/** Read the event stream until the caller aborts it. */
export async function readEvents(
  onEvent: (event: ServerEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  const info = await engineInfo();
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
