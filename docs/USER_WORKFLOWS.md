# Launchpad Platform - User Workflows

## Complete User Journey

```
┌─────────────────────────────────────────────────────────────────┐
│                    USER JOURNEY OVERVIEW                         │
└─────────────────────────────────────────────────────────────────┘

1. Account Setup (One-time)
   └─▶ 2. Infrastructure Creation (Per Environment)
       └─▶ 3. Application Deployment (Per Application)
           └─▶ 4. Application Management (Ongoing)
```

---

## 1. Account Setup Workflow

```
┌──────────────┐
│   User       │
└──────┬───────┘
       │
       │ 1. Run setup script in AWS account
       ▼
┌─────────────────────────────────────────────────────────┐
│  ./create_aws_role.sh                                    │
│  - Creates IAM role: LaunchpadDeploymentRole            │
│  - Attaches deployment policies                         │
│  - Configures trust with platform account               │
│  - Returns AWS Account ID                               │
└────────────────────────┬────────────────────────────────┘
                         │
                         │ 2. Sign up on platform
                         ▼
┌─────────────────────────────────────────────────────────┐
│  https://launchpad.example.com                           │
│  - Click "Sign in with GitHub"                          │
│  - Authorize GitHub OAuth                               │
│  - Grant repository access                              │
└────────────────────────┬────────────────────────────────┘
                         │
                         │ 3. Platform creates user
                         ▼
┌─────────────────────────────────────────────────────────┐
│  Identity Service                                        │
│  - Creates User record                                  │
│  - Stores GitHub token                                  │
│  - Publishes user.created event                         │
│  - Issues JWT token                                     │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
                   ┌──────────┐
                   │Dashboard │
                   │  Ready   │
                   └──────────┘
```

---

## 2. Infrastructure Creation Workflow

```
┌──────────────┐
│   User       │
└──────┬───────┘
       │
       │ 1. Click "Create Infrastructure"
       ▼
┌─────────────────────────────────────────────────────────┐
│  Dashboard Form                                          │
│  ┌────────────────────────────────────────────────┐    │
│  │ Name: production                                │    │
│  │ Cloud Provider: AWS                             │    │
│  │ AWS Account ID: 123456789012                    │    │
│  │ Region: us-west-2                               │    │
│  │ Max CPU: 4 vCPU                                 │    │
│  │ Max Memory: 8 GB                                │    │
│  └────────────────────────────────────────────────┘    │
└────────────────────────┬────────────────────────────────┘
                         │
                         │ 2. Submit request
                         ▼
┌─────────────────────────────────────────────────────────┐
│  Infrastructure Service                                  │
│  ┌────────────────────────────────────────────────┐    │
│  │ 1. Validate AWS Account ID                      │    │
│  │ 2. Assume LaunchpadDeploymentRole               │    │
│  │ 3. Create Infrastructure record (DB)            │    │
│  │ 4. Create Environment record (PENDING)          │    │
│  │ 5. Publish infrastructure.created event         │    │
│  │ 6. Enqueue provisioning job                     │    │
│  └────────────────────────────────────────────────┘    │
└────────────────────────┬────────────────────────────────┘
                         │
                         │ 3. Background processing
                         ▼
┌─────────────────────────────────────────────────────────┐
│  Terraform Worker                                        │
│  ┌────────────────────────────────────────────────┐    │
│  │ Status: PROVISIONING                            │    │
│  │                                                 │    │
│  │ 1. Assume role in customer account              │    │
│  │ 2. Create S3 bucket (terraform state)           │    │
│  │ 3. Create DynamoDB table (state locks)          │    │
│  │ 4. Run terraform init                           │    │
│  │ 5. Run terraform apply                          │    │
│  │    - Create VPC                                 │    │
│  │    - Create Subnets (public/private)            │    │
│  │    - Create NAT Gateway                         │    │
│  │    - Create Internet Gateway                    │    │
│  │    - Create ECS Cluster                         │    │
│  │    - Create ALB                                 │    │
│  │    - Create ECR Repository                      │    │
│  │    - Create IAM Roles                           │    │
│  │    - Create Security Groups                     │    │
│  │ 6. Capture terraform outputs                    │    │
│  │ 7. Update Environment (ACTIVE)                  │    │
│  │ 8. Publish environment.updated event            │    │
│  └────────────────────────────────────────────────┘    │
└────────────────────────┬────────────────────────────────┘
                         │
                         │ 4. Event propagation
                         ▼
┌─────────────────────────────────────────────────────────┐
│  Application Service (Consumer)                          │
│  - Receives infrastructure.created event                │
│  - Syncs Infrastructure to local DB                     │
│  - Receives environment.updated event                   │
│  - Syncs Environment to local DB                        │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
                   ┌──────────┐
                   │Infrastructure│
                   │   ACTIVE   │
                   │ (5-10 min) │
                   └──────────┘
```

---

## 3. Application Deployment Workflow

```
┌──────────────┐
│   User       │
└──────┬───────┘
       │
       │ 1. Prepare repository with Dockerfile
       ▼
┌─────────────────────────────────────────────────────────┐
│  GitHub Repository                                       │
│  ┌────────────────────────────────────────────────┐    │
│  │ my-app/                                         │    │
│  │ ├── Dockerfile                                  │    │
│  │ ├── package.json                                │    │
│  │ ├── src/                                        │    │
│  │ └── ...                                         │    │
│  └────────────────────────────────────────────────┘    │
└────────────────────────┬────────────────────────────────┘
                         │
                         │ 2. Click "Create Application"
                         ▼
┌─────────────────────────────────────────────────────────┐
│  Dashboard Form                                          │
│  ┌────────────────────────────────────────────────┐    │
│  │ Name: my-api                                    │    │
│  │ Infrastructure: production                      │    │
│  │ Repository: github.com/user/my-app              │    │
│  │ Branch: main                                    │    │
│  │ Commit: HEAD                                    │    │
│  │ Dockerfile Path: Dockerfile                     │    │
│  │ CPU: 0.5 vCPU                                   │    │
│  │ Memory: 1 GB                                    │    │
│  │ Environment Variables:                          │    │
│  │   NODE_ENV=production                           │    │
│  │   PORT=8000                                     │    │
│  └────────────────────────────────────────────────┘    │
└────────────────────────┬────────────────────────────────┘
                         │
                         │ 3. Submit request
                         ▼
┌─────────────────────────────────────────────────────────┐
│  Application Service                                     │
│  ┌────────────────────────────────────────────────┐    │
│  │ 1. Validate infrastructure is ACTIVE            │    │
│  │ 2. Check resource quotas                        │    │
│  │ 3. Validate GitHub repository access            │    │
│  │ 4. Create Application record (CREATED)          │    │
│  └────────────────────────────────────────────────┘    │
└────────────────────────┬────────────────────────────────┘
                         │
                         │ 4. Click "Deploy"
                         ▼
┌─────────────────────────────────────────────────────────┐
│  Application Deployment Service                          │
│  ┌────────────────────────────────────────────────┐    │
│  │ Status: BUILDING                                │    │
│  │                                                 │    │
│  │ 1. Assume role in customer account              │    │
│  │ 2. Create/ensure CodeBuild project              │    │
│  │ 3. Trigger CodeBuild with:                      │    │
│  │    - Repository URL                             │    │
│  │    - Branch                                     │    │
│  │    - Commit hash                                │    │
│  │    - Dockerfile path                            │    │
│  │    - ECR repository URL                         │    │
│  └────────────────────────────────────────────────┘    │
└────────────────────────┬────────────────────────────────┘
                         │
                         │ 5. Build process
                         ▼
┌─────────────────────────────────────────────────────────┐
│  AWS CodeBuild (Customer Account)                        │
│  ┌────────────────────────────────────────────────┐    │
│  │ 1. Clone GitHub repository                      │    │
│  │ 2. Checkout specified commit                    │    │
│  │ 3. Build Docker image:                          │    │
│  │    docker build -f Dockerfile -t my-api .       │    │
│  │ 4. Tag image:                                   │    │
│  │    docker tag my-api:latest ecr-url:my-api      │    │
│  │ 5. Push to ECR:                                 │    │
│  │    docker push ecr-url:my-api                   │    │
│  └────────────────────────────────────────────────┘    │
└────────────────────────┬────────────────────────────────┘
                         │
                         │ 6. Build complete
                         ▼
┌─────────────────────────────────────────────────────────┐
│  Application Deployment Service (Continued)              │
│  ┌────────────────────────────────────────────────┐    │
│  │ Status: DEPLOYING                               │    │
│  │                                                 │    │
│  │ 1. Create CloudWatch log group                  │    │
│  │ 2. Register ECS task definition:                │    │
│  │    - Container image from ECR                   │    │
│  │    - CPU/Memory allocation                      │    │
│  │    - Environment variables                      │    │
│  │    - Port mappings (8000)                       │    │
│  │ 3. Create ALB target group                      │    │
│  │ 4. Create ECS service:                          │    │
│  │    - Task definition                            │    │
│  │    - Desired count: 1                           │    │
│  │    - Private subnets                            │    │
│  │    - Security groups                            │    │
│  │    - Target group                               │    │
│  │ 5. Create ALB listener rule:                    │    │
│  │    - Path: /my-api*                             │    │
│  │    - Forward to target group                    │    │
│  │ 6. Update status: ACTIVE                        │    │
│  │ 7. Return deployment URL                        │    │
│  └────────────────────────────────────────────────┘    │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
                   ┌──────────┐
                   │Application│
                   │  ACTIVE   │
                   │(5-15 min) │
                   └──────────┘
                         │
                         │ 8. Access application
                         ▼
┌─────────────────────────────────────────────────────────┐
│  http://alb-dns.amazonaws.com/my-api                     │
│  - Traffic flows through ALB                            │
│  - Routes to ECS task in private subnet                 │
│  - Application responds                                 │
└─────────────────────────────────────────────────────────┘
```

