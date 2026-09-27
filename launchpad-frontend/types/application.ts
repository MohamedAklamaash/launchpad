export type ApplicationStatus = 'CREATED' | 'BUILDING' | 'PUSHING_IMAGE' | 'DEPLOYING' | 'ACTIVE' | 'SLEEPING' | 'FAILED';

// List response (minimal)
export interface ApplicationSummary {
  id: string;
  name: string;
  status: ApplicationStatus;
  cpu: number;
  memory: number;
  port: number;
}

// Detail response (full)
export interface Application {
  id: string;
  name: string;
  description: string | null;
  infrastructure_id: string;
  status: ApplicationStatus;
  is_sleeping: boolean;
  auto_deploy_paused: boolean;
  cpu: number;
  memory: number;
  storage: number;
  port: number;
  url: string;
  branch: string;
  dockerfile_path: string;
  build_context: string | null;
  envs: Record<string, string>;
  attached_database_ids: string[];
  deployment_url: string | null;
  /** F1b: null until TLS/DNS are live and this app has a host-mode route — the path URL
   * above always works regardless, host URLs are additive. */
  host_url: string | null;
  /** Why host_url is null, e.g. 'tls_not_issued', 'dns_not_synced', 'host_route_not_applied'. */
  host_url_status: string | null;
  build_id: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export type DeploymentTriggeredBy = 'DEPLOY' | 'ROLLBACK';

export interface Deployment {
  id: string;
  image_tag: string;
  commit_sha: string | null;
  compute_type: string;
  status: 'SUCCEEDED' | 'FAILED';
  triggered_by: DeploymentTriggeredBy;
  created_at: string;
  /** False for a row tagged 'latest' (predates the per-commit buildspec) — not a valid rollback target. */
  rollback_addressable: boolean;
}

export interface RollbackPreview {
  deployment_id: string;
  image_tag: string;
  commit_sha: string | null;
  compute_type: string;
  cpu: number;
  memory: number;
  port: number;
  deployed_at: string;
  added_keys: string[];
  removed_keys: string[];
  /** Derived from a content hash — no env values are ever returned. */
  values_changed: boolean;
}

export interface ApplicationCreate {
  infrastructure_id: string;
  name: string;
  description?: string;
  project_remote_url: string;
  project_branch: string;
  dockerfile_path?: string;
  build_context?: string;
  port?: number;
  alloted_cpu: number;
  alloted_memory: number;
  envs?: Record<string, string>;
}

export type RuntimeLogContainer = 'app' | 'proxy';

export interface RuntimeLogEvent {
  timestamp: string;
  message: string;
}

export interface RuntimeLogsResponse {
  events: RuntimeLogEvent[];
  next_cursor: string | null;
  truncated: boolean;
}

export interface RuntimeLogsQuery {
  container?: RuntimeLogContainer;
  minutes?: number;
  cursor?: string;
  previous?: boolean;
}

export interface ApplicationUpdate {
  name?: string;
  description?: string;
  project_branch?: string;
  dockerfile_path?: string;
  port?: number;
  alloted_cpu?: number;
  alloted_memory?: number;
  envs?: Record<string, string>;
  /** Full replacement list. Does not trigger a redeploy — redeploy to apply. */
  attached_database_ids?: string[];
}
