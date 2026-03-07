# Launchpad - Infrastructure Provisioning Platform

## Project Overview
Multi-tenant SaaS platform that provisions AWS infrastructure for users via Terraform, managed through a Django backend with async worker processing.

## Architecture

### Services
1. **Identity Services** - User authentication, authorization, notifications
2. **Gateway Service** - API gateway/routing
3. **Infrastructure Service** - Provisions AWS infrastructure (VPC, ECS, ALB, ECR, IAM, KMS)
4. **Application Service** - Manages application deployments
5. **Payment Service** - Billing and subscriptions

### Infrastructure Service (Current Focus)

**Components:**
- **Django API** - REST endpoints for infrastructure CRUD
- **Worker** (`worker.py`) - Async Terraform executor using Redis queue
- **Terraform Modules** - Reusable AWS infrastructure (VPC, ECS, ALB, ECR, IAM, KMS)

**Flow:**
1. User creates infrastructure via API
2. API validates and enqueues provision job to Redis
3. Worker picks up job, authenticates to user's AWS account
4. Worker runs Terraform apply with S3 backend
5. Outputs saved to database, user notified

**AWS Resources Created:**
- VPC with public/private subnets, NAT gateway, flow logs
- ECS Fargate cluster
- Application Load Balancer
- ECR repository
- IAM roles for ECS tasks
- KMS keys for encryption

**State Management:**
- S3 bucket: `launchpad-tf-state-{account_id}` (shared per account)
- DynamoDB: `launchpad-tf-locks-{account_id}` (shared per account)
- State path: `infra/{infra_id}/terraform.tfstate` (unique per infrastructure)

**Authentication:**
- Platform uses IAM user `aklamaash-terraform` in account `221082203366`
- User provides AWS credentials (access key or assumes role)
- Worker uses credentials to provision in user's account

**Deployment Role (for cross-account):**
- Role: `LaunchpadDeploymentRole`
- Policy: `LaunchpadDeploymentPolicy` with `ec2:*`, `ecs:*`, `iam:*`, `kms:*`, `s3:*`, `dynamodb:*`, `logs:*`, `ecr:*`, `elasticloadbalancing:*`
- Created via: `/app_scripts/create_aws_role.sh`

## Key Files

### Infrastructure Service
- `worker.py` - Main worker process
- `api/services/terraform_worker.py` - Terraform execution logic
- `api/services/infra_queue.py` - Redis queue management
- `api/models/infrastructure.py` - Infrastructure model
- `api/models/environment.py` - Environment/state model
- `infra/aws/modules/` - Terraform modules

### Scripts
- `app_scripts/create_aws_role.sh` - Create deployment role
- `app_scripts/update_aws_role.sh` - Update IAM policy
- `app_scripts/cleanup_kms_key.sh` - Clean orphaned KMS keys

## Recent Fixes
1. Added `kms:*` wildcard permission (was hitting missing KMS permissions)
2. Removed ECS capacity providers config (requires service-linked role)
3. Enhanced cleanup logging on provision failures
4. Fixed misleading "resources destroyed" notification

## Tech Stack
- **Backend:** Django 5.1, Django REST Framework
- **Queue:** Redis + Pika (RabbitMQ for events)
- **IaC:** Terraform 1.x
- **Cloud:** AWS (boto3)
- **Database:** PostgreSQL

## Development
```bash
cd deployment-services/infrastructure-service
source ../venv/bin/activate
./worker.py  # Start worker
python manage.py runserver  # Start API
```
