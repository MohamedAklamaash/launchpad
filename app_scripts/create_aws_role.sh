#!/bin/bash
set -e

########################################
# USAGE
########################################

if [ "$1" = "-h" ] || [ "$1" = "--help" ]; then
  cat <<USAGE
Usage: $0

Creates the LaunchpadDeploymentRole in YOUR AWS account so the Launchpad
platform can deploy infrastructure on your behalf via cross-account assume-role.

Environment variables (all optional, sensible defaults applied):
  LAUNCHPAD_PLATFORM_ACCOUNT_ID  Launchpad platform AWS account ID (default: 221082203366)
  LAUNCHPAD_PLATFORM_USER        Launchpad platform IAM user (default: aklamaash-terraform)
  LAUNCHPAD_EXTERNAL_ID          Per-customer ExternalId binding the trust policy (defaults to LAUNCHPAD_INFRA_ID)
  LAUNCHPAD_REGION               AWS region for deployment (default: us-east-1)
  LAUNCHPAD_INFRA_ID             Infra UUID; required for onboarding callback
  LAUNCHPAD_CALLBACK_URL         Launchpad callback URL; required for onboarding callback
  LAUNCHPAD_ONBOARDING_TOKEN     Single-use onboarding token; required for onboarding callback
USAGE
  exit 0
fi

ROLE_NAME="LaunchpadDeploymentRole"
POLICY_NAME="LaunchpadDeploymentPolicy"

# Defaults match the current Launchpad platform; override via env if platform creds rotate
# so customers don't need a fresh script each rotation.
TRUSTED_ACCOUNT_ID="${LAUNCHPAD_PLATFORM_ACCOUNT_ID:-221082203366}"
PLATFORM_USER="${LAUNCHPAD_PLATFORM_USER:-aklamaash-terraform}"
# Default ExternalId to the infra UUID — backend uses infra.id as ExternalId on AssumeRole, so binding
# the trust policy to it by default removes a manual setup step for customers using the dashboard flow.
ASSUME_EXTERNAL_ID="${LAUNCHPAD_EXTERNAL_ID:-${LAUNCHPAD_INFRA_ID:-}}"

# Region must match where Launchpad provisions; customer's CLI default may differ.
LAUNCHPAD_REGION="${LAUNCHPAD_REGION:-us-east-1}"
export AWS_REGION="$LAUNCHPAD_REGION"

# Use a temp dir + trap so policy JSON files don't pollute PWD on failure / Ctrl-C.
WORK_DIR=$(mktemp -d)
trap 'rm -rf "$WORK_DIR"' EXIT

echo "=========================================="
echo "Launchpad AWS Role Setup"
echo "Region:           ${LAUNCHPAD_REGION}"
echo "Platform account: ${TRUSTED_ACCOUNT_ID}"
echo "Platform user:    ${PLATFORM_USER}"
echo "=========================================="

if [ -z "$ASSUME_EXTERNAL_ID" ]; then
  echo "WARNING: Neither LAUNCHPAD_EXTERNAL_ID nor LAUNCHPAD_INFRA_ID is set."
  echo "         Trust policy will not enforce ExternalId."
  echo "         Set one of these env vars to bind the role to a specific Launchpad customer infrastructure."
fi

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

########################################
# TRUST POLICY
########################################

# Two heredocs (with vs. without ExternalId condition) keeps it readable and avoids
# a jq dependency. Empty ExternalId preserves prior behavior for backward compat.
if [ -n "$ASSUME_EXTERNAL_ID" ]; then
  cat > "$WORK_DIR/trust-policy.json" <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::${TRUSTED_ACCOUNT_ID}:user/${PLATFORM_USER}"
      },
      "Action": "sts:AssumeRole",
      "Condition": {
        "StringEquals": { "sts:ExternalId": "${ASSUME_EXTERNAL_ID}" }
      }
    }
  ]
}
EOF
else
  cat > "$WORK_DIR/trust-policy.json" <<EOF
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
fi

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
    --assume-role-policy-document file://"$WORK_DIR/trust-policy.json"
fi

########################################
# POLICY FOR TERRAFORM INFRA
########################################

# Permissions granted to Launchpad in YOUR account:
# - ec2/ecs/elb/ecr/logs/codebuild: deploy and manage container infrastructure
# - s3: terraform state bucket + application asset storage
# - dynamodb: terraform state lock table
# - iam:*: create execution roles for ECS tasks (scoped to launchpad-* roles in code)
# - kms:*: encrypt state bucket and secrets
# Review before running. To narrow scope, edit launchpad-policy.json before this script runs.
cat > "$WORK_DIR/launchpad-policy.json" <<EOF
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
    --policy-document file://"$WORK_DIR/launchpad-policy.json"
fi

########################################
# ATTACH POLICY TO ROLE
########################################

echo "Attaching policy to role..."

aws iam attach-role-policy \
  --role-name ${ROLE_NAME} \
  --policy-arn ${POLICY_ARN} \
  2>/dev/null || true

echo ""
echo "=========================================="
echo "Launchpad Role Setup Complete"
echo "=========================================="
echo ""
echo "Role ARN:"
echo "arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"
echo ""

########################################
# LAUNCHPAD ONBOARDING CALLBACK (best-effort)
########################################

# All three env vars are injected by the dashboard when it generates this script for a specific
# infra. When run manually (no env vars), we skip the callback rather than failing the script.
if [ -z "${LAUNCHPAD_INFRA_ID}" ] || [ -z "${LAUNCHPAD_CALLBACK_URL}" ] || [ -z "${LAUNCHPAD_ONBOARDING_TOKEN}" ]; then
  echo "LAUNCHPAD_INFRA_ID, LAUNCHPAD_CALLBACK_URL or LAUNCHPAD_ONBOARDING_TOKEN not set; skipping onboarding callback."
  echo "If you ran this manually, trigger onboarding from the Launchpad dashboard."
  exit 0
fi

# Reject plaintext callback URLs — onboarding payload contains the customer's AWS Account ID.
case "$LAUNCHPAD_CALLBACK_URL" in
  https://*) ;;
  http://localhost*|http://127.0.0.1*) ;;
  *)
    echo "ERROR: LAUNCHPAD_CALLBACK_URL must be HTTPS (or localhost for dev)."
    echo "  Got: $LAUNCHPAD_CALLBACK_URL"
    exit 1
    ;;
esac

echo "Notifying Launchpad at ${LAUNCHPAD_CALLBACK_URL}..."

# Drop -f: with -f curl returns non-zero on 4xx AND skips writing the body, so the actual
# status was being masked by the `|| echo "000"` fallback.
RESP_FILE="$WORK_DIR/callback_resp"
CALLBACK_HTTP_CODE=$(curl -sS -o "$RESP_FILE" -w "%{http_code}" \
  -X POST "${LAUNCHPAD_CALLBACK_URL}" \
  -H 'Content-Type: application/json' \
  -d "{\"infra_id\":\"${LAUNCHPAD_INFRA_ID}\",\"account_id\":\"${ACCOUNT_ID}\",\"onboarding_token\":\"${LAUNCHPAD_ONBOARDING_TOKEN}\"}" \
  || echo "000")

echo "Callback HTTP status: ${CALLBACK_HTTP_CODE}"
if [ -f "$RESP_FILE" ]; then
  echo "Callback response:"
  cat "$RESP_FILE"
  echo ""
fi

if [ "${CALLBACK_HTTP_CODE}" != "202" ]; then
  echo "Callback failed; you may need to re-trigger onboarding from the dashboard."
fi

exit 0
