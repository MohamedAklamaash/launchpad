#!/bin/bash
set -e

# NOTE: This script refreshes BOTH the LaunchpadDeploymentPolicy (permissions)
# AND the LaunchpadDeploymentRole trust policy in place. Run it whenever
# Launchpad widens its IAM surface (the codebuild:* regression that broke
# create_project for accounts onboarded before the fix is the canonical
# example) OR when the trust-policy ExternalId / platform principal changes.

ROLE_NAME="LaunchpadDeploymentRole"
POLICY_NAME="LaunchpadDeploymentPolicy"

# Defaults match the current Launchpad platform; override via env if platform creds rotate
# so customers don't need a fresh script each rotation. Kept in sync with create_aws_role.sh.
TRUSTED_ACCOUNT_ID="${LAUNCHPAD_PLATFORM_ACCOUNT_ID:-221082203366}"
PLATFORM_USER="${LAUNCHPAD_PLATFORM_USER:-aklamaash-terraform}"
# Default ExternalId to the infra UUID — backend uses infra.id as ExternalId on AssumeRole.
ASSUME_EXTERNAL_ID="${LAUNCHPAD_EXTERNAL_ID:-${LAUNCHPAD_INFRA_ID:-}}"

echo "Updating LaunchpadDeploymentPolicy..."

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
POLICY_ARN="arn:aws:iam::${ACCOUNT_ID}:policy/${POLICY_NAME}"

# Use a temp dir + trap so the policy JSON doesn't pollute PWD if the AWS calls fail.
WORK_DIR=$(mktemp -d)
trap 'rm -rf "$WORK_DIR"' EXIT

# NOTE: Action list must stay in sync with the other script (create_aws_role.sh / update_aws_role.sh).
# LaunchpadDeploymentPolicy first statement. CI guard: app_scripts/_check_policy_sync.py
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

# Pre-flight: if LaunchpadDeploymentPolicy doesn't exist, the subsequent
# get-policy / create-policy-version calls would surface a raw NoSuchEntity
# under `set -e`. Same shape as the role pre-flight below.
if ! aws iam get-policy --policy-arn "${POLICY_ARN}" >/dev/null 2>&1; then
  echo "ERROR: Policy ${POLICY_NAME} (arn ${POLICY_ARN}) does not exist." >&2
  echo "       Run create_aws_role.sh first." >&2
  exit 1
fi

# Get current default version
DEFAULT_VERSION=$(aws iam get-policy --policy-arn ${POLICY_ARN} --query 'Policy.DefaultVersionId' --output text)

# Create new version
echo "Creating new policy version..."
aws iam create-policy-version \
  --policy-arn ${POLICY_ARN} \
  --policy-document file://"$WORK_DIR/launchpad-policy.json" \
  --set-as-default

# Delete old version
echo "Deleting old policy version ${DEFAULT_VERSION}..."
aws iam delete-policy-version \
  --policy-arn ${POLICY_ARN} \
  --version-id ${DEFAULT_VERSION}

########################################
# TRUST POLICY REFRESH (IDEMPOTENT)
########################################

# Same ExternalId contract as create_aws_role.sh — refuse to write a trust
# policy without ExternalId unless the caller explicitly opts out. We refresh
# the trust policy on every run so a customer who originally onboarded with a
# stale platform principal / ExternalId can recover without recreating the
# role.
echo "Refreshing trust policy..."

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
elif [ "${LAUNCHPAD_ALLOW_NO_EXTERNAL_ID:-0}" = "1" ]; then
  echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
  echo "!! WARNING: LAUNCHPAD_ALLOW_NO_EXTERNAL_ID=1 is set.            !!"
  echo "!! Trust policy will NOT enforce sts:ExternalId.                !!"
  echo "!! This weakens cross-account assume-role protection — do this  !!"
  echo "!! ONLY for advanced/manual setups that bind ExternalId         !!"
  echo "!! elsewhere. NOT RECOMMENDED for the standard onboarding flow. !!"
  echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
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
else
  echo "ERROR: ExternalId is required to refresh the trust policy. Set" >&2
  echo "       LAUNCHPAD_INFRA_ID (preferred) or LAUNCHPAD_EXTERNAL_ID." >&2
  echo "       For advanced / manual setup that wires ExternalId out-of-band," >&2
  echo "       set LAUNCHPAD_ALLOW_NO_EXTERNAL_ID=1 (must be literally \"1\";" >&2
  echo "       values like \"true\" / \"yes\" are NOT accepted; NOT RECOMMENDED)." >&2
  exit 1
fi

# Pre-flight: if the role doesn't exist, aws iam update-assume-role-policy
# surfaces a raw NoSuchEntity that confuses operators. Same idiom as
# create_aws_role.sh's get-role guard.
if ! aws iam get-role --role-name "${ROLE_NAME}" >/dev/null 2>&1; then
  echo "ERROR: Role ${ROLE_NAME} does not exist. Run create_aws_role.sh first." >&2
  exit 1
fi

# update-assume-role-policy is idempotent — it replaces the trust policy in place.
if aws iam update-assume-role-policy \
  --role-name "${ROLE_NAME}" \
  --policy-document file://"$WORK_DIR/trust-policy.json"; then
  echo "Trust policy refreshed."
else
  echo "ERROR: Failed to refresh trust policy on ${ROLE_NAME}." >&2
  exit 1
fi

echo ""
echo "Policy updated successfully!"
echo "Policy ARN: ${POLICY_ARN}"
