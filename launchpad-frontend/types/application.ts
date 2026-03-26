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
  status: ApplicationStatus;
  is_sleeping: boolean;
  cpu: number;
  memory: number;
  storage: number;
  port: number;
  url: string;
  branch: string;
  dockerfile_path: string;
  build_context: string | null;
  envs: Record<string, string>;
  deployment_url: string | null;
  build_id: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
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

export interface ApplicationUpdate {
  name?: string;
  description?: string;
  project_branch?: string;
  dockerfile_path?: string;
  port?: number;
  alloted_cpu?: number;
  alloted_memory?: number;
  envs?: Record<string, string>;
}
