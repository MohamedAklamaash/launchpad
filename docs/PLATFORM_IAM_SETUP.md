# Launchpad Platform Setup - Internal Documentation

## Platform IAM User Configuration

This document describes the IAM setup required in the **Launchpad platform AWS account** (Account ID: `221082203366`).

---

## Overview

The platform uses an IAM user `aklamaash-terraform` to assume roles in customer AWS accounts for infrastructure provisioning and application deployment.

---

## IAM User: aklamaash-terraform

### 1. Create IAM User

```bash
aws iam create-user --user-name aklamaash-terraform
```

### 2. Create Access Keys

```bash
aws iam create-access-key --user-name aklamaash-terraform
```

**Save the credentials securely**:
- Access Key ID: `AKIA...`
- Secret Access Key: `...`

Store these in:
- Infrastructure service: `.env` file
- Application service: `.env` file
- Environment variables: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`

---

## Required Permissions

The `aklamaash-terraform` user needs permission to assume roles in customer accounts.

### Policy: AllowAssumeCustomerRoles

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AssumeCustomerDeploymentRoles",
      "Effect": "Allow",
      "Action": "sts:AssumeRole",
      "Resource": "arn:aws:iam::*:role/LaunchpadDeploymentRole"
    }
  ]
}
```

This policy allows the user to assume **any** `LaunchpadDeploymentRole` in **any** AWS account.

### Attach Policy to User

```bash
# Create policy
aws iam create-policy \
  --policy-name AllowAssumeCustomerRoles \
  --policy-document file://assume-role-policy.json

# Get your account ID
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

# Attach policy to user
aws iam attach-user-policy \
  --user-name aklamaash-terraform \
  --policy-arn arn:aws:iam::${ACCOUNT_ID}:policy/AllowAssumeCustomerRoles
```

---

## Complete Setup Script

Save as `setup-platform-user.sh`:

```bash
#!/bin/bash
set -e

USER_NAME="aklamaash-terraform"
POLICY_NAME="AllowAssumeCustomerRoles"

echo "=========================================="
echo "Launchpad Platform User Setup"
echo "=========================================="

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo "Platform Account ID: $ACCOUNT_ID"

# Create assume role policy
cat > assume-role-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AssumeCustomerDeploymentRoles",
      "Effect": "Allow",
      "Action": "sts:AssumeRole",
      "Resource": "arn:aws:iam::*:role/LaunchpadDeploymentRole"
    }
  ]
}
EOF

# Create user if doesn't exist
if aws iam get-user --user-name ${USER_NAME} >/dev/null 2>&1; then
  echo "✓ User ${USER_NAME} already exists"
else
  echo "Creating IAM user..."
  aws iam create-user --user-name ${USER_NAME}
  echo "✓ Created user ${USER_NAME}"
fi

# Create policy if doesn't exist
POLICY_ARN="arn:aws:iam::${ACCOUNT_ID}:policy/${POLICY_NAME}"
if aws iam get-policy --policy-arn ${POLICY_ARN} >/dev/null 2>&1; then
  echo "✓ Policy ${POLICY_NAME} already exists"
else
  echo "Creating policy..."
  aws iam create-policy \
    --policy-name ${POLICY_NAME} \
    --policy-document file://assume-role-policy.json
  echo "✓ Created policy ${POLICY_NAME}"
fi

# Attach policy to user
echo "Attaching policy to user..."
aws iam attach-user-policy \
  --user-name ${USER_NAME} \
  --policy-arn ${POLICY_ARN} \
  2>/dev/null || echo "✓ Policy already attached"

# Check if access key exists
KEYS=$(aws iam list-access-keys --user-name ${USER_NAME} --query 'AccessKeyMetadata[].AccessKeyId' --output text)

if [ -z "$KEYS" ]; then
  echo ""
  echo "Creating access key..."
  aws iam create-access-key --user-name ${USER_NAME} --output json > access-key.json
  
  echo ""
  echo "=========================================="
  echo "⚠️  SAVE THESE CREDENTIALS SECURELY"
  echo "=========================================="
  cat access-key.json | jq -r '"Access Key ID: " + .AccessKey.AccessKeyId'
  cat access-key.json | jq -r '"Secret Access Key: " + .AccessKey.SecretAccessKey'
  echo ""
  echo "Add to .env files:"
  echo "AWS_ACCESS_KEY_ID=$(cat access-key.json | jq -r .AccessKey.AccessKeyId)"
  echo "AWS_SECRET_ACCESS_KEY=$(cat access-key.json | jq -r .AccessKey.SecretAccessKey)"
  echo ""
  
  rm access-key.json
else
  echo "✓ Access key already exists: $KEYS"
  echo "⚠️  If you need new credentials, delete the old key first:"
  echo "   aws iam delete-access-key --user-name ${USER_NAME} --access-key-id $KEYS"
fi

# Cleanup
rm -f assume-role-policy.json

echo ""
echo "=========================================="
echo "✅ Platform User Setup Complete"
echo "=========================================="
echo ""
echo "User ARN: arn:aws:iam::${ACCOUNT_ID}:user/${USER_NAME}"
echo ""
echo "This user can now assume LaunchpadDeploymentRole in customer accounts"
echo ""
```

---

## Environment Configuration

### Infrastructure Service (.env)

```bash
# Platform AWS credentials
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=us-west-2

# Other config...
```

### Application Service (.env)

