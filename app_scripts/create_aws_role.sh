#!/bin/bash
set -e

########################################
# USAGE
########################################

if [ "$1" = "-h" ] || [ "$1" = "--help" ]; then
  cat <<USAGE
Usage: $0

Idempotent setup for the LaunchpadDeploymentRole in YOUR AWS account so the
Launchpad platform can deploy on your behalf via cross-account assume-role.

Safe to run repeatedly. On every run it ensures the role, the deployment
policy (latest permissions), and the trust policy are up to date:
  - first run  -> creates the role + policy, then posts the onboarding callback
  - later runs -> refreshes the policy version + trust policy in place; posts the
                  policy-refresh callback when a script API key is provided

Which callback fires is decided by the credentials present:
  - LAUNCHPAD_ONBOARDING_TOKEN set -> onboarding callback (first-time bootstrap)
  - LAUNCHPAD_API_KEY set          -> policy-refresh callback (attributed refresh)

Environment variables (all optional unless noted):
  LAUNCHPAD_PLATFORM_ACCOUNT_ID    Launchpad platform AWS account ID (default: 221082203366)
  LAUNCHPAD_PLATFORM_USER          Launchpad platform IAM user (default: aklamaash-terraform)
  LAUNCHPAD_EXTERNAL_ID            Per-customer ExternalId binding the trust policy (defaults to LAUNCHPAD_INFRA_ID)
  LAUNCHPAD_REGION                 AWS region for deployment (default: us-east-1)
  LAUNCHPAD_COMPUTE_TYPE           Infra compute target: "ecs_fargate" (default) or "eks".
                                   "eks" adds EKS permissions scoped to Launchpad's own
                                   infra-* clusters and raises the role session limit to 2h.
  LAUNCHPAD_INFRA_ID               Infra UUID; required for either callback
  LAUNCHPAD_CALLBACK_URL           Launchpad callback URL; required for either callback
  LAUNCHPAD_ONBOARDING_TOKEN       Single-use onboarding token (first-time bootstrap)
  LAUNCHPAD_API_KEY                Per-user script API key (attributed policy refresh)
  LAUNCHPAD_MOCK                   Set to "1" for dev/mock mode: skips every AWS call and just
                                   posts the callback (zero-cost demo / e2e). Requires
                                   LAUNCHPAD_ACCOUNT_ID.
  LAUNCHPAD_ACCOUNT_ID             AWS Account ID to report in mock mode (must match infra.code).
  LAUNCHPAD_ALLOW_NO_EXTERNAL_ID   Escape hatch: set to literally "1" (NOT "true"/"yes") to skip
                                   the ExternalId requirement. Advanced/manual setups only —
                                   weakens cross-account assume-role protection. NOT RECOMMENDED.
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

MOCK_MODE="${LAUNCHPAD_MOCK:-0}"

# Only the literal "eks" widens the policy; anything else gets the ECS-only document.
COMPUTE_TYPE="${LAUNCHPAD_COMPUTE_TYPE:-ecs_fargate}"

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
[ "$COMPUTE_TYPE" = "eks" ] && echo "Compute target:   EKS (adds scoped eks permissions + 2h role sessions)"
[ "$MOCK_MODE" = "1" ] && echo "Mode:             MOCK (no AWS calls)"
echo "=========================================="

########################################
# ACCOUNT ID
########################################

if [ "$MOCK_MODE" = "1" ]; then
  # Dev/mock: the platform mocks AWS server-side, so the script must not touch
  # real AWS. The account id is injected by the dashboard (the infra's code).
  if [ -z "${LAUNCHPAD_ACCOUNT_ID:-}" ]; then
    echo "ERROR: LAUNCHPAD_MOCK=1 requires LAUNCHPAD_ACCOUNT_ID (the infra's AWS account id)." >&2
    exit 1
  fi
  ACCOUNT_ID="$LAUNCHPAD_ACCOUNT_ID"
  echo "Mock mode: skipping AWS CLI; using account ${ACCOUNT_ID}."
else
  ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
fi

POLICY_ARN="arn:aws:iam::${ACCOUNT_ID}:policy/${POLICY_NAME}"

########################################
# POLICY DOCUMENTS
########################################

# ExternalId is mandatory by default — without it, anyone in the Launchpad
# platform account who can call sts:AssumeRole could assume this role against
# any customer who reused the same role name. The escape hatch
# (LAUNCHPAD_ALLOW_NO_EXTERNAL_ID=1) exists only for advanced/manual setups
# that wire their own ExternalId out-of-band.
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
  echo "ERROR: ExternalId is required. Set LAUNCHPAD_INFRA_ID (preferred — the" >&2
  echo "       dashboard injects it), or LAUNCHPAD_EXTERNAL_ID." >&2
  echo "       For advanced / manual setup that wires ExternalId out-of-band," >&2
  echo "       set LAUNCHPAD_ALLOW_NO_EXTERNAL_ID=1 (must be literally \"1\";" >&2
  echo "       values like \"true\" / \"yes\" are NOT accepted; NOT RECOMMENDED)." >&2
  exit 1