---

## 4. Application Management Workflow

### View Logs

```
User → Dashboard → Application → View Logs
                                      │
                                      ▼
                            AWS CloudWatch Logs
                            /ecs/my-api-task
                                      │
                                      ▼
                            Real-time log stream
```

### Update Application

```
User → Update code → Push to GitHub
                          │
                          ▼
User → Dashboard → Application → Deploy
                                      │
                                      ▼
                            Repeat deployment workflow
                            (New version deployed)
```

### Delete Application

```
User → Dashboard → Application → Delete
                                      │
                                      ▼
                            Application Service
                            - Delete ECS service
                            - Delete task definition
                            - Delete target group
                            - Delete listener rule
                            - Delete application record
```

---

## Traffic Flow (Runtime)

```
┌──────────┐
│  User    │
│ Browser  │
└────┬─────┘
     │
     │ HTTP Request
     │ http://alb-dns/my-api/users
     ▼
┌─────────────────────────────────────────────────────────┐
│  Application Load Balancer (Customer Account)            │
│  - Receives request                                      │
│  - Matches path pattern: /my-api*                        │
│  - Routes to target group                                │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│  Target Group                                            │
│  - Health check: /                                       │
│  - Forwards to healthy ECS tasks                         │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│  ECS Task (Private Subnet)                               │
│  - Container running on port 8000                        │
│  - Processes request                                     │
│  - Returns response                                      │
└────────────────────────┬────────────────────────────────┘
                         │
                         │ Response
                         ▼
                    ┌─────────┐
                    │  User   │
                    │ Browser │
                    └─────────┘
```

---

## Error Handling Workflows

### Infrastructure Provisioning Failed

```
Terraform Worker
    │
    │ Error during terraform apply
    ▼
Environment status: ERROR
    │
    ▼
User Dashboard shows error
    │
    ▼
User can:
- View error logs
- Retry provisioning
- Delete infrastructure
```

### Application Build Failed

```
CodeBuild
    │
    │ Build error (e.g., Dockerfile syntax)
    ▼
Application status: FAILED
    │
    ▼
User Dashboard shows error
    │
    ▼
User can:
- View CodeBuild logs
- Fix Dockerfile
- Retry deployment
```

### Application Container Crashed

```
ECS Task
    │
    │ Container exits with error
    ▼
ECS automatically restarts task
    │
    ▼
If continues failing:
- ALB marks as unhealthy
- User sees in dashboard
    │
    ▼
User can:
- View CloudWatch logs
- Fix application code
- Redeploy
```

---

## Multi-Application Workflow

```
Infrastructure: production
    │
    ├─▶ Application: api
    │   └─▶ URL: http://alb-dns/api
    │
    ├─▶ Application: admin
    │   └─▶ URL: http://alb-dns/admin
    │
    └─▶ Application: worker
        └─▶ URL: http://alb-dns/worker

All applications:
- Share same VPC
- Share same ECS cluster
- Share same ALB
- Have separate target groups
- Have separate listener rules
- Run in isolation (separate tasks)
```

---

## Resource Quota Management

```
Infrastructure: max_cpu=4, max_memory=8

Application 1: cpu=1, memory=2  ✓ Allowed
Application 2: cpu=1, memory=2  ✓ Allowed
Application 3: cpu=2, memory=4  ✓ Allowed
Application 4: cpu=1, memory=1  ✗ Denied (exceeds quota)

User must:
- Delete an application, OR
- Increase infrastructure limits
```

---

**Version**: 1.0.0  
**Last Updated**: 2026-03-08