```bash
# Platform AWS credentials
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=us-west-2

# Other config...
```

---

## How It Works

### 1. Customer Setup

Customer runs script in their account:
```bash
./create_aws_role.sh
```

This creates:
- Role: `LaunchpadDeploymentRole`
- Trust policy: Allows `arn:aws:iam::221082203366:user/aklamaash-terraform` to assume role

### 2. Platform Assumes Role

When deploying infrastructure or applications:

```python
# In platform code
sts_client = boto3.client(
    "sts",
    aws_access_key_id=platform_access_key,
    aws_secret_access_key=platform_secret_key,
)

response = sts_client.assume_role(
    RoleArn=f"arn:aws:iam::{customer_account_id}:role/LaunchpadDeploymentRole",
    RoleSessionName="deployment-session"
)

# Use temporary credentials
customer_session = boto3.Session(
    aws_access_key_id=response['Credentials']['AccessKeyId'],
    aws_secret_access_key=response['Credentials']['SecretAccessKey'],
    aws_session_token=response['Credentials']['SessionToken']
)
```

### 3. Operations in Customer Account

All AWS operations use the assumed role credentials:
- Terraform provisions infrastructure
- CodeBuild builds Docker images
- ECS deploys applications
- ALB routes traffic

---

## Security Best Practices

### 1. Credential Management

- ✅ Store credentials in environment variables (not code)
- ✅ Use AWS Secrets Manager for production
- ✅ Rotate credentials every 90 days
- ✅ Never commit credentials to Git

### 2. Access Control

- ✅ User can only assume `LaunchpadDeploymentRole` (not other roles)
- ✅ User has no direct permissions in platform account
- ✅ All operations happen via AssumeRole
- ✅ Temporary credentials expire after 1 hour

### 3. Monitoring

- ✅ Enable CloudTrail in platform account
- ✅ Monitor AssumeRole API calls
- ✅ Alert on failed assume role attempts
- ✅ Log all customer account operations

### 4. Credential Rotation

```bash
# Create new access key
aws iam create-access-key --user-name aklamaash-terraform

# Update .env files with new credentials

# Test new credentials work

# Delete old access key
aws iam delete-access-key \
  --user-name aklamaash-terraform \
  --access-key-id AKIA_OLD_KEY
```

---

## Troubleshooting

### AssumeRole Access Denied

**Error**: `User: arn:aws:iam::221082203366:user/aklamaash-terraform is not authorized to perform: sts:AssumeRole`

**Causes**:
1. Policy not attached to user
2. Customer role doesn't trust the user
3. Customer role doesn't exist

**Fix**:
```bash
# Check user policies
aws iam list-attached-user-policies --user-name aklamaash-terraform

# Check customer role trust policy
aws iam get-role --role-name LaunchpadDeploymentRole \
  --profile customer-account
```

### Invalid Credentials

**Error**: `The security token included in the request is invalid`

**Causes**:
1. Access key deleted or rotated
2. Credentials not set in environment
3. Wrong credentials in .env file

**Fix**:
```bash
# Verify credentials work
aws sts get-caller-identity

# Should return:
# {
#   "UserId": "AIDA...",
#   "Account": "221082203366",
#   "Arn": "arn:aws:iam::221082203366:user/aklamaash-terraform"
# }
```

---

## Testing

### Test AssumeRole

```bash
# Set platform credentials
export AWS_ACCESS_KEY_ID=AKIA...
export AWS_SECRET_ACCESS_KEY=...

# Test assume role (replace with customer account ID)
aws sts assume-role \
  --role-arn arn:aws:iam::123456789012:role/LaunchpadDeploymentRole \
  --role-session-name test-session

# Should return temporary credentials
```

### Test Customer Operations

```bash
# Use temporary credentials from above
export AWS_ACCESS_KEY_ID=ASIA...
export AWS_SECRET_ACCESS_KEY=...
export AWS_SESSION_TOKEN=...

# Test VPC creation
aws ec2 describe-vpcs

# Test ECS access
aws ecs list-clusters
```

---

## Maintenance

### Regular Tasks

**Monthly**:
- Review CloudTrail logs for unusual activity
- Check for failed AssumeRole attempts
- Verify credentials are working

**Quarterly**:
- Rotate access keys
- Review and update policies
- Audit customer role trust policies

**Annually**:
- Security audit of entire setup
- Review and update documentation
- Test disaster recovery procedures

---

## Disaster Recovery

### Lost Credentials

1. Create new access key:
   ```bash
   aws iam create-access-key --user-name aklamaash-terraform
   ```

2. Update all services with new credentials

3. Delete old access key

### User Deleted

1. Recreate user:
   ```bash
   ./setup-platform-user.sh
   ```

2. Update all services with new credentials

3. Notify customers (no action needed on their end)

### Policy Deleted

1. Recreate policy:
   ```bash
   ./setup-platform-user.sh
   ```

2. Test AssumeRole works

---

## Summary

**Platform Account**: `221082203366`  
**IAM User**: `aklamaash-terraform`  
**Permission**: Can assume `LaunchpadDeploymentRole` in any customer account  
**Credentials**: Stored in `.env` files (infrastructure-service, application-service)

**Customer Setup**: Run `create_aws_role.sh` to create role with trust policy  
**Platform Operations**: Assume role → Get temporary credentials → Operate in customer account

---

**Version**: 1.0.0  
**Last Updated**: 2026-03-08  
**Owner**: Platform Team
