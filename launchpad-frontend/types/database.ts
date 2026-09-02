export type DatabaseEngine = 'postgres' | 'mysql' | 'redis' | 'docdb';

export type DatabaseStatus = 'PENDING' | 'PROVISIONING' | 'ACTIVE' | 'ERROR' | 'DELETING' | 'DELETED';

export interface ManagedDatabase {
  id: string;
  environment_id: string;
  name: string;
  engine: DatabaseEngine;
  engine_version: string;
  instance_class: string;
  allocated_storage: number | null;
  status: DatabaseStatus;
  host: string | null;
  port: number | null;
  // AWS Secrets Manager ARN only — never a credential value.
  secret_arn: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface DatabaseCreate {
  name: string;
  engine: DatabaseEngine;
  engine_version: string;
  instance_class: string;
  allocated_storage?: number;
}

export interface PolicyRefreshRequiredError {
  error: string;
  code: 'policy_refresh_required';
  denied_actions: string[];
}
