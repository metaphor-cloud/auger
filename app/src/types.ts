/** Shapes that the engine returns. These mirror `engine/src/auger/api/models.py`. */

export type Mode = "off" | "draft" | "complete";

export type Policy = {
  enabled: boolean;
  mode: Mode;
  instructions: string;
  auto_review_assigned_prs: boolean;
  idle_seconds: number;
  priority: number;
  model_profile: string;
  system_prompt: string;
  hints: string;
  tools: string[];
  max_tool_calls: number;
  audit_hours: number;
  adversary: boolean;
  alternate: boolean;
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
  config_error: string | null;
};

export type Backend = {
  name: string;
  url: string;
  model: string;
  up: boolean;
  hosted: boolean;
  managed: boolean;
  state: "running" | "starting" | "stopped";
  downloaded: boolean;
  ours: boolean;
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
  category: string;
  opened_at: string | null;
  triage: string | null;
  first_seen_at: string;
  last_seen_at: string;
  times_seen: number;
  run_id: string | null;
};

export type Preset = {
  key: string;
  name: string;
  summary: string;
  system: string;
};

export type Prompt = {
  system: string;
  rules: string;
  instructions: string;
  shipped: string;
  preset: string;
  presets: Preset[];
  missing: string[];
};

export type Turn = {
  id: number;
  at: number;
  backend: string;
  model: string;
  job_class: string;
  repo: string;
  prompt: string;
  answer: string;
  prompt_tokens: number;
  completion_tokens: number;
  duration_ms: number;
  error: string | null;
  clipped: boolean;
};

export type TranscriptList = { turns: Turn[]; latest: number; depth: number };

export type Onboarding = {
  done: boolean;
  roots: number;
  models_ready: boolean;
  repositories: number;
  sandbox: string;
  degraded: boolean;
};

export type Note = {
  id: number;
  author: string;
  written_at: string;
  text: string;
};

export type NoteList = { notes: Note[] };

export type Recorded = { item: Finding; existed: boolean };

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
  ready: boolean;
  workers: number;
  models_ready: boolean;
  models_reason: string | null;
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

export type Root = { path: string; exclude: string[]; max_depth: number | null };

export type McpServerSetting = {
  name: string;
  transport: string;
  target: string;
  enabled: boolean;
  timeout_seconds: number;
};

export type ForgeSetting = { name: string; host: string; enabled: boolean };

export type SettingField = {
  key: string;
  path: string;
  kind: "boolean" | "integer" | "number" | "string";
  value: unknown;
  default: unknown;
  choices: string[];
  minimum: number | null;
  maximum: number | null;
  describes: string;
};

export type SettingSection = {
  name: string;
  title: string;
  describes: string;
  fields: SettingField[];
};

export type SettingsSchema = { sections: SettingSection[]; handled: string[] };

export type Settings = {
  defaults: Policy;
  levels: PolicyLevel[];
  config_path: string;
  exclude: string[];
  codegraph: boolean;
  codegraph_available: boolean;
  roots: Root[];
  mcp: McpServerSetting[];
  forges: ForgeSetting[];
  schedule: Record<string, number | string | boolean>;
  allow_hosted: boolean;
  //: What the active profile spends on a review.
  profile_limits: {
    review_max_tokens: number;
    review_temperature: number;
    names: string[];
  } | null;
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
  needs_sign_in: boolean;
  signed_in: boolean;
};

export type ToolList = { servers: McpServer[]; allowed: string[] };

export type Found = {
  source: string;
  id: string;
  url: string;
  downloads: number;
  likes: number;
  gated: boolean;
  updated: string;
};

export type SearchResults = { results: Found[]; token: boolean; token_env: string };

export type ModelFile = {
  name: string;
  size_bytes: number;
  gigabytes: number;
  fits: boolean;
  downloaded: boolean;
};

export type FileResults = { repo: string; files: ModelFile[]; usable_memory_gb: number };

export type ModelChoice = {
  name: string;
  job_class: string;
  repo: string;
  filename: string;
  memory_gb: number;
  description: string;
  fits: boolean;
  downloaded: boolean;
};

export type Catalog = {
  models: ModelChoice[];
  recommended: string;
  /** The model each job class runs right now, keyed by job class. */
  chosen: Record<string, string>;
  usable_memory_gb: number;
  runtime_installed: boolean;
  setup_running: boolean;
};

export type SetupProgress = {
  stage: string;
  name: string;
  received: number;
  total: number;
  fraction: number;
  message: string;
};

export type RepositorySummary = {
  path: string;
  name: string;
  open_findings: number;
  worst_severity: string;
  last_run_at: string | null;
  last_status: string | null;
};

export type Dashboard = {
  version: string;
  paused: boolean;
  pending: number;
  in_flight: string[];
  workers: number;
  sandbox: Sandbox;
  models_up: number;
  models_total: number;
  codegraph: boolean;
  repositories: number;
  enabled: number;
  excluded: number;
  indexed_files: number;
  chunks: number;
  findings: Record<string, number>;
  suppressed: number;
  dismissed: number;
  runs_today: number;
  runs_by_status: Record<string, number>;
  prompt_tokens: number;
  completion_tokens: number;
  last_run_at: string | null;
  skipped_reasons: Record<string, number>;
  busiest: RepositorySummary[];
  warnings: string[];
};
