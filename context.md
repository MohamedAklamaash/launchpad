# Cloud Deployment Platform — System Context

## Overview

This project is a **cloud deployment platform** that allows users to deploy applications directly from GitHub repositories into their own AWS infrastructure.

The platform acts as a **control plane** that orchestrates infrastructure provisioning and application deployment.

The compute, networking, and runtime workloads run entirely inside the **user's AWS account**.

This architecture is similar to platforms like:

- Porter
- Flightcontrol
- Qovery

The platform does **not host user applications** itself. Instead, it manages and orchestrates deployments into the user's cloud environment.

---

# Core Idea

The system converts:


GitHub Repository + Deployment Configuration


into


Running Application inside user's AWS infrastructure


The platform performs orchestration, while all compute resources belong to the user.

---

# System Architecture

The platform is divided into two logical planes.

## Control Plane (Platform)

Runs inside the platform's infrastructure.

Responsible for:

- user authentication
- GitHub integration
- infrastructure orchestration
- deployment orchestration
- job processing
- metadata storage

Components:

- Django API (infrastructure-service, application-service)
- Deployment workers (terraform_worker, deployment_worker)
- PostgreSQL (infrastructure_db, application_db)
- Redis (queue system)
- RabbitMQ (event bus)

The control plane **never runs user application code**.

---

## Data Plane (User Infrastructure)

Runs inside the **user's AWS account**.

Responsible for:

- container execution
- networking
- load balancing
- container registry
- runtime compute

Resources created inside the user's account:

- VPC
- Subnets (public + private)
- NAT Gateway
- Internet Gateway
- ECS Cluster
- Application Load Balancer
- ECR Repository
- ECS Services
- ECS Tasks
- Security Groups
- IAM Roles

---

# Authentication

Users authenticate using **GitHub OAuth**.

GitHub authentication is used for:

- user login
- repository access
- repository cloning for deployments

---

# Infrastructure Model

Each user can create **Infrastructure** objects.

Infrastructure represents a **dedicated AWS environment** owned by the user.

Creating infrastructure provisions a full cloud environment.

Terraform provisions:

- VPC
- Public and private subnets
- NAT Gateway
- Internet Gateway
- Route tables
- ECS Cluster
- Application Load Balancer
- ECR repository
- IAM roles (ECS task execution role)
- Security groups (ALB security group)

Infrastructure provisioning occurs inside the **user's AWS account**.

The platform assumes a role in the user's account using:


sts:AssumeRole


**IAM Setup**:
- User creates `LaunchpadDeploymentRole` in their AWS account
- Role trusts platform IAM user: `aklamaash-terraform` (account 221082203366)
- Platform stores temporary credentials (1 hour TTL) in infrastructure metadata
- Credentials auto-refresh on expiry

---

# Environment Model

An Environment represents the **actual provisioned resources** created by Terraform.

The system stores Terraform outputs in the Environment model.

Examples:

- VPC ID
- ECS cluster ARN
- ALB ARN
- ALB DNS
- ECR repository URL
- ECS task execution role ARN
- Subnet IDs
- Security group IDs

Environment status tracks provisioning lifecycle:


PENDING
PROVISIONING
ACTIVE
ERROR
DESTROYING
DESTROYED


These states ensure infrastructure provisioning is deterministic and recoverable.

**Event-Driven Sync**:
- Infrastructure service publishes `infrastructure.created` and `environment.updated` events
- Application service consumes events to sync infrastructure metadata
- Ensures `is_cloud_authenticated` flag is synced across services

---

# Application Model

Applications represent deployable services running inside an Infrastructure.

Each Application contains:

- GitHub repository URL
- branch
- commit hash
- Dockerfile path
- port (default: 8080)
- environment variables
- CPU allocation
- memory allocation
- storage allocation
- deployment status
- deployment URL
- build ID
- error message

Applications are deployed as **ECS services** inside the infrastructure cluster.

