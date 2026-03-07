#!/bin/bash
# Cleanup orphaned KMS keys from failed infrastructure provisions

KEY_ID="$1"
REGION="${2:-us-west-2}"

if [ -z "$KEY_ID" ]; then
  echo "Usage: $0 <key-id> [region]"
  echo "Example: $0 1fb5e7e1-52ca-4bfc-835c-cfa6ed89d699 us-west-2"
  exit 1
fi

echo "Assuming LaunchpadDeploymentRole..."
CREDS=$(aws sts assume-role \
  --role-arn arn:aws:iam::221082203366:role/LaunchpadDeploymentRole \
  --role-session-name cleanup-session \
  --query 'Credentials.[AccessKeyId,SecretAccessKey,SessionToken]' \
  --output text)

export AWS_ACCESS_KEY_ID=$(echo $CREDS | awk '{print $1}')
export AWS_SECRET_ACCESS_KEY=$(echo $CREDS | awk '{print $2}')
export AWS_SESSION_TOKEN=$(echo $CREDS | awk '{print $3}')

echo "Scheduling KMS key $KEY_ID for deletion..."
aws kms schedule-key-deletion \
  --key-id "$KEY_ID" \
  --pending-window-in-days 7 \
  --region "$REGION"

echo "Done. Key will be deleted in 7 days."
