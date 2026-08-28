/** Client for the engine. The host owns the port and the token and hands them over IPC. */

import { invoke } from "@tauri-apps/api/core";

import { takeEvents, type ServerEvent } from "./sse";
import type {
  BackendList,
  FindingList,
  ForgeList,
  Queue,
  RepositoryList,
  RunList,
  Settings,
  System,
  ToolList,
} from "./types";

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

export async function getSystem(): Promise<System> {
  return request("/system");
}

export async function getRepositories(): Promise<RepositoryList> {
  return request("/repositories");
}

export async function getModels(): Promise<BackendList> {
  return request("/models");
}

export async function checkModels(): Promise<BackendList> {
  return request("/models/check", { method: "POST" });
}

export async function startModels(): Promise<BackendList> {
  return request("/models/start", { method: "POST" });
}

export async function getFindings(repo?: string, status = "open"): Promise<FindingList> {
  const query = new URLSearchParams({ status });
  if (repo) query.set("repo", repo);
  return request(`/findings?${query}`);
}

export async function setFindingStatus(
  fingerprints: string[],
  status: "open" | "suppressed" | "resolved",
): Promise<FindingList> {
  return request("/findings/status", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ fingerprints, status }),
  });
}

export async function getRuns(repo?: string): Promise<RunList> {
  return request(repo ? `/runs?repo=${encodeURIComponent(repo)}` : "/runs");
}

export async function getQueue(): Promise<Queue> {
  return request("/queue");
}

export async function pauseQueue(): Promise<Queue> {
  return request("/queue/pause", { method: "POST" });
}

export async function resumeQueue(): Promise<Queue> {
  return request("/queue/resume", { method: "POST" });
}

export async function requestReview(path: string, target = "HEAD"): Promise<Queue> {
  return request("/review", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, target }),
  });
}

export async function getForges(): Promise<ForgeList> {
  return request("/forges");
}

export async function getTools(): Promise<ToolList> {
  return request("/tools");
}

export async function checkTools(): Promise<ToolList> {
  return request("/tools/check", { method: "POST" });
}

export async function getSettings(): Promise<Settings> {
  return request("/settings");
}

export async function changeSettings(
  level: "defaults" | "org" | "repo",
  key: string,
  changes: Record<string, unknown>,
): Promise<Settings> {
  return request("/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ level, key, changes }),
  });
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