fi

# Permissions granted to Launchpad in YOUR account:
# - ec2/ecs/elb/ecr/logs/codebuild: deploy and manage container infrastructure
# - s3: terraform state bucket + application asset storage
# - dynamodb: terraform state lock table
# - rds/elasticache/secretsmanager: create and manage managed databases you provision
#   and the credentials Launchpad injects into your containers
# - iam:*: create execution roles for ECS tasks (scoped to launchpad-* roles in code)
# - kms:*: encrypt state bucket and secrets
# - eks (EKS infras only): cluster management scoped to Launchpad's infra-* clusters
# Review before running. To narrow scope, edit launchpad-policy.json before this script runs.

if [ "$COMPUTE_TYPE" = "eks" ]; then
  # Create/List/Describe don't accept resource-level scoping; every mutating action is
  # scoped to infra-* clusters. The Deny exists because an unscoped eks:CreateAccessEntry
  # is one API call to cluster-admin on pre-existing clusters in this account.
  EKS_STATEMENTS=$(cat <<EOF
,
    {
      "Effect": "Allow",
      "Action": [
        "eks:CreateCluster",
        "eks:List*",
        "eks:Describe*"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": "eks:*",
      "Resource": [
        "arn:aws:eks:*:${ACCOUNT_ID}:cluster/infra-*",
        "arn:aws:eks:*:${ACCOUNT_ID}:access-entry/infra-*/*",
        "arn:aws:eks:*:${ACCOUNT_ID}:addon/infra-*/*",
        "arn:aws:eks:*:${ACCOUNT_ID}:nodegroup/infra-*/*"
      ]
    },
    {
      "Effect": "Deny",
      "Action": [
        "eks:*AccessEntr*",
        "eks:*AccessPolic*"
      ],
      "NotResource": [
        "arn:aws:eks:*:${ACCOUNT_ID}:cluster/infra-*",
        "arn:aws:eks:*:${ACCOUNT_ID}:access-entry/infra-*/*"
      ]
    }
EOF
)
else
  EKS_STATEMENTS=""
fi

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
        "codebuild:*",
        "rds:*",
        "elasticache:*",
        "secretsmanager:*"
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
    }${EKS_STATEMENTS}
  ]
}
EOF

########################################
# APPLY IAM (idempotent; skipped in mock mode)
########################################

if [ "$MOCK_MODE" = "1" ]; then
  echo "Mock mode: skipping IAM role/policy changes."
else
  echo "Ensuring IAM role..."
  if aws iam get-role --role-name "${ROLE_NAME}" >/dev/null 2>&1; then
    # A re-run with a new ExternalId (or a rotated platform principal) must land on
    # the existing role, otherwise the backend's AssumeRole (which always sends
    # ExternalId) fails with AccessDenied.
    echo "Role exists; refreshing trust policy..."
    aws iam update-assume-role-policy \
      --role-name "${ROLE_NAME}" \
      --policy-document file://"$WORK_DIR/trust-policy.json"
    if [ "$COMPUTE_TYPE" = "eks" ]; then
      # EKS cluster applies can outlive a 1h STS session; ECS-only roles keep the default.
      echo "Raising role max session duration to 2h (EKS)..."
      aws iam update-role \
        --role-name "${ROLE_NAME}" \
        --max-session-duration 7200
    fi
  else
    echo "Creating IAM role..."
    if [ "$COMPUTE_TYPE" = "eks" ]; then
      aws iam create-role \
        --role-name "${ROLE_NAME}" \
        --assume-role-policy-document file://"$WORK_DIR/trust-policy.json" \
        --max-session-duration 7200
    else
      aws iam create-role \
        --role-name "${ROLE_NAME}" \
        --assume-role-policy-document file://"$WORK_DIR/trust-policy.json"
    fi
  fi

  echo "Ensuring deployment policy (latest permissions)..."
  if aws iam get-policy --policy-arn "${POLICY_ARN}" >/dev/null 2>&1; then
    DEFAULT_VERSION=$(aws iam get-policy --policy-arn "${POLICY_ARN}" --query 'Policy.DefaultVersionId' --output text)

    # IAM allows at most 5 versions per managed policy. Prune oldest non-default
    # versions down to 4 before creating so a re-run never fails at the limit.
    VERSION_COUNT=$(aws iam list-policy-versions --policy-arn "${POLICY_ARN}" \
      --query 'length(Versions)' --output text)
    if [ "${VERSION_COUNT}" -ge 5 ]; then
      echo "Policy has ${VERSION_COUNT} versions (IAM max is 5); pruning oldest non-default..."
      for OLD_VERSION in $(aws iam list-policy-versions --policy-arn "${POLICY_ARN}" \
          --query 'Versions[?IsDefaultVersion==`false`].VersionId' --output text); do
        VERSION_COUNT=$(aws iam list-policy-versions --policy-arn "${POLICY_ARN}" \
          --query 'length(Versions)' --output text)
        [ "${VERSION_COUNT}" -lt 5 ] && break
        echo "Deleting policy version ${OLD_VERSION}..."
        aws iam delete-policy-version --policy-arn "${POLICY_ARN}" --version-id "${OLD_VERSION}"
      done
    fi

    echo "Publishing new policy version..."
    aws iam create-policy-version \
      --policy-arn "${POLICY_ARN}" \
      --policy-document file://"$WORK_DIR/launchpad-policy.json" \
      --set-as-default
    echo "Deleting previous policy version ${DEFAULT_VERSION}..."
    aws iam delete-policy-version \
      --policy-arn "${POLICY_ARN}" \
      --version-id "${DEFAULT_VERSION}"
  else
    echo "Creating deployment policy..."
    aws iam create-policy \
      --policy-name "${POLICY_NAME}" \
      --policy-document file://"$WORK_DIR/launchpad-policy.json"
  fi

  echo "Attaching policy to role..."
  aws iam attach-role-policy \
    --role-name "${ROLE_NAME}" \
    --policy-arn "${POLICY_ARN}" \
    2>/dev/null || true

  echo ""
  echo "Role ARN: arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"
