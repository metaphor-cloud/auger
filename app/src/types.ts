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
