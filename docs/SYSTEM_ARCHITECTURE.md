# Launchpad Platform - System Architecture

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         LAUNCHPAD PLATFORM                               │
│                      (Control Plane - Account: 221082203366)            │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐             │
│  │   Gateway    │    │   Identity   │    │   Payment    │             │
│  │   Service    │───▶│   Service    │    │   Service    │             │
│  │  (API GW)    │    │  (Auth)      │    │  (Billing)   │             │
│  └──────┬───────┘    └──────────────┘    └──────────────┘             │
│         │                                                               │
│         ├──────────────┬────────────────────────┐                      │
│         ▼              ▼                        ▼                       │
│  ┌──────────────┐ ┌──────────────┐      ┌──────────────┐             │
│  │Infrastructure│ │ Application  │      │   RabbitMQ   │             │
│  │   Service    │ │   Service    │◀────▶│  (Events)    │             │
│  │  (Infra Mgmt)│ │  (App Deploy)│      │              │             │
│  └──────┬───────┘ └──────┬───────┘      └──────────────┘             │
│         │                │                                              │
│         │                │              ┌──────────────┐               │
│         │                └─────────────▶│  PostgreSQL  │               │
│         │                               │  (Metadata)  │               │
│         │                               └──────────────┘               │
│         │                                                               │
│         │  ┌──────────────────────────────────────────┐               │
│         └─▶│     Terraform Worker (Background)        │               │
│            │  - Provisions infrastructure via Terraform│               │
│            │  - Stores state in customer S3/DynamoDB  │               │
│            └──────────────────────────────────────────┘               │
│                                                                          │
│  Platform IAM User: aklamaash-terraform                                 │
│  Permission: sts:AssumeRole on customer LaunchpadDeploymentRole        │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    │ AssumeRole
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      CUSTOMER AWS ACCOUNT                                │
│                         (Data Plane)                                     │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  IAM Role: LaunchpadDeploymentRole                                      │
│  Trust: arn:aws:iam::221082203366:user/aklamaash-terraform             │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                    VPC (Isolated Network)                        │   │
│  │                                                                  │   │
│  │  ┌────────────────┐         ┌────────────────┐                 │   │
│  │  │ Public Subnets │         │Private Subnets │                 │   │
│  │  │                │         │                │                 │   │
│  │  │  ┌──────────┐  │         │  ┌──────────┐ │                 │   │
│  │  │  │   ALB    │  │         │  │   ECS    │ │                 │   │
│  │  │  │          │  │         │  │ Cluster  │ │                 │   │
│  │  │  └────┬─────┘  │         │  │          │ │                 │   │
│  │  │       │        │         │  │ ┌──────┐ │ │                 │   │
│  │  │       │        │         │  │ │Task 1│ │ │                 │   │
│  │  │       │        │         │  │ └──────┘ │ │                 │   │
│  │  │       │        │         │  │ ┌──────┐ │ │                 │   │
│  │  │       │        │         │  │ │Task 2│ │ │                 │   │
│  │  │       │        │         │  │ └──────┘ │ │                 │   │
│  │  │       │        │         │  └──────────┘ │                 │   │
│  │  └───────┼────────┘         └────────────────┘                 │   │
│  │          │                           ▲                          │   │
│  │          │                           │                          │   │
│  │          └───────────────────────────┘                          │   │
│  │                    Routing Rules                                │   │
│  │                                                                  │   │
│  │  ┌────────────────┐         ┌────────────────┐                 │   │
│  │  │  NAT Gateway   │         │Internet Gateway│                 │   │
│  │  └────────────────┘         └────────────────┘                 │   │
│  │                                                                  │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                 │
│  │     ECR      │  │  CodeBuild   │  │ CloudWatch   │                 │
│  │ (Images)     │  │  (Builds)    │  │   (Logs)     │                 │
│  └──────────────┘  └──────────────┘  └──────────────┘                 │
│                                                                          │
│  ┌──────────────┐  ┌──────────────┐                                    │
│  │      S3      │  │  DynamoDB    │                                    │
│  │(TF State)    │  │ (TF Locks)   │                                    │
│  └──────────────┘  └──────────────┘                                    │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Service Architecture Details

### Control Plane Services

```
┌─────────────────────────────────────────────────────────────────┐
│                      Gateway Service                             │
│  - API Gateway / Load Balancer                                  │
│  - Request routing                                               │
│  - Rate limiting                                                 │
│  - CORS handling                                                 │
└────────────────────────┬────────────────────────────────────────┘
                         │
         ┌───────────────┼───────────────┐
         ▼               ▼               ▼
┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│  Identity   │  │Infrastructure│  │ Application │
│  Service    │  │   Service    │  │   Service   │
├─────────────┤  ├─────────────┤  ├─────────────┤
│- GitHub     │  │- Create     │  │- Create     │
│  OAuth      │  │  Infra      │  │  Apps       │
│- JWT Auth   │  │- Terraform  │  │- Deploy     │
│- User Mgmt  │  │  Worker     │  │  Apps       │
│- Invites    │  │- Env Status │  │- CodeBuild  │
└─────────────┘  └─────────────┘  └─────────────┘
       │                │                │
       └────────────────┼────────────────┘
                        ▼
              ┌─────────────────┐
              │    RabbitMQ     │
              │   (Event Bus)   │
              ├─────────────────┤
              │- user.created   │
              │- infra.created  │
              │- env.updated    │
              └─────────────────┘
```

