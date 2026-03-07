#!/bin/bash
set -e

POLICY_NAME="LaunchpadDeploymentPolicy"

echo "Updating LaunchpadDeploymentPolicy..."

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
POLICY_ARN="arn:aws:iam::${ACCOUNT_ID}:policy/${POLICY_NAME}"

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

# Get current default version
DEFAULT_VERSION=$(aws iam get-policy --policy-arn ${POLICY_ARN} --query 'Policy.DefaultVersionId' --output text)

# Create new version
echo "Creating new policy version..."
aws iam create-policy-version \
  --policy-arn ${POLICY_ARN} \
  --policy-document file://launchpad-policy.json \
  --set-as-default

# Delete old version
echo "Deleting old policy version ${DEFAULT_VERSION}..."
aws iam delete-policy-version \
  --policy-arn ${POLICY_ARN} \
  --version-id ${DEFAULT_VERSION}

rm -f launchpad-policy.json

echo ""
echo "Policy updated successfully!"
echo "Policy ARN: ${POLICY_ARN}"