Multiple applications can run inside the same infrastructure.

**Deployment Fields**:
- `status`: CREATED, BUILDING, PUSHING_IMAGE, DEPLOYING, ACTIVE, FAILED
- `dockerfile_path`: Path to Dockerfile (default: "Dockerfile")
- `port`: Container port (default: 8080)
- `task_definition_arn`: ECS task definition ARN
- `service_arn`: ECS service ARN
- `target_group_arn`: ALB target group ARN
- `listener_rule_arn`: ALB listener rule ARN
- `deployment_url`: Public URL to access the application
- `build_id`: CodeBuild job ID
- `error_message`: Error details if deployment failed

---

# Environment Variables

Application environment variables are stored as:


envs = JSONField


Example:


{
"DATABASE_URL": "...",
"REDIS_URL": "...",
"NODE_ENV": "production"
}


These variables are injected into containers during deployment.

**PORT Environment Variable**:
The platform automatically injects `PORT={application.port}` so applications know which port to listen on.

Secrets are currently stored in plaintext (Level 1 secret management). Future versions may integrate encrypted storage or AWS Secrets Manager.

---

# Infrastructure Creation Workflow

When a user creates infrastructure:

1. User submits infrastructure configuration
2. Backend creates Infrastructure record (status: CREATED)
3. Infrastructure provisioning job is enqueued
4. Terraform worker processes the job:
   - Assumes role in user's AWS account
   - Executes Terraform apply
   - Captures outputs
   - Updates Environment record
5. Worker publishes `infrastructure.created` event
6. Worker publishes `environment.updated` event
7. Infrastructure becomes ACTIVE

The infrastructure now acts as a **mini application platform**.

**Async Processing**:
- Infrastructure provisioning runs in background worker
- User receives immediate response (202 Accepted)
- Status updates tracked in database

---

# Application Deployment Workflow

When a user deploys an application:

1. User submits GitHub repository and configuration
2. Application record is created (status: CREATED)
3. Deployment job is added to Redis queue
4. Deployment worker processes the job

**Deployment Pipeline (11 steps)**:

1. **Validate Infrastructure**: Check status is ACTIVE and cloud authenticated
2. **Create AWS Session**: Assume role with credential refresh logic
3. **Trigger CodeBuild**: Start build job in user's AWS account
4. **Wait for Build**: Poll until build completes (status: BUILDING)
5. **Create Task Definition**: Define ECS task with container config (status: DEPLOYING)
6. **Create Target Group**: Create ALB target group for the application
7. **Configure ALB Routing**: Create listener rule for path-based routing
8. **Verify Attachment**: Wait for target group to attach to ALB (5s + verification)
9. **Add Security Group Rule**: Allow traffic from ALB to container port
10. **Create ECS Service**: Launch containers with load balancer integration
11. **Wait for Stability**: Poll until service is running (status: ACTIVE)

**Error Handling**:
- Any failure updates status to FAILED
- Error message stored in `application.error_message`
- Build logs available in CloudWatch

---

# Build System

Application builds are performed using **AWS CodeBuild inside the user's AWS account**.

This ensures:

- build isolation
- no execution of user code in platform infrastructure
- build costs are billed to the user

**Build Process**:

1. CodeBuild clones GitHub repository (supports private repos with token)
2. Checks out specified branch/commit
3. Builds Docker image from Dockerfile
4. Tags image as `{app-name}-latest`
5. Pushes to user's ECR repository

**Buildspec**:
```yaml
version: 0.2
phases:
  pre_build:
    - Login to ECR
    - Clone repository
    - Checkout branch/commit
  build:
    - docker build -f $DOCKERFILE_PATH -t $APP_NAME:latest .
    - docker tag $APP_NAME:latest $ECR_URL:$APP_NAME-latest
  post_build:
    - docker push $ECR_URL:$APP_NAME-latest
```

**Important**:
- Use ECR Public Gallery for base images: `public.ecr.aws/docker/library/node:21-alpine`
- Avoids Docker Hub rate limiting (429 errors)
- CodeBuild project uses privileged mode for Docker builds

---

# Networking Model

Each Infrastructure creates a **dedicated VPC**.

All applications within that infrastructure run inside the same VPC.

Networking architecture:


Internet
│
▼
Application Load Balancer (port 80/443)
│
├── Listener Rule: /app-a* → Target Group A (port 8080)
│   └ ECS Service A (2 tasks in private subnets)
│
├── Listener Rule: /app-b* → Target Group B (port 3000)
│   └ ECS Service B (2 tasks in private subnets)
│
└── Listener Rule: /app-c* → Target Group C (port 8080)
    └ ECS Service C (2 tasks in private subnets)


**ECS Networking**:
- Tasks run in `awsvpc` mode
- Each container gets its own ENI and private IP
- Tasks run in private subnets (no public IP)
- NAT Gateway for outbound internet access

**Security Groups**:
- ALB security group: Allows 80/443 from internet
- Container security group: Allows {app.port} from ALB security group
- Automatically configured during deployment

---

# Security Model

Isolation occurs at multiple levels.

**Infrastructure isolation**:
Each Infrastructure has its own VPC.

**Application isolation**:
Each application runs as an independent ECS service.

**Network isolation**:
Security groups control traffic between ALB and containers.

**Build isolation**:
Application builds run in AWS CodeBuild within the user's account.

**IAM isolation**:
Platform uses AssumeRole with temporary credentials (1 hour TTL).

**Security Group Rules**:
- ALB → Container: tcp:{application.port} (auto-created)
- Internet → ALB: tcp:80, tcp:443
- Container → Internet: All traffic (via NAT Gateway)

---

# Application Routing

Applications are exposed through the infrastructure's ALB.

**Current routing model**:


http://{alb-dns}/{app-name}/*


Example:
```
http://infra-019ccc43-alb-123.us-west-2.elb.amazonaws.com/my-app
```

**ALB Configuration**:
- Listener: HTTP on port 80
- Rules: Path-based routing with priority
- Target Groups: One per application
- Health Checks: GET / every 30s, timeout 10s, accepts 200-499

**Future routing model**:


app-name.platform-domain.com


---

# Deployment Lifecycle States

Applications move through states:


CREATED → BUILDING → PUSHING_IMAGE → DEPLOYING → ACTIVE
                                                    ↓
                                                  FAILED


**State Transitions**:
- `CREATED`: Application record created, waiting for deployment
- `BUILDING`: CodeBuild is building Docker image
- `PUSHING_IMAGE`: Image is being pushed to ECR
- `DEPLOYING`: Creating ECS service and ALB configuration
- `ACTIVE`: Application is running and accessible
- `FAILED`: Deployment failed (check error_message)

These states ensure deterministic deployments.

---

# Async Processing

**Infrastructure Provisioning**:
- Queue: Redis
- Worker: `terraform_worker.py`
- Processing: Terraform apply in background
- Events: Publishes to RabbitMQ after completion

**Application Deployment**:
- Queue: Redis
- Worker: `deployment_worker.py`
- Processing: 11-step deployment pipeline
- Polling: CodeBuild status, ECS service stability

**Event Bus**:
- Technology: RabbitMQ
- Events: `infrastructure.created`, `environment.updated`
- Consumers: Application service syncs infrastructure metadata

---

# Logging & Monitoring

**Application Logs**:
- Location: AWS CloudWatch Logs
- Log Group: `/ecs/{app-name}-task`
- Retention: 7 days (configurable)

**Build Logs**:
- Location: AWS CloudWatch Logs
- Log Group: `/aws/codebuild/launchpad-build-{infra-id}`
- Access: Via build_id in application record

**Deployment Status**:
- Database: `api_application` table
- Fields: `status`, `error_message`, `build_id`, `deployment_url`

**Viewing Logs**:
```bash
# Container logs
aws logs tail /ecs/{app-name}-task --follow

# Build logs
aws logs tail /aws/codebuild/launchpad-build-{infra-id} --follow
```

---

# Port Configuration

**Default Port**: 8080

Applications must listen on the port specified in the `port` field (default: 8080).

**How It Works**:
1. User specifies `port` when creating application (optional, defaults to 8080)
2. Platform injects `PORT` environment variable into container
3. Application reads `process.env.PORT` and listens on that port
4. ALB health checks and routes traffic to that port

**Example (Node.js)**:
```javascript
const PORT = process.env.PORT || 3000;
app.listen(PORT, '0.0.0.0', () => {
  console.log(`Server listening on port ${PORT}`);
});
```

**Important**:
- Listen on `0.0.0.0`, not `localhost`
- Respond to `GET /` for health checks
- Use ECR Public Gallery for base images

---

# Dockerfile Requirements

**Base Images**:
Use ECR Public Gallery to avoid Docker Hub rate limits:

```dockerfile
# ✅ Correct
FROM public.ecr.aws/docker/library/node:21-alpine

# ❌ Wrong (hits rate limits)
FROM node:21-alpine
```

**Port Configuration**:
```dockerfile
EXPOSE 8080  # Document the port (optional)
```

**Health Check Endpoint**:
Application must respond to `GET /` with 200-499 status code.

---

# Edge Cases & Fixes

**Fixed Issues**:
1. ✅ Target group not attached to ALB → Added 5s wait + verification
2. ✅ Invalid Fargate CPU/Memory → Auto-round to valid combinations
3. ✅ Docker Hub rate limiting → Use ECR Public Gallery
4. ✅ Queue dequeue errors → Handle None gracefully
5. ✅ Duplicate resources → Idempotent creation
6. ✅ Service not stable → Wait for running tasks
7. ✅ Image tag mismatch → Use `{app-name}-latest` consistently
8. ✅ Security group blocking traffic → Auto-add ingress rules
9. ✅ Port mismatch → Configurable port field with PORT env var

**See**: `docs/DEPLOYMENT_EDGE_CASES.md` for comprehensive list

---

# CPU/Memory Configuration

**Fargate Valid Combinations**:
- 0.25 vCPU: 0.5-2 GB
- 0.5 vCPU: 1-4 GB
- 1 vCPU: 2-8 GB
- 2 vCPU: 4-16 GB
- 4 vCPU: 8-30 GB

Platform automatically rounds user input to nearest valid combination.

---

# Future Improvements

Possible enhancements include:

**CI/CD pipelines**
- automatic deployment on Git push
- GitHub webhooks integration

**Custom domains**
- user-provided DNS
- SSL/TLS certificates

**Secrets management**
- AWS Secrets Manager integration
- encrypted environment variables

**Observability**
- metrics dashboards
- log aggregation
- alerting

**Autoscaling**
- automatic ECS service scaling
- CPU/memory-based triggers

**Internal service discovery**
- DNS-based communication between services
- service mesh integration

**Multi-region**
- deploy to multiple AWS regions
- global load balancing

**Cost tracking**
- per-application cost breakdown
- budget alerts

---

# Design Philosophy

The platform aims to provide:

- infrastructure isolation
- reproducible deployments
- minimal operational complexity
- cloud-native architecture
- async processing for long-running operations
- event-driven architecture for service communication

The system treats cloud infrastructure as a **runtime for applications**, transforming GitHub repositories into running services.

---

# Documentation

Comprehensive documentation available in `/docs`:

- `SYSTEM_ARCHITECTURE.md` - Architecture diagrams
- `USER_WORKFLOWS.md` - User journey flows
- `USER_ONBOARDING_GUIDE.md` - Setup instructions
- `IAM_POLICIES.md` - IAM configuration
- `DEPLOYMENT_EDGE_CASES.md` - Known issues and fixes
- `QUICK_START.md` - Quick reference