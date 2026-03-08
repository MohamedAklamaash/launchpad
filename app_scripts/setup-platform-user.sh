#!/bin/bash
set -e

USER_NAME="aklamaash-terraform"
POLICY_NAME="AllowAssumeCustomerRoles"

echo "=========================================="
echo "Launchpad Platform User Setup"
echo "=========================================="

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo "Platform Account ID: $ACCOUNT_ID"
echo ""

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
  echo ""
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