---

## Data Flow Architecture

### Infrastructure Provisioning Flow

```
User Request
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ 1. Infrastructure Service                                    │
│    - Validates request                                       │
│    - Creates Infrastructure record (DB)                      │
│    - Creates Environment record (status: PENDING)            │
│    - Publishes infrastructure.created event                  │
│    - Enqueues provisioning job                               │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. Terraform Worker (Background)                             │
│    - Assumes role in customer account                        │
│    - Creates S3 bucket for Terraform state                   │
│    - Creates DynamoDB table for state locking                │
│    - Runs terraform init                                     │
│    - Runs terraform apply                                    │
│    - Captures outputs (VPC, ECS, ALB, ECR, etc.)            │
│    - Updates Environment record (status: ACTIVE)             │
│    - Publishes environment.updated event                     │
└─────────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. Application Service (Consumer)                            │
│    - Receives infrastructure.created event                   │
│    - Syncs Infrastructure to local DB                        │
│    - Receives environment.updated event                      │
│    - Syncs Environment to local DB                           │
│    - Infrastructure ready for deployments                    │
└─────────────────────────────────────────────────────────────┘
```

### Application Deployment Flow

```
User Request
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ 1. Application Service                                       │
│    - Validates infrastructure is ACTIVE                      │
│    - Creates Application record (status: CREATED)            │
│    - Validates resource quotas                               │
│    - Validates GitHub repository access                      │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. Deployment Service                                        │
│    - Assumes role in customer account                        │
│    - Creates/ensures CodeBuild project exists                │
│    - Triggers CodeBuild with repo URL, branch, commit        │
│    - Updates status: BUILDING                                │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. AWS CodeBuild (Customer Account)                          │
│    - Clones GitHub repository                                │
│    - Builds Docker image from Dockerfile                     │
│    - Tags image                                              │
│    - Pushes to ECR                                           │
│    - Updates status: PUSHING_IMAGE                           │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. Deployment Service (Continued)                            │
│    - Waits for build completion                              │
│    - Creates CloudWatch log group                            │
│    - Registers ECS task definition                           │
│    - Creates ALB target group                                │
│    - Creates ECS service                                     │
│    - Creates ALB listener rule                               │
│    - Updates status: DEPLOYING → ACTIVE                      │
│    - Returns deployment URL                                  │
└─────────────────────────────────────────────────────────────┘
```

---

## Event-Driven Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      RabbitMQ Exchange                       │
│                   infrastructure.events                      │
└────────────────────────┬────────────────────────────────────┘
                         │
         ┌───────────────┼───────────────┐
         ▼               ▼               ▼
┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│   Queue:    │  │   Queue:    │  │   Queue:    │
│ user.events │  │infra.events │  │ env.events  │
└──────┬──────┘  └──────┬──────┘  └──────┬──────┘
       │                │                │
       ▼                ▼                ▼
┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│  Consumer:  │  │  Consumer:  │  │  Consumer:  │
│Application  │  │Application  │  │Application  │
│  Service    │  │  Service    │  │  Service    │
└─────────────┘  └─────────────┘  └─────────────┘

Events Published:
- user.created (Identity Service)
- infrastructure.created (Infrastructure Service)
- environment.updated (Infrastructure Service)

Events Consumed:
- Application Service syncs all events to local DB
```

---

## Security Architecture

### Cross-Account Access Pattern

```
┌─────────────────────────────────────────────────────────────┐
│              Platform Account (221082203366)                 │
│                                                              │
│  ┌────────────────────────────────────────────────────┐    │
│  │  IAM User: aklamaash-terraform                      │    │
│  │  Access Key: AKIA...                                │    │
│  │  Secret Key: (stored in .env)                       │    │
│  │                                                      │    │
│  │  Policy: AllowAssumeCustomerRoles                   │    │
│  │  {                                                   │    │
│  │    "Action": "sts:AssumeRole",                      │    │
│  │    "Resource": "arn:aws:iam::*:role/                │    │
│  │                 LaunchpadDeploymentRole"            │    │
│  │  }                                                   │    │
│  └────────────────────────────────────────────────────┘    │
│                           │                                  │
└───────────────────────────┼──────────────────────────────────┘
                            │
                            │ sts:AssumeRole
                            ▼
