#!/bin/bash

echo "Testing IAM permissions..."

# Test IAM CreateRole
aws iam create-role \
  --role-name TestLaunchpadRole \
  --assume-role-policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Principal": {"Service": "ecs-tasks.amazonaws.com"},
      "Action": "sts:AssumeRole"
    }]
  }' \
  --tags Key=Test,Value=Launchpad \
  2>&1 | head -5

echo ""
echo "Testing KMS permissions..."

# Test KMS CreateKey with tags
aws kms create-key \
  --description "Test Launchpad KMS Key" \
  --tags TagKey=Test,TagValue=Launchpad \
  2>&1 | head -5

echo ""
echo "Cleaning up test resources..."

# Cleanup
aws iam delete-role --role-name TestLaunchpadRole 2>/dev/null || true

echo "Test complete!"
