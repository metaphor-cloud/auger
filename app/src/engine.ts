/** Client for the engine. The host owns the port and the token and hands them over IPC. */

import { invoke } from "@tauri-apps/api/core";

import { takeEvents, type ServerEvent } from "./sse";
import type {
  BackendList,
  Catalog,
  Dashboard,
  FileResults,
  FindingList,
  ForgeList,
  Queue,
  RepositoryList,
  NoteList,
  Onboarding,
  Prompt,
  Recorded,
  RunList,
  SearchResults,
  Settings,
  SettingsSchema,
  TranscriptList,
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

/** What the engine refused, in its own words. A rejected setting says which one. */
async function reason(response: Response, method: string, path: string): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
  } catch {
    // The body was not JSON. Fall back to the status.
  }
  return `${method} ${path} returned ${response.status}`;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const info = await engineInfo();
  const response = await fetch(engineUrl(info, path), {
    ...init,
    headers: { ...init.headers, Authorization: `Bearer ${info.token}` },
  });
  if (!response.ok) throw new Error(await reason(response, init.method ?? "GET", path));
  return (await response.json()) as T;
}

export async function health(): Promise<{ status: string; version: string }> {
  return request("/health");
}

export async function getDashboard(): Promise<Dashboard> {
  return request("/dashboard");
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

export async function getCatalog(): Promise<Catalog> {
  return request("/models/catalog");
}

export async function setupModels(
  model = "",
  embed = "",
  adversary = "",
): Promise<{ ok: boolean; error: string | null }> {
  return request("/models/setup", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model, embed, adversary }),
  });
}

export async function searchModels(query: string): Promise<SearchResults> {
  return request(`/models/search?q=${encodeURIComponent(query)}`);
}

export async function modelFiles(repo: string): Promise<FileResults> {
  return request(`/models/files?repo=${encodeURIComponent(repo)}`);
}

export async function fetchModel(
  repo: string,
  filename: string,
  jobClass: string,
): Promise<{ ok: boolean; error: string | null }> {
  return request("/models/fetch", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ repo, filename, job_class: jobClass }),
  });
}

export async function checkModels(): Promise<BackendList> {
  return request("/models/check", { method: "POST" });
}

export async function startModels(): Promise<BackendList> {
  return request("/models/start", { method: "POST" });
}

export async function stopModels(name?: string): Promise<BackendList> {
  return request(name ? `/models/stop?name=${encodeURIComponent(name)}` : "/models/stop", {
    method: "POST",
  });
}

export async function markOpened(fingerprint: string): Promise<FindingList> {
  return request(`/findings/${fingerprint}/opened`, { method: "POST" });
}

export async function getPrompt(rules?: string, instructions?: string): Promise<Prompt> {
  const query = new URLSearchParams();
  if (rules !== undefined) query.set("rules", rules);
  if (instructions !== undefined) query.set("instructions", instructions);
  const suffix = query.toString();
  return request(suffix ? `/prompt?${suffix}` : "/prompt");
}

export async function getTranscript(after = 0, limit = 60): Promise<TranscriptList> {
  return request(`/transcript?after=${after}&limit=${limit}`);
}

export async function getOnboarding(): Promise<Onboarding> {
  return request("/onboarding");
}

export async function finishOnboarding(done: boolean): Promise<Onboarding> {
  return request("/onboarding", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ done }),
  });
}

export async function getFindings(
  repo?: string,
  status = "open,doing",
  includeDismissed = false,
  search = "",
): Promise<FindingList> {
  const query = new URLSearchParams({ status });
  if (repo) query.set("repo", repo);
  if (includeDismissed) query.set("include_dismissed", "true");
  if (search.trim()) query.set("query", search.trim());
  return request(`/findings?${query}`);
}

export async function recordItem(item: {
  repo_path: string;
  title: string;
  detail?: string;
  file?: string;
  severity?: string;
}): Promise<Recorded> {
  return request("/findings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(item),
  });
}

export async function getNotes(fingerprint: string): Promise<NoteList> {
  return request(`/findings/${fingerprint}/notes`);
}

export async function addNote(fingerprint: string, text: string): Promise<NoteList> {
  return request(`/findings/${fingerprint}/notes`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
}

export async function setFindingStatus(
  fingerprints: string[],
  status: "open" | "doing" | "resolved" | "suppressed",
): Promise<FindingList> {
  return request("/findings/status", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ fingerprints, status }),
  });
}

export async function getRuns(repo?: string, limit = 100): Promise<RunList> {
  const query = new URLSearchParams({ limit: String(limit) });
  if (repo) query.set("repo", repo);
  return request(`/runs?${query}`);
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

export async function requestAudit(path: string): Promise<Queue> {
  return request("/audit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
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

export async function signInTool(name: string): Promise<ToolList> {
  return request(`/tools/${encodeURIComponent(name)}/sign-in`, { method: "POST" });
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

export async function changeExclusion(pattern: string, remove: boolean): Promise<Settings> {
  return request("/settings/exclude", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pattern, remove }),
  });
}

export async function setCodegraph(enabled: boolean): Promise<Settings> {
  return request("/settings/codegraph", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
}

export async function setSetting(
  path: string,
  value: unknown,
  remove = false,
): Promise<Settings> {
  return request("/settings/value", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, value, remove }),
  });
}

export async function getSettingsSchema(): Promise<SettingsSchema> {
  return request("/settings/schema");
}

export async function getConfigText(): Promise<string> {
  const info = await engineInfo();
  const response = await fetch(engineUrl(info, "/settings/raw"), {
    headers: { Authorization: `Bearer ${info.token}` },
  });
  if (!response.ok) throw new Error(`GET /settings/raw returned ${response.status}`);
  return response.text();
}

export async function writeConfigText(text: string): Promise<Settings> {
  return request("/settings/raw", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
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