┌─────────────────────────────────────────────────────────────┐
│              Customer Account (123456789012)                 │
│                                                              │
│  ┌────────────────────────────────────────────────────┐    │
│  │  IAM Role: LaunchpadDeploymentRole                  │    │
│  │                                                      │    │
│  │  Trust Policy:                                       │    │
│  │  {                                                   │    │
│  │    "Principal": {                                    │    │
│  │      "AWS": "arn:aws:iam::221082203366:user/        │    │
│  │              aklamaash-terraform"                    │    │
│  │    }                                                 │    │
│  │  }                                                   │    │
│  │                                                      │    │
│  │  Permissions:                                        │    │
│  │  - ec2:*, ecs:*, ecr:*, elasticloadbalancing:*     │    │
│  │  - iam:*, codebuild:*, logs:*                       │    │
│  │  - s3:*, dynamodb:*, kms:*                          │    │
│  └────────────────────────────────────────────────────┘    │
│                           │                                  │
│                           ▼                                  │
│              Temporary Session Credentials                   │
│              (Valid for 1 hour)                              │
│              - AccessKeyId: ASIA...                          │
│              - SecretAccessKey: ...                          │
│              - SessionToken: ...                             │
│                           │                                  │
│                           ▼                                  │
│              All AWS Operations                              │
│              (Terraform, CodeBuild, ECS, etc.)              │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

---

## Network Architecture (Customer VPC)

```
┌─────────────────────────────────────────────────────────────────┐
│                    Customer VPC (10.0.0.0/16)                    │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌────────────────────────────────────────────────────────┐    │
│  │              Public Subnets (2 AZs)                     │    │
│  │                                                          │    │
│  │  AZ-1: 10.0.0.0/24          AZ-2: 10.0.1.0/24          │    │
│  │                                                          │    │
│  │  ┌──────────────────┐      ┌──────────────────┐        │    │
│  │  │       ALB        │      │   NAT Gateway    │        │    │
│  │  │  (Port 80/443)   │      │  (Elastic IP)    │        │    │
│  │  └────────┬─────────┘      └────────┬─────────┘        │    │
│  │           │                         │                   │    │
│  └───────────┼─────────────────────────┼───────────────────┘    │
│              │                         │                        │
│              │                         │                        │
│  ┌───────────┼─────────────────────────┼───────────────────┐    │
│  │           │   Private Subnets (2 AZs)│                  │    │
│  │           │                         │                   │    │
│  │  AZ-1: 10.0.2.0/24          AZ-2: 10.0.3.0/24          │    │
│  │           │                         │                   │    │
│  │  ┌────────▼─────────┐      ┌───────▼──────────┐        │    │
│  │  │   ECS Tasks      │      │   ECS Tasks      │        │    │
│  │  │                  │      │                  │        │    │
│  │  │  ┌────────────┐  │      │  ┌────────────┐  │        │    │
│  │  │  │  App 1     │  │      │  │  App 1     │  │        │    │
│  │  │  │  (Task)    │  │      │  │  (Task)    │  │        │    │
│  │  │  └────────────┘  │      │  └────────────┘  │        │    │
│  │  │                  │      │                  │        │    │
│  │  │  ┌────────────┐  │      │  ┌────────────┐  │        │    │
│  │  │  │  App 2     │  │      │  │  App 2     │  │        │    │
│  │  │  │  (Task)    │  │      │  │  (Task)    │  │        │    │
│  │  │  └────────────┘  │      │  └────────────┘  │        │    │
│  │  │                  │      │                  │        │    │
│  │  └──────────────────┘      └──────────────────┘        │    │
│  │                                                          │    │
│  └──────────────────────────────────────────────────────────┘    │
│                                                                  │
│  Internet Gateway                                                │
│  Route Tables (Public/Private)                                   │
│  Security Groups (ALB, ECS)                                      │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘

Traffic Flow:
1. Internet → ALB (Public Subnet)
2. ALB → ECS Tasks (Private Subnet) via Target Groups
3. ECS Tasks → Internet via NAT Gateway (for pulling images, etc.)
```

---

## Technology Stack

### Control Plane
- **Language**: Python 3.14
- **Framework**: Django 5.2
- **API**: Django REST Framework
- **Database**: PostgreSQL
- **Message Queue**: RabbitMQ
- **Background Jobs**: Custom worker threads
- **Infrastructure as Code**: Terraform

### Data Plane (Customer Account)
- **Container Orchestration**: AWS ECS Fargate
- **Container Registry**: AWS ECR
- **Build System**: AWS CodeBuild
- **Load Balancer**: AWS Application Load Balancer
- **Networking**: AWS VPC, NAT Gateway
- **Logging**: AWS CloudWatch Logs
- **State Storage**: AWS S3 + DynamoDB

### External Integrations
- **Authentication**: GitHub OAuth
- **Source Control**: GitHub API
- **Cloud Provider**: AWS (via boto3)

---

## Scalability & Resilience

### Control Plane
- **Horizontal Scaling**: Multiple service instances behind load balancer
- **Database**: PostgreSQL with connection pooling
- **Message Queue**: RabbitMQ with persistent messages
- **Circuit Breakers**: Prevent cascade failures
- **Retry Logic**: Exponential backoff for transient failures

### Data Plane
- **Auto-scaling**: ECS service auto-scaling (future)
- **Multi-AZ**: Resources deployed across 2 availability zones
- **Health Checks**: ALB health checks for ECS tasks
- **Fault Tolerance**: ECS automatically replaces failed tasks

---

**Version**: 1.0.0  
**Last Updated**: 2026-03-08
