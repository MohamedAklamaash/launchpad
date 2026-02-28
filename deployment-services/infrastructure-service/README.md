# Infrastructure Service API

This service manages cloud infrastructure metadata and orchestrates automated provisioning using Terraform. It is responsible for setting up secure baselines connected to the user's personal cloud environments.

## Architecture & Security Baseline
When integrated with AWS, this service deploys a highly secure, automated baseline architecture in the user's AWS account.
The provisioned baseline enforces:
- **Networking**: VPC across 2 AZs, private/public subnets, and solitary cost-optimized NAT Gateway.
- **Security & Logging**: KMS-encrypted multi-region CloudTrail, VPC Flow Logs to CloudWatch, and Compute Optimizer enablement.
- **Identity & Access Management**: Dedicated execution roles adhering to the principle of least privilege.
- **State Management**: Automated S3 remote state tracking encrypted and localized strictly within the user's AWS account.

## Base URL
`/api/v1/`

## Endpoints

**Note on Trailing Slashes**: `APPEND_SLASH` is disabled. Ensure your request URLs match the definitions exactly.

**Authentication**: Attach `Authorization: Bearer <access_token>` header. CSRF is exempted for these API endpoints.

### 1. List Infrastructures
- **URL**: `/infrastructures/`
- **Method**: `GET`
- **Description**: Returns all infrastructures owned by the authenticated user.
- **Response**: `200 OK`
  ```json
  [
    {
      "id": "uuid",
      "name": "string",
      "cloud_provider": "string",
      "max_cpu": 0.0,
      "max_memory": 0.0,
      "is_cloud_authenticated": boolean,
      "metadata": {},
      "created_at": "ISO-8601",
      "updated_at": "ISO-8601",
      "invited_users": ["uuid"]
    }
  ]
  ```

### 2. Create Infrastructure (Provisioning)
- **URL**: `/infrastructures/`
- **Method**: `POST`
- **Description**: Creates a new infrastructure record and asynchronously fires `terraform apply` using temporary STS credentials obtained from the user's provided access/secret keys. Bootstraps an S3 backend in the user's account before execution.
- **script**: `[text](../../app_scripts/create_aws_role.sh)`
  **description**: run this script to create temp credentials for the user
- **Body**:
  ```json
  {
    "name": "string",
    "cloud_provider": "AWS",
    "max_cpu": 0.0,
    "max_memory": 0.0,
    "metadata": {
      "aws_access_key_id": "...",
      "aws_secret_access_key": "..."
    }
  }
  ```
- **Response**: `201 Created`

### 3. Get Infrastructure Detail
- **URL**: `/infrastructures/<id>/`
- **Method**: `GET`
- **Response**: `200 OK`

### 4. Update Infrastructure
- **URL**: `/infrastructures/<id>/`
- **Method**: `PUT`
- **Body**: (Any subset of fields)
- **Response**: `200 OK`

### 5. Delete Infrastructure (Deprovisioning)
- **URL**: `/infrastructures/<id>/`
- **Method**: `DELETE`
- **Description**: Deletes the infrastructure record and synchronously triggers `terraform destroy` utilizing freshly rotated temporary STS credentials to completely wipe all provisioned resources in the user's account.
- **Response**: `204 No Content`

## Required User AWS Permissions
For the service to successfully provision the baseline infra, the provided AWS credentials must assume a role/user with the following attached managed policies:
- `AmazonEC2FullAccess`
- `AmazonECS_FullAccess`
- `AmazonEKSClusterPolicy`
- `AmazonEKSServicePolicy`
- `AmazonEKSWorkerNodePolicy`
- `AmazonEKS_CNI_Policy`
- `AmazonS3FullAccess`
- `AmazonVPCFullAccess`
- `CloudWatchFullAccess`
- `ElasticLoadBalancingFullAccess`

## Automated Cost Optimization Cron
The AWS baseline includes Compute Optimizer which gathers intelligence over time. To act on this intelligence programmatically, the service includes a periodic enforcement script. 
A helper script automatically schedules the enforcement cron job inside your deployment container using `python-crontab` immediately upon application startup. You do not need to run any manual commands; simply starting the server (`runserver` or WSGI server) will actively inject the weekly cron schedule.
