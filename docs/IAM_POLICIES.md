# Launchpad IAM Policies

## Overview

This document defines the IAM policies required for Launchpad to operate in your AWS account.

---

## Automated Setup (Recommended)

**You do not run these scripts by hand from this doc.** The Launchpad dashboard generates
the exact command — with your infrastructure's IDs, callback URL, and one-time onboarding
token pre-filled — when you create an infrastructure. Copy it from there and run it in a
shell with access to your AWS account. See the
[User Onboarding Guide](./USER_ONBOARDING_GUIDE.md#step-3--run-the-aws-setup-command).

The generated bootstrap command looks like:

```bash
export LAUNCHPAD_INFRA_ID=<your-infra-uuid>
export LAUNCHPAD_EXTERNAL_ID=<your-infra-uuid>
export LAUNCHPAD_CALLBACK_URL=https://<gateway>/api/infrastructures/onboarding/callback
export LAUNCHPAD_ONBOARDING_TOKEN=<one-time-token>
export LAUNCHPAD_COMPUTE_TYPE=<ecs_fargate|eks>
curl -sSL https://raw.githubusercontent.com/MohamedAklamaash/launchpad/<pinned-ref>/app_scripts/create_aws_role.sh | bash
```

`LAUNCHPAD_COMPUTE_TYPE` matches the compute target you chose for the infrastructure
(`ecs_fargate` is the default). `eks` adds the scoped EKS statements described in
[EKS permissions](#eks-permissions-kubernetes-infrastructures); ECS-only accounts never
receive them.

The script creates:
- IAM role: `LaunchpadDeploymentRole`
- IAM policy: `LaunchpadDeploymentPolicy`
- A trust relationship scoped to the Launchpad platform principal **and** your
  infrastructure's `ExternalId`
- A callback to Launchpad that verifies the token and starts provisioning

It is idempotent: re-running refreshes the policy **and** trust policy in place. Re-running
it with a script API key is also the refresh path for when Launchpad's required permissions
widen — see [Keeping the policy current](#keeping-the-policy-current).

---

## EKS permissions (Kubernetes infrastructures)

When an infrastructure is created with the **Kubernetes** compute target, the onboarding
script (run with `LAUNCHPAD_COMPUTE_TYPE=eks`) adds EKS statements to
`LaunchpadDeploymentPolicy`. They are deliberately **not** `eks:*` on `*`:

- `eks:CreateCluster`, `eks:List*`, and `eks:Describe*` on `*` (create and read-only
  actions cannot be resource-scoped before the cluster exists).
- All mutating EKS actions scoped to `arn:aws:eks:*:<your-account-id>:cluster/infra-*`
  and its `access-entry/*`, `addon/*`, and `nodegroup/*` children — Launchpad names every
  cluster it creates `infra-<id>`, so the role can only mutate clusters Launchpad owns.
- An explicit `Deny` on `eks:*AccessEntry*` and `eks:*AccessPolicy*` for clusters not
  named `infra-*`.

Why the scoping matters: `eks:CreateAccessEntry` on an unscoped resource would let the
role grant itself cluster-admin on **any pre-existing EKS cluster in your account**. The
resource scoping plus the explicit Deny confines Launchpad to the clusters it provisions;
clusters you already run stay untouchable even if the Allow list widens later.

For EKS infrastructures the script also sets the role's `MaxSessionDuration` to 7200
seconds — cluster provisioning can exceed the 1-hour default session.

The generated script is the authoritative statement list
([`app_scripts/create_aws_role.sh`](https://github.com/MohamedAklamaash/launchpad/blob/main/app_scripts/create_aws_role.sh));
if this section and the script ever disagree, the script wins.

---

## Manual Setup

If you prefer to configure the role by hand instead of running the script, follow these
instructions. **You must include the `ExternalId` condition** — the backend always
presents it on AssumeRole, so a trust policy without it will fail with `AccessDenied`.

Manual setup covers **ECS Fargate infrastructures only** — the policy below has no EKS
statements. Kubernetes infrastructures require the script (with
`LAUNCHPAD_COMPUTE_TYPE=eks`), which owns the scoped EKS statement list.

### Trust Policy for Cross-Account Role

Create role `LaunchpadDeploymentRole` with this trust policy. Replace
`<YOUR_INFRA_ID>` with the infrastructure UUID shown in the dashboard (this is the
`ExternalId`):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::221082203366:user/aklamaash-terraform"
      },
      "Action": "sts:AssumeRole",
      "Condition": {
        "StringEquals": { "sts:ExternalId": "<YOUR_INFRA_ID>" }
      }
    }
  ]
}
```

**Important**: 
- Platform Account ID: `221082203366`
- Platform User: `aklamaash-terraform`
- Role Name: `LaunchpadDeploymentRole` (exact name required)
- `sts:ExternalId`: your infrastructure UUID (required — prevents confused-deputy abuse)

### Deployment Policy

Attach this policy to the role:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ec2:*",
        "ecs:*",
        "elasticloadbalancing:*",
        "ecr:*",
        "logs:*",
        "s3:*",
        "dynamodb:*",
        "codebuild:*",
        "rds:*",
        "elasticache:*",
        "secretsmanager:*"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": "iam:*",
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": "kms:*",
      "Resource": "*"
    }
  ]
}
```

---

## Setup Instructions

### Using the dashboard-generated script (Recommended)

Create the infrastructure in the dashboard and run the command it generates (it carries
your IDs + one-time token). See the
[User Onboarding Guide](./USER_ONBOARDING_GUIDE.md#step-3--run-the-aws-setup-command).
The script source lives at
[`app_scripts/create_aws_role.sh`](https://github.com/MohamedAklamaash/launchpad/blob/main/app_scripts/create_aws_role.sh);
the dashboard pins it to a specific commit and injects the env vars.

### Using AWS Console

1. **Create the policy**: IAM → Policies → Create policy → paste the deployment policy
   JSON above → name it `LaunchpadDeploymentPolicy`.
2. **Create the role**: IAM → Roles → Create role → "AWS account" → "Another AWS account"
   → Account ID `221082203366`. **Check "Require external ID"** and enter your
   infrastructure UUID. Attach `LaunchpadDeploymentPolicy`. Name the role
   `LaunchpadDeploymentRole`.
3. **Verify the trust policy** matches the JSON above (principal scoped to
   `aklamaash-terraform` and the `sts:ExternalId` condition present).

### Using AWS CLI

```bash
# Your infrastructure UUID from the dashboard — also used as the ExternalId.
INFRA_ID=<YOUR_INFRA_ID>

# Create trust policy file (note the ExternalId condition — required)
cat > trust-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::221082203366:user/aklamaash-terraform"
      },
      "Action": "sts:AssumeRole",
      "Condition": {
        "StringEquals": { "sts:ExternalId": "${INFRA_ID}" }
      }
    }
  ]
}
EOF

# Create deployment policy file
cat > deployment-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ec2:*",
        "ecs:*",
        "elasticloadbalancing:*",
        "ecr:*",
        "logs:*",
        "s3:*",
        "dynamodb:*",
        "codebuild:*",
        "rds:*",
        "elasticache:*",
        "secretsmanager:*"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": "iam:*",
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": "kms:*",
      "Resource": "*"
    }
  ]
}
EOF

# Get your account ID
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

# Create policy
aws iam create-policy \
  --policy-name LaunchpadDeploymentPolicy \
  --policy-document file://deployment-policy.json

# Create role
aws iam create-role \
  --role-name LaunchpadDeploymentRole \
  --assume-role-policy-document file://trust-policy.json

# Attach policy to role
aws iam attach-role-policy \
  --role-name LaunchpadDeploymentRole \
  --policy-arn arn:aws:iam::${ACCOUNT_ID}:policy/LaunchpadDeploymentPolicy

# Cleanup
rm trust-policy.json deployment-policy.json

echo "Role ARN: arn:aws:iam::${ACCOUNT_ID}:role/LaunchpadDeploymentRole"
```

---

## Keeping the policy current

When Launchpad adds capabilities, `LaunchpadDeploymentPolicy` may gain actions. If your
deployments start failing with `AccessDenied`, re-run `create_aws_role.sh` — surfaced in
the dashboard as the *Refresh policy script*. The same idempotent script re-applies the
latest policy **and** trust policy in place; you don't recreate the role.

**On a Kubernetes infrastructure, always export `LAUNCHPAD_COMPUTE_TYPE=eks` when
re-running the script.** The EKS statements are added only when that variable is set;
a refresh without it re-applies the ECS-only policy and silently drops the EKS
permissions.

Because one script owns the action list, the bootstrap and refresh paths can never drift
apart, so a refresh never narrows your permissions by accident.

The refresh snippet also carries a **per-user API key** (`LAUNCHPAD_API_KEY`) and posts to
a policy-refresh callback so Launchpad records who ran the refresh, against which account,
and when. The key is issued from the dashboard ("Generate API key"), shown once, stored
only as a hash, and rotated on re-issue. This attribution callback is best-effort — if it
can't reach Launchpad, the IAM update still succeeds.

---

## Permission Breakdown

### Why Each Permission is Needed

**EC2 (VPC Management)**:
- Create isolated network for your applications
- Public/private subnets for security
- NAT Gateway for outbound internet access
- Security groups for network isolation

**ECS Management**:
- Create cluster to run containers
- Register task definitions (container specs)
- Create services (long-running containers)
- Run and manage tasks

**ECR Management**:
- Store Docker images
- Pull images for deployment
- Manage image lifecycle

**ALB Management**:
- Expose applications to internet
- Route traffic to containers
- Health checks
- SSL/TLS termination (future)

**IAM Management**:
- Create service roles for ECS tasks
- Create service roles for CodeBuild
- PassRole to allow services to assume roles
- Manage permissions for created resources

**EKS (Kubernetes infrastructures only)**:
- Create and manage the `infra-*` EKS Auto Mode cluster
- Access entries for the provisioning and deploy identities
- Cluster add-ons (VPC CNI network policy enforcement)
- Scoped so pre-existing clusters in your account are untouchable (see
  [EKS permissions](#eks-permissions-kubernetes-infrastructures))

**CodeBuild Management**:
- Build Docker images from your code
- Run builds in your account (not Launchpad's)
- Access build logs and status

**CloudWatch Logs**:
- Store application logs
- Debug issues
- Monitor application health

**RDS / ElastiCache / Secrets Manager**:
- Create and manage managed PostgreSQL, MySQL, Redis, and DocumentDB instances you
  provision from the dashboard, all inside your existing private subnets
- Store and retrieve the connection credentials Launchpad injects into your app
  containers — the plaintext credential never transits Launchpad's own systems

**S3**:
- Store Terraform state
- Version control infrastructure changes
- Backup and recovery

**DynamoDB**:
- Lock Terraform state during operations
- Prevent concurrent modifications
- Ensure consistency

**KMS**:
- Encrypt Terraform state
- Encrypt sensitive data
- Key management for resources

---

## Security Considerations

### Principle of Least Privilege

The policy grants broad permissions within specific services. This is necessary because:
- Infrastructure requirements vary per application
- Terraform needs flexibility to create resources
- Dynamic resource creation (CodeBuild projects, IAM roles)

### Trust Policy Security

The trust policy is restricted to:
- **Specific Account**: `221082203366` (Launchpad platform)
- **Specific User**: `aklamaash-terraform` (not account root)
- **Specific ExternalId**: your infrastructure UUID — the backend must present this exact
  value on AssumeRole, which blocks confused-deputy abuse even if someone learns your
  account ID and role name
- **AssumeRole Only**: No direct access to resources

This is far more secure than trusting the entire account root.

### Recommendations

1. **Enable CloudTrail**: Monitor all API calls made by Launchpad
2. **Set Up Alerts**: CloudWatch alarms for unusual activity
3. **Regular Audits**: Review IAM policies and CloudTrail logs quarterly
4. **Resource Tagging**: All resources tagged with `ManagedBy: Launchpad`
5. **Cost Alerts**: Set up AWS Budgets to monitor spending

---

## Troubleshooting

### Permission Denied Errors

If you see permission errors:

1. **Check Role Name**: Must be exactly `LaunchpadDeploymentRole`
2. **Check Trust Policy**: Launchpad account ID must be correct **and** the
   `sts:ExternalId` condition must equal your infrastructure UUID. A missing or wrong
   ExternalId is the most common cause of `AccessDenied` right after onboarding —
   re-run the bootstrap/refresh script to fix the trust policy in place.
3. **Check Policy Attachment**: Policy must be attached to role
4. **Check Region**: Some services are region-specific
5. **Permissions widened?**: Re-run `create_aws_role.sh` (the dashboard's *Refresh policy*
   snippet) to pick up newly required actions.

### Testing Permissions

Test if role is configured correctly:

```bash
# Assume the role (the --external-id must match your infrastructure UUID,
# exactly as the backend sends it)
aws sts assume-role \
  --role-arn arn:aws:iam::<YOUR_ACCOUNT_ID>:role/LaunchpadDeploymentRole \
  --role-session-name test-session \
  --external-id <YOUR_INFRA_ID>

# Use temporary credentials to test
export AWS_ACCESS_KEY_ID=<from above>
export AWS_SECRET_ACCESS_KEY=<from above>
export AWS_SESSION_TOKEN=<from above>

# Test VPC creation
aws ec2 describe-vpcs
```

---

## Revoking Access

To revoke Launchpad's access:

1. **Delete Role**:
   ```bash
   aws iam delete-role --role-name LaunchpadDeploymentRole
   ```

2. **Delete Policy**:
   ```bash
   aws iam delete-policy --policy-arn arn:aws:iam::<YOUR_ACCOUNT_ID>:policy/LaunchpadDeploymentPolicy
   ```

WARNING: This will prevent Launchpad from managing your infrastructure. Clean up resources first.

---

## Updates

This policy may be updated as Launchpad adds features. Check for updates:
- [GitHub](https://github.com/MohamedAklamaash/launchpad/blob/main/docs/IAM_POLICIES.md)

**Version**: 2.1.0  
**Last Updated**: 2026-08-30