fi

echo ""
echo "=========================================="
echo "Launchpad Role Setup Complete"
echo "=========================================="
echo ""

########################################
# CALLBACK
########################################

# The dashboard injects these when it generates the snippet for a specific infra.
# Which callback fires depends on which credential is present.
if [ -z "${LAUNCHPAD_INFRA_ID:-}" ] || [ -z "${LAUNCHPAD_CALLBACK_URL:-}" ]; then
  echo "LAUNCHPAD_INFRA_ID or LAUNCHPAD_CALLBACK_URL not set; skipping callback."
  echo "If you ran this manually, trigger onboarding from the Launchpad dashboard."
  exit 0
fi

# Reject plaintext callback URLs — the payload carries the customer's AWS Account ID.
case "$LAUNCHPAD_CALLBACK_URL" in
  https://*) ;;
  http://localhost*|http://127.0.0.1*) ;;
  *)
    echo "ERROR: LAUNCHPAD_CALLBACK_URL must be HTTPS (or localhost for dev)." >&2
    echo "  Got: $LAUNCHPAD_CALLBACK_URL" >&2
    exit 1
    ;;
esac

RESP_FILE="$WORK_DIR/callback_resp"

if [ -n "${LAUNCHPAD_ONBOARDING_TOKEN:-}" ]; then
  echo "Notifying Launchpad (onboarding) at ${LAUNCHPAD_CALLBACK_URL}..."
  # Drop -f: with -f curl returns non-zero on 4xx AND skips writing the body, so the
  # actual status would be masked by the `|| echo "000"` fallback.
  CALLBACK_HTTP_CODE=$(curl -sS -o "$RESP_FILE" -w "%{http_code}" \
    --connect-timeout 5 --max-time 30 \
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

  # 202 = provisioning enqueued, 200 = already queued (idempotent re-run) — both fine.
  if [ "${CALLBACK_HTTP_CODE}" != "202" ] && [ "${CALLBACK_HTTP_CODE}" != "200" ]; then
    echo "Callback failed; you may need to re-trigger onboarding from the dashboard."
  fi
elif [ -n "${LAUNCHPAD_API_KEY:-}" ]; then
  echo "Reporting policy refresh to Launchpad at ${LAUNCHPAD_CALLBACK_URL}..."
  if [ "$MOCK_MODE" = "1" ]; then
    CALLER_ARN="arn:aws:iam::${ACCOUNT_ID}:user/mock"
  else
    CALLER_ARN=$(aws sts get-caller-identity --query Arn --output text)
  fi
  # Failure here is non-fatal: the IAM refresh already succeeded, and a broken
  # network path shouldn't push the customer to re-run IAM mutations.
  if curl --fail --silent --show-error \
       --connect-timeout 5 --max-time 15 \
       -X POST "$LAUNCHPAD_CALLBACK_URL" \
       -H "Content-Type: application/json" \
       -H "X-API-Key: ${LAUNCHPAD_API_KEY}" \
       -d "{\"infra_id\":\"${LAUNCHPAD_INFRA_ID}\",\"account_id\":\"${ACCOUNT_ID}\",\"caller_arn\":\"${CALLER_ARN}\",\"script\":\"create_aws_role.sh\",\"role_name\":\"${ROLE_NAME}\",\"policy_arn\":\"${POLICY_ARN}\"}"; then
    echo ""
    echo "Refresh recorded with Launchpad."
  else
    echo "WARNING: could not report the refresh to Launchpad (network/auth)." >&2
    echo "         The IAM update itself succeeded. Re-run later or check"   >&2
    echo "         your LAUNCHPAD_API_KEY / LAUNCHPAD_CALLBACK_URL."          >&2
  fi
else
  echo "Neither LAUNCHPAD_ONBOARDING_TOKEN nor LAUNCHPAD_API_KEY set; skipping callback."
  echo "Use the dashboard's Bootstrap snippet (first-time) or Refresh snippet (attributed refresh)."
fi

exit 0
