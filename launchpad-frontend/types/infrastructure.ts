export type InfrastructureStatus = 'PENDING' | 'PROVISIONING' | 'ACTIVE' | 'UPDATING' | 'ERROR' | 'DESTROYING' | 'DESTROYED';

export type ComputeType = 'ecs_fargate' | 'eks';

export interface InvitedUserSummary {
  id: string;
  email: string;
  user_name: string;
  role: string;
}

export interface Infrastructure {
  id: string;
  name: string;
  cloud_provider: 'AWS';
  max_cpu: number;
  max_memory: number;
  code: string;
  compute_type: ComputeType;
  owner_id: string;
  user_id: string;
  status: InfrastructureStatus;
  is_cloud_authenticated: boolean;
  is_mock?: boolean;
  /** LaunchpadDeploymentPolicy version applied in the customer's account. Null when the
   *  account was onboarded by a script predating policy versioning. */
  policy_version?: number | null;
  /** The version Launchpad currently ships. */
  current_policy_version?: number | null;
  /** The lowest policy version this infra's compute_type must be at. Distinct from
   *  current_policy_version, which is the global latest and can be ahead of what this
   *  compute_type has ever needed. */
  required_policy_version?: number | null;
  /** True when the applied version is behind — the customer should re-run the refresh
   *  script before the missing grants cause an AccessDenied mid-deploy. */
  policy_refresh_required?: boolean;
  invited_users?: InvitedUserSummary[];
  /** Launchpad platform AWS account ID; the trust policy created by create_aws_role.sh
   *  must name this as principal. */
  platform_account_id: string;
  /** Launchpad platform IAM user name; the trust policy created by create_aws_role.sh
   *  must name this as principal. */
  platform_user: string;
  metadata?: { aws_region?: string; [key: string]: string | undefined };
  /** Set once the owner completes the exit flow (F6). Never cleared. */
  exited_at?: string | null;
  created_at: string;
  updated_at: string;
  environment?: Environment;
}

export interface Environment {
  id: string;
  infrastructure_id: string;
  vpc_id?: string;
  ecs_cluster_arn?: string;
  alb_arn?: string;
  alb_dns?: string;
  ecr_repository_url?: string;
  task_execution_role_arn?: string;
  subnet_ids?: string[];
  security_group_ids?: string[];
  status: InfrastructureStatus;
}

export interface InfrastructureCreate {
  name: string;
  cloud_provider: 'aws';
  max_cpu: number;
  max_memory: number;
  code: string;
  compute_type: ComputeType;
  metadata?: { aws_region?: string;[key: string]: string | undefined };
}

// Returned only by POST /api/infrastructures/. The plaintext nonce is shown once and never re-served.
export interface InfrastructureCreateResponse extends Infrastructure {
  onboarding_token: string;
}


// Owner-only. Served by GET /api/infrastructures/{id}/logs; invited users get 403.
export interface ProvisioningLogs {
  status: InfrastructureStatus;
  error_message: string | null;
  logs: string;
  withheld_lines: number;
  truncated: boolean;
  updated_at: string;
}

// Owner-only. Served by GET /api/infrastructures/{id}/costs; invited users get 403.
// ecs_fargate infras carry source="actual" everywhere; eks infras carry source="estimate"
// everywhere (a Kubernetes pod isn't a taggable AWS resource, so there is no actual figure
// to fall back to). Can return 422 with PolicyRefreshRequiredError if the applied IAM
// policy predates the Cost Explorer grants (v3).
export type CostSource = 'actual' | 'estimate' | 'mock';

export interface AppCost {
  app: string;
  amount_usd: number;
  source: CostSource;
}

export interface SharedCost {
  amount_usd: number;
  source: CostSource;
  note?: string;
}

export interface TagActivation {
  activated: boolean | null;
  reason: string | null;
}

export interface InfrastructureCosts {
  infrastructure_id: string;
  compute_type: ComputeType;
  window_start: string;
  window_end: string;
  currency: string;
  apps: AppCost[];
  shared: SharedCost;
  tag_activation: TagActivation;
  is_mock: boolean;
  cached: boolean;
}

export type NukeRunStatus = 'PENDING' | 'RUNNING' | 'FAILED' | 'COMPLETED';

export type NukeStepStatus = 'pending' | 'running' | 'success' | 'failed' | 'policy_refresh_required';

export interface NukeStep {
  key: string;
  label: string;
  status: NukeStepStatus;
  detail: unknown;
}

export interface NukeLeftover {
  type: string;
  id: string;
  reason: string;
}

export interface NukeStatus {
  infrastructure_id: string;
  status: NukeRunStatus;
  steps: NukeStep[];
  leftovers: NukeLeftover[];
  started_at: string | null;
  finished_at: string | null;
}
