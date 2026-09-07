export type InfrastructureStatus = 'PENDING' | 'PROVISIONING' | 'ACTIVE' | 'ERROR' | 'DESTROYING' | 'DESTROYED';

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
  invited_users?: InvitedUserSummary[];
  metadata?: { aws_region?: string; [key: string]: string | undefined };
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
