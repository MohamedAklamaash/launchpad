# Launchpad IAM Policies

## Overview

This document defines the IAM policies required for Launchpad to operate in your AWS account.

---

## Automated Setup (Recommended)

Use our setup script to automatically configure IAM role and policies:

```bash
curl -fsSL https://raw.githubusercontent.com/launchpad/setup/main/create_aws_role.sh | bash
```

Or download and run manually:
```bash
wget https://raw.githubusercontent.com/launchpad/setup/main/create_aws_role.sh
chmod +x create_aws_role.sh
./create_aws_role.sh
```

The script creates:
- IAM role: `LaunchpadDeploymentRole`
- IAM policy: `LaunchpadDeploymentPolicy`
- Trust relationship with Launchpad platform account

---

## Manual Setup

If you prefer manual setup, follow these instructions.

### Trust Policy for Cross-Account Role

Create role `LaunchpadDeploymentRole` with this trust policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::221082203366:user/aklamaash-terraform"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

**Important**: 
- Platform Account ID: `221082203366`
- Platform User: `aklamaash-terraform`
- Role Name: `LaunchpadDeploymentRole` (exact name required)

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
        "codebuild:*"
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

### Using Automated Script (Recommended)

```bash
# Download and run
curl -fsSL https://raw.githubusercontent.com/launchpad/setup/main/create_aws_role.sh | bash

# Or manually
wget https://raw.githubusercontent.com/launchpad/setup/main/create_aws_role.sh
chmod +x create_aws_role.sh
./create_aws_role.sh
```

### Using AWS Console

1. **Create Role**:
   - Go to IAM → Roles → Create role
   - Select "AWS account" → "Another AWS account"
   - Enter Account ID: `221082203366`
   - Click Next
   - Click "Create policy" (opens new tab)
   - Paste the deployment policy JSON above
   - Name: `LaunchpadDeploymentPolicy`
   - Return to role creation tab
   - Attach `LaunchpadDeploymentPolicy`
   - Name: `LaunchpadDeploymentRole`
   - Click Create role

2. **Update Trust Policy**:
   - Open the role
   - Click "Trust relationships" tab
   - Click "Edit trust policy"
   - Replace with trust policy above
   - Save changes

### Using AWS CLI

```bash
# Create trust policy file
cat > trust-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::221082203366:user/aklamaash-terraform"
      },
      "Action": "sts:AssumeRole"
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
        "codebuild:*"
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

**CodeBuild Management**:
- Build Docker images from your code
- Run builds in your account (not Launchpad's)
- Access build logs and status

**CloudWatch Logs**:
- Store application logs
- Debug issues
- Monitor application health

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
- **AssumeRole Only**: No direct access to resources

This is more secure than trusting the entire account root.

### Recommendations

1. **Enable CloudTrail**: Monitor all API calls made by Launchpad
2. **Set Up Alerts**: CloudWatch alarms for unusual activity
3. **Regular Audits**: Review IAM policies and CloudTrail logs quarterly
4. **Resource Tagging**: All resources tagged with `ManagedBy: Launchpad`
5. **Cost Alerts**: Set up AWS Budgets to monitor spending

---

## Permission Breakdown

### Why Each Permission is Needed

---

## Troubleshooting

### Permission Denied Errors

If you see permission errors:

1. **Check Role Name**: Must be exactly `LaunchpadDeploymentRole`
2. **Check Trust Policy**: Launchpad account ID must be correct
3. **Check Policy Attachment**: Policy must be attached to role
4. **Check Region**: Some services are region-specific

### Testing Permissions

Test if role is configured correctly:

```bash
# Assume the role
aws sts assume-role \
  --role-arn arn:aws:iam::<YOUR_ACCOUNT_ID>:role/LaunchpadDeploymentRole \
  --role-session-name test-session

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

⚠️ **Warning**: This will prevent Launchpad from managing your infrastructure. Clean up resources first.

---

## Updates

This policy may be updated as Launchpad adds features. Check for updates:
- [GitHub](https://github.com/launchpad/iam-policies)
- [Documentation](https://docs.launchpad.example.com/iam)

**Version**: 1.0.0  
**Last Updated**: 2026-03-08
