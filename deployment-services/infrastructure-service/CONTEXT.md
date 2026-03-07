# Launchpad Infrastructure Service - Complete Context

## Project Overview
Multi-tenant SaaS platform that provisions AWS infrastructure in users' AWS accounts. Users provide credentials, platform deploys baseline infrastructure (VPC, ECS, ALB, ECR) via Terraform.

## Architecture

### Core Concept
**Delegated Deployment Model:**
- User provides AWS credentials (access keys or role ARN)
- Platform provisions infrastructure in user's AWS account
- User owns and pays for all AWS resources
- Platform manages lifecycle via Terraform

### Services
1. **Identity Services** - Auth, users, notifications
2. **Gateway Service** - API routing
3. **Infrastructure Service** - Provisions AWS baseline infra (THIS SERVICE)
4. **Application Service** - Deploys user applications to provisioned infra
5. **Payment Service** - Billing for platform usage

## Infrastructure Service

### Components
- **Django REST API** - CRUD endpoints for infrastructure
- **Worker** (`worker.py`) - Async Terraform executor
- **Redis Queue** - Job queue for provision/destroy operations
- **RabbitMQ** - Event bus for cross-service communication
- **PostgreSQL** - Infrastructure and environment state

### Data Models
- **Infrastructure** - User's infrastructure definition (credentials, region, config)
- **Environment** - Provisioned state (VPC ID, cluster ARN, ALB DNS, logs, status)

### Workflow

**Provision:**
1. User creates infrastructure via API (POST /infrastructures/)
2. API validates, saves to DB, enqueues provision job
3. Worker picks job from Redis queue
4. Worker authenticates to user's AWS account
5. Worker runs `terraform apply` with S3 backend
6. Outputs saved to Environment model
7. User notified via RabbitMQ event

**Destroy:**
1. User deletes infrastructure via API (DELETE /infrastructures/{id}/)
2. API enqueues destroy job
3. Worker runs `terraform destroy`
4. Resources deleted in user's account
5. Environment marked as DESTROYED

### AWS Resources Created (in user's account)

**VPC Module:**
- VPC with DNS support
- 2 public subnets (for ALB)
- 2 private subnets (for ECS tasks)
- Internet Gateway
- NAT Gateway + Elastic IP
- Route tables (public/private)
- Security groups (ALB, compute)
- VPC Flow Logs (CloudWatch + KMS encryption)
- S3 VPC Endpoint
- Network ACLs

**ECS Module:**
- ECS Fargate cluster
- Container Insights enabled

**ALB Module:**
- Application Load Balancer
- Target group (IP-based for Fargate)
- HTTP listener (port 80)
- Health checks

**ECR Module:**
- ECR repository for container images
- Image scanning enabled
- Lifecycle policy (keep last 10 images)

**IAM Module:**
- ECS task execution role
- Policies for ECR, CloudWatch Logs

### State Management

**Terraform Backend:**
- S3 bucket: `launchpad-tf-state-{account_id}` (shared per account)
- DynamoDB table: `launchpad-tf-locks-{account_id}` (shared per account)
- State file path: `infra/{infra_id}/terraform.tfstate` (unique per infrastructure)

**Why shared bucket/table:**
- One bucket per AWS account (user's account)
- Each infrastructure gets unique state file path
- DynamoDB provides state locking

### Authentication

**Platform Credentials:**
- IAM user: `aklamaash-terraform` in account `221082203366`
- Used for platform's own infrastructure

**User Credentials:**
- User provides AWS access key + secret key
- OR user creates `LaunchpadDeploymentRole` in their account
- Worker uses credentials to provision in user's account

**Cross-Account Role (optional):**
- Role: `LaunchpadDeploymentRole`
- Trust: Platform IAM user
- Permissions: `ec2:*`, `ecs:*`, `iam:*`, `kms:*`, `s3:*`, `dynamodb:*`, `logs:*`, `ecr:*`, `elasticloadbalancing:*`
- Created via: `/app_scripts/create_aws_role.sh`

### File Structure

```
infrastructure-service/
├── api/
│   ├── models/
│   │   ├── infrastructure.py      # Infrastructure model
│   │   └── environment.py         # Environment/state model
│   ├── services/
│   │   ├── terraform_worker.py    # Terraform execution logic
│   │   ├── infra_queue.py         # Redis queue operations
│   │   ├── notification.py        # User notifications
│   │   └── infrastructure.py      # Business logic
│   ├── views/
│   │   └── infrastructure.py      # REST API endpoints
│   ├── serializers/
│   │   └── infrastructure.py      # DRF serializers
│   └── messaging/
│       └── consumers/
│           └── auth_consumer.py   # RabbitMQ event consumer
├── infra/
│   └── aws/
│       └── modules/
│           ├── vpc/               # VPC, subnets, NAT, IGW
│           ├── ecs/               # ECS cluster
│           ├── alb/               # Load balancer
│           ├── ecr/               # Container registry
│           └── iam/               # IAM roles
├── core/
│   ├── settings.py                # Django settings
│   └── urls.py                    # URL routing
├── worker.py                      # Async worker process
└── manage.py                      # Django management
```

### Key Files

**Worker & Execution:**
- `worker.py` - Main worker loop, processes provision/destroy jobs
- `api/services/terraform_worker.py` - Terraform execution, state management
- `api/services/infra_queue.py` - Redis queue operations

**API & Business Logic:**
- `api/views/infrastructure.py` - REST endpoints
- `api/services/infrastructure.py` - Business logic, validation
- `api/models/infrastructure.py` - Infrastructure model
- `api/models/environment.py` - Environment state model

**Terraform Modules:**
- `infra/aws/modules/vpc/main.tf` - VPC resources
- `infra/aws/modules/ecs/main.tf` - ECS cluster
- `infra/aws/modules/alb/main.tf` - Load balancer
- `infra/aws/modules/ecr/main.tf` - Container registry
- `infra/aws/modules/iam/main.tf` - IAM roles

### Terraform Deletion Order

When `terraform destroy` runs (automatic, in reverse dependency order):

1. ALB listeners → target groups → load balancer
2. ECS cluster
3. VPC endpoints
4. Route table associations
5. Route tables
6. NAT gateway (⏱️ 5-10 minutes - AWS limitation)
7. Elastic IP
8. Internet gateway
9. Subnets
10. Security groups
11. Network ACLs
12. Flow logs, CloudWatch log groups
13. IAM roles/policies
14. KMS keys (scheduled for deletion)
15. VPC

**Note:** NAT Gateway deletion is slow (AWS behavior). Terraform waits for complete deletion before proceeding.

### Environment Statuses

- `PENDING` - Infrastructure created, waiting for provision
- `PROVISIONING` - Terraform apply in progress
- `ACTIVE` - Successfully provisioned
- `ERROR` - Provision failed (includes cleanup status)
- `DESTROYING` - Terraform destroy in progress
- `DESTROYED` - Successfully destroyed

### Error Handling

**Provision Failures:**
1. Transient errors (throttling, timeouts) → Retry up to 3 times
2. Permanent errors → Run `terraform destroy` to clean up
3. Cleanup status included in error message
4. User notified with actual cleanup result

**Destroy Failures:**
- Logged with full error details
- User notified to check AWS console
- State file remains in S3 for manual recovery

### Recent Fixes

1. **IAM/KMS Permissions** - Changed to `iam:*` and `kms:*` wildcards (policy v9)
2. **ECS Capacity Providers** - Removed (not needed for basic Fargate)
3. **Worker Error Handling** - Handle missing Infrastructure/Environment gracefully
4. **Terraform Dependencies** - Added explicit `depends_on` for proper deletion order
5. **Cleanup Logging** - Enhanced to show actual cleanup success/failure

### Scripts

**Setup:**
- `app_scripts/create_aws_role.sh` - Create LaunchpadDeploymentRole in user's account
- `app_scripts/update_aws_role.sh` - Update IAM policy

**Cleanup (for development only):**
- `app_scripts/cleanup_kms_key.sh` - Delete orphaned KMS keys
- `app_scripts/cleanup_ecs_clusters.sh` - Delete empty ECS clusters
- `app_scripts/cleanup_vpc_resources.sh` - Delete orphaned VPC resources

**Note:** In production, Terraform handles all cleanup in user's account.

### Tech Stack

- **Backend:** Django 5.1, Django REST Framework
- **Queue:** Redis (job queue), RabbitMQ (events)
- **IaC:** Terraform 1.x
- **Cloud:** AWS (boto3)
- **Database:** PostgreSQL
- **Language:** Python 3.14

### Development

**Start Worker:**
```bash
cd deployment-services/infrastructure-service
source ../venv/bin/activate
./worker.py
```

**Start API:**
```bash
python manage.py runserver
```

**Run Migrations:**
```bash
python manage.py migrate
```

### API Endpoints

- `POST /infrastructures/` - Create infrastructure
- `GET /infrastructures/` - List user's infrastructures
- `GET /infrastructures/{id}/` - Get infrastructure details
- `DELETE /infrastructures/{id}/` - Delete infrastructure (triggers destroy)
- `GET /infrastructures/{id}/environment/` - Get environment state

### Configuration

**Environment Variables (.env):**
```
DATABASE_URL=postgresql://user:pass@localhost:5432/infra_db
REDIS_URL=redis://localhost:6379/0
RABBITMQ_URL=amqp://guest:guest@localhost:5672/
JWT_SECRET=your-secret-key
AWS_REGION=us-west-2
```

### Monitoring

- Worker logs to stdout (capture with systemd/docker)
- Terraform logs stored in Environment.logs field
- RabbitMQ events for cross-service notifications
- CloudWatch Logs in user's account (VPC Flow Logs)

### Security

- User credentials encrypted in database
- Terraform state encrypted in S3
- KMS encryption for CloudWatch Logs
- Security groups restrict traffic (ALB → ECS only)
- Private subnets for compute (no direct internet access)
- IAM roles follow least privilege

### Limitations

- NAT Gateway deletion takes 5-10 minutes (AWS limitation)
- One infrastructure per user per region (can be changed)
- Terraform runs synchronously (worker blocks during apply/destroy)
- No rollback on partial failures (destroy cleans up)

### Future Enhancements

- Support for multiple regions
- Custom VPC CIDR configuration
- Auto-scaling policies
- HTTPS/TLS termination at ALB
- CloudFormation alternative to Terraform
- Terraform Cloud integration
- Cost estimation before provision
- Resource tagging customization
