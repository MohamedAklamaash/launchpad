export type InfrastructureStatus = 'PENDING' | 'PROVISIONING' | 'ACTIVE' | 'ERROR' | 'DESTROYING' | 'DESTROYED';

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
  owner_id: string;
  user_id: string;
  status: InfrastructureStatus;
  is_cloud_authenticated: boolean;
  invited_users?: InvitedUserSummary[];
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
  metadata?: { aws_region?: string;[key: string]: string | undefined };
}
