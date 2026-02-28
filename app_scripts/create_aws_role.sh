#!/bin/bash

ROLE_NAME="DeploymentRole"
TRUSTED_ACCOUNT_ID="123456789012"   # <-- CHANGE THIS

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
"arn:aws:iam::aws:policy/AccountManagementFromVercel"
"arn:aws:iam::aws:policy/AmazonEC2FullAccess"
"arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy"
"arn:aws:iam::aws:policy/AmazonS3FullAccess"
"arn:aws:iam::aws:policy/AmazonVPCFullAccess"
"arn:aws:iam::aws:policy/CloudWatchFullAccess"
"arn:aws:iam::aws:policy/CloudWatchFullAccessV2"
"arn:aws:iam::aws:policy/ElasticLoadBalancingFullAccess"
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

echo "Done."