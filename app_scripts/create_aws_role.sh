#!/bin/bash
set -e

ROLE_NAME="LaunchpadDeploymentRole"
POLICY_NAME="LaunchpadDeploymentPolicy"
ASSUME_POLICY_NAME="AllowAssumeLaunchpadDeploymentRole"

TRUSTED_ACCOUNT_ID="221082203366"
PLATFORM_USER="aklamaash-terraform"

echo "=========================================="
echo "Launchpad AWS Role Setup"
echo "=========================================="

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

########################################
# TRUST POLICY
########################################

cat > trust-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::${TRUSTED_ACCOUNT_ID}:user/${PLATFORM_USER}"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF

########################################
# CREATE ROLE (IDEMPOTENT)
########################################

echo "Checking if role exists..."

if aws iam get-role --role-name ${ROLE_NAME} >/dev/null 2>&1; then
  echo "Role already exists."
else
  echo "Creating IAM role..."
  aws iam create-role \
    --role-name ${ROLE_NAME} \
    --assume-role-policy-document file://trust-policy.json
fi

########################################
# POLICY FOR TERRAFORM INFRA
########################################

cat > launchpad-policy.json <<EOF
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
        "dynamodb:*"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "iam:CreateRole",
        "iam:DeleteRole",
        "iam:GetRole",
        "iam:PassRole",
        "iam:AttachRolePolicy",
        "iam:DetachRolePolicy",
        "iam:PutRolePolicy",
        "iam:DeleteRolePolicy",
        "iam:GetRolePolicy",
        "iam:ListRolePolicies",
        "iam:ListAttachedRolePolicies",
        "iam:TagRole",
        "iam:UntagRole"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "kms:CreateKey",
        "kms:CreateAlias",
        "kms:DeleteAlias",
        "kms:DescribeKey",
        "kms:EnableKeyRotation",
        "kms:PutKeyPolicy",
        "kms:ScheduleKeyDeletion",
        "kms:TagResource",
        "kms:UntagResource"
      ],
      "Resource": "*"
    }
  ]
}
EOF

########################################
# CREATE POLICY (IDEMPOTENT)
########################################

POLICY_ARN="arn:aws:iam::${ACCOUNT_ID}:policy/${POLICY_NAME}"

if aws iam get-policy --policy-arn ${POLICY_ARN} >/dev/null 2>&1; then
  echo "Policy already exists."
else
  echo "Creating deployment policy..."
  aws iam create-policy \
    --policy-name ${POLICY_NAME} \
    --policy-document file://launchpad-policy.json
fi

########################################
# ATTACH POLICY TO ROLE
########################################

echo "Attaching policy to role..."

aws iam attach-role-policy \
  --role-name ${ROLE_NAME} \
  --policy-arn ${POLICY_ARN} \
  2>/dev/null || true

########################################
# PLATFORM USER ASSUME ROLE POLICY
########################################

cat > allow-assume-role.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "sts:AssumeRole",
      "Resource": "arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"
    }
  ]
}
EOF

ASSUME_POLICY_ARN="arn:aws:iam::${ACCOUNT_ID}:policy/${ASSUME_POLICY_NAME}"

if aws iam get-policy --policy-arn ${ASSUME_POLICY_ARN} >/dev/null 2>&1; then
  echo "Assume policy already exists."
else
  echo "Creating assume-role policy..."
  aws iam create-policy \
    --policy-name ${ASSUME_POLICY_NAME} \
    --policy-document file://allow-assume-role.json
fi

echo "Attaching assume-role policy to ${PLATFORM_USER}..."

aws iam attach-user-policy \
  --user-name ${PLATFORM_USER} \
  --policy-arn ${ASSUME_POLICY_ARN} \
  2>/dev/null || true

########################################
# CLEANUP
########################################

rm -f trust-policy.json
rm -f launchpad-policy.json
rm -f allow-assume-role.json

echo ""
echo "=========================================="
echo "Launchpad Role Setup Complete"
echo "=========================================="
echo ""
echo "Role ARN:"
echo "arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"
echo ""