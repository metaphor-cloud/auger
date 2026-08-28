/** Shapes that the engine returns. These mirror `engine/src/reviewrig/api/models.py`. */

export type Mode = "off" | "draft" | "complete";

export type Policy = {
  enabled: boolean;
  mode: Mode;
  auto_review_assigned_prs: boolean;
  idle_seconds: number;
  priority: number;
  model_profile: string;
  hints: string;
  tools: string[];
  audit_hours: number;
};

export type Repository = {
  path: string;
  name: string;
  slug: string;
  host: string | null;
  namespace: string | null;
  org_key: string | null;
  policy: Policy;
};

export type RepositoryList = {
  repositories: Repository[];
  enabled: number;
};

export type Sandbox = {
  backend: string;
  degraded: boolean;
  warning: string | null;
};

export type Egress = {
  proxy_url: string;
  allowed: string[];
  allowed_requests: number;
  refused_requests: number;
  failed_requests: number;
  recently_refused: string[];
};

export type Index = {
  files: number;
  chunks: number;
  vectors: boolean;
  embedded: number;
};

export type System = {
  version: string;
  sandbox: Sandbox;
  egress: Egress;
  index: Index;
  image: string;
};

export type Backend = {
  name: string;
  url: string;
  model: string;
  up: boolean;
  hosted: boolean;
  managed: boolean;
  models_served: string[];
  reason: string | null;
  requests: number;
  prompt_tokens: number;
  completion_tokens: number;
  failures: number;
};

export type BackendList = {
  backends: Backend[];
  profiles: Record<string, Record<string, string>>;
  active_profile_backends: Record<string, string>;
  allow_hosted: boolean;
};

export type Finding = {
  fingerprint: string;
  repo_path: string;
  source: string;
  severity: string;
  title: string;
  detail: string;
  suggestion: string;
  file: string;
  line: number | null;
  confidence: number;
  status: string;
  triage: string | null;
  first_seen_at: string;
  last_seen_at: string;
  times_seen: number;
  run_id: string | null;
};

export type FindingList = {
  findings: Finding[];
  counts: Record<string, number>;
};

export type Run = {
  id: string;
  repo_path: string;
  kind: string;
  status: string;
  reason: string | null;
  base: string | null;
  head: string | null;
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
  finding_count: number;
  prompt_tokens: number;
  completion_tokens: number;
  backend: string | null;
  error: string | null;
  attempts: number;
};

export type RunList = { runs: Run[] };

export type Queue = {
  pending: number;
  in_flight: string[];
  paused: boolean;
  workers: number;
};

export type Forge = {
  name: string;
  kind: string;
  host: string;
  enabled: boolean;
  reachable: boolean;
  user: string;
  reason: string | null;
};

export type ForgeList = { forges: Forge[] };

export type PolicyLevel = {
  level: "defaults" | "org" | "repo";
  key: string;
  overrides: Record<string, unknown>;
};

export type Settings = {
  defaults: Policy;
  levels: PolicyLevel[];
  config_path: string;
};

export type Tool = {
  server: string;
  name: string;
  qualified: string;
  description: string;
};

export type McpServer = {
  name: string;
  transport: string;
  target: string;
  reachable: boolean;
  reason: string | null;
  tools: Tool[];
};

export type ToolList = { servers: McpServer[]; allowed: string[] };
