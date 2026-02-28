#!/bin/bash

ROLE_NAME="DeploymentRole"
TRUSTED_ACCOUNT_ID="221082203366"
PLATFORM_USER="aklamaash-terraform"

echo "Creating trust policy..."

cat > trust-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::${TRUSTED_ACCOUNT_ID}:root"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF

echo "Creating IAM Role..."
aws iam create-role \
  --role-name ${ROLE_NAME} \
  --assume-role-policy-document file://trust-policy.json

echo "Attaching policies..."

POLICIES=(
"arn:aws:iam::aws:policy/AmazonS3FullAccess"
"arn:aws:iam::aws:policy/AmazonVPCFullAccess"
"arn:aws:iam::aws:policy/CloudWatchFullAccess"
"arn:aws:iam::aws:policy/ElasticLoadBalancingFullAccess"
"arn:aws:iam::aws:policy/IAMFullAccess"
"arn:aws:iam::aws:policy/AWSKeyManagementServicePowerUser"
"arn:aws:iam::aws:policy/SecretsManagerReadWrite"
"arn:aws:iam::aws:policy/AWSCloudTrail_FullAccess"
)

for POLICY_ARN in "${POLICIES[@]}"
do
  echo "Attaching $POLICY_ARN"
  aws iam attach-role-policy \
    --role-name ${ROLE_NAME} \
    --policy-arn ${POLICY_ARN}
done

echo "Verifying attached policies..."
aws iam list-attached-role-policies \
  --role-name ${ROLE_NAME}

echo "Adding extra inline permissions for resource management..."
cat > rollout-extra-permissions.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "kms:EnableKeyRotation",
        "kms:TagResource",
        "kms:ScheduleKeyDeletion",
        "kms:DisableKey",
        "kms:PutKeyPolicy",
        "kms:GetKeyPolicy",
        "kms:DescribeKey",
        "kms:CreateGrant",
        "kms:ListGrants",
        "kms:RevokeGrant",
        "compute-optimizer:UpdateEnrollmentStatus",
        "compute-optimizer:GetEnrollmentStatus",
        "ec2:DescribeAddressesAttribute",
        "ec2:DescribeAddresses",
        "iam:PassRole",
        "iam:GetRole",
        "iam:ListAttachedRolePolicies",
        "iam:ListRolePolicies"
      ],
      "Resource": "*"
    }
  ]
}
EOF

aws iam put-role-policy \
  --role-name ${ROLE_NAME} \
  --policy-name LaunchpadExtraPermissions \
  --policy-document file://rollout-extra-permissions.json

rm -f rollout-extra-permissions.json

echo "=========================================="
echo "Creating Platform Caller Policy..."
echo "=========================================="

cat > allow-assume-role.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "sts:AssumeRole",
      "Resource": "arn:aws:iam::${TRUSTED_ACCOUNT_ID}:role/${ROLE_NAME}"
    }
  ]
}
EOF

echo "Creating IAM Policy AllowAssumeDeploymentRole..."
POLICY_ARN=$(aws iam create-policy \
  --policy-name AllowAssumeDeploymentRole \
  --policy-document file://allow-assume-role.json \
  --query 'Policy.Arn' --output text 2>/dev/null)

if [ -z "$POLICY_ARN" ]; then
    echo "Policy AllowAssumeDeploymentRole might already exist. Fetching ARN..."
    POLICY_ARN="arn:aws:iam::${TRUSTED_ACCOUNT_ID}:policy/AllowAssumeDeploymentRole"
fi

echo "Attaching caller policy to user ${PLATFORM_USER}..."
aws iam attach-user-policy \
  --user-name ${PLATFORM_USER} \
  --policy-arn ${POLICY_ARN}

echo "Removing temporary JSON config files..."
rm -f trust-policy.json allow-assume-role.json

echo "Done."