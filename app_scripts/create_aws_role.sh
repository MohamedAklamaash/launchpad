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
  LAUNCHPAD_PLATFORM_ACCOUNT_ID    Launchpad platform AWS account ID the trust policy must name as
                                   principal. Required unless LAUNCHPAD_MOCK=1. The dashboard's
                                   generated command sets this for you.
  LAUNCHPAD_PLATFORM_USER          Launchpad platform IAM user name. Required unless LAUNCHPAD_MOCK=1.
                                   The dashboard's generated command sets this for you.
  LAUNCHPAD_EXTERNAL_ID            Per-customer ExternalId binding the trust policy (defaults to LAUNCHPAD_INFRA_ID)
  LAUNCHPAD_REGION                 AWS region for deployment (default: us-east-1)
  LAUNCHPAD_COMPUTE_TYPE           Infra compute target: "ecs_fargate" (default) or "eks".
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

MOCK_MODE="${LAUNCHPAD_MOCK:-0}"

# No hardcoded default: a stale platform account/user here builds a trust policy naming
# the wrong AWS principal, which IAM rejects at role-creation time with
# "MalformedPolicyDocument: Invalid principal". A hardcoded value here has gone stale
# before. The dashboard's generated command always sets both from the server's own
# LAUNCHPAD_PLATFORM_PRINCIPAL_ARN, so it never has to guess. Mock mode still needs a
# value to render a trust policy with (nothing real is ever created), so it falls back
# to the same placeholder core/settings.py uses for LAUNCHPAD_PLATFORM_PRINCIPAL_ARN in
# MODE=dev.
if [ "$MOCK_MODE" = "1" ]; then
  TRUSTED_ACCOUNT_ID="${LAUNCHPAD_PLATFORM_ACCOUNT_ID:-000000000000}"
  PLATFORM_USER="${LAUNCHPAD_PLATFORM_USER:-dev-placeholder}"
else
  if [ -z "${LAUNCHPAD_PLATFORM_ACCOUNT_ID:-}" ]; then
    echo "ERROR: LAUNCHPAD_PLATFORM_ACCOUNT_ID is required (the Launchpad platform AWS" >&2
    echo "       account id your trust policy must name as principal). The dashboard's" >&2
    echo "       generated command sets this for you; set LAUNCHPAD_MOCK=1 for local" >&2
    echo "       dev/mock runs instead." >&2
    exit 1
  fi
  if [ -z "${LAUNCHPAD_PLATFORM_USER:-}" ]; then
    echo "ERROR: LAUNCHPAD_PLATFORM_USER is required (the Launchpad platform IAM user" >&2
    echo "       your trust policy must name as principal). The dashboard's generated" >&2
    echo "       command sets this for you; set LAUNCHPAD_MOCK=1 for local dev/mock runs" >&2
    echo "       instead." >&2
    exit 1
  fi
  TRUSTED_ACCOUNT_ID="$LAUNCHPAD_PLATFORM_ACCOUNT_ID"
  PLATFORM_USER="$LAUNCHPAD_PLATFORM_USER"
fi

# Default ExternalId to the infra UUID — backend uses infra.id as ExternalId on AssumeRole, so binding
# the trust policy to it by default removes a manual setup step for customers using the dashboard flow.
ASSUME_EXTERNAL_ID="${LAUNCHPAD_EXTERNAL_ID:-${LAUNCHPAD_INFRA_ID:-}}"

# Selects which IAM statements the generated policy region below applies. Keep the
# default in sync with policy_data.DEFAULT_COMPUTE_TYPE.
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
[ "$MOCK_MODE" = "1" ] && echo "Mode:             MOCK (no AWS calls)"
[ "$COMPUTE_TYPE" = "eks" ] && echo "Compute type:     EKS"
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
PLATFORM_PRINCIPAL_ARN="arn:aws:iam::${TRUSTED_ACCOUNT_ID}:user/${PLATFORM_USER}"

########################################
# EXISTING ROLE STATE
########################################

# Two Launchpad infrastructures can land in the same AWS account (Infrastructure.code
# isn't unique) and share this one role. Overwriting the trust policy with only this
# run's ExternalId would revoke AssumeRole for every other infra already trusted on the
# role, forever. Read what's already there (if anything) before we build the new trust
# policy below, so we can merge instead of replace. Same story for which compute types
# the role already serves (`launchpad:compute-types` tag): a role also serving an EKS
# infra must never be narrowed back to the ecs_fargate policy document just because this
# run happens to be for an ecs_fargate infra.
ROLE_EXISTS=0
EXISTING_EXTERNAL_IDS=""
EXISTING_COMPUTE_TYPES=""
LEGACY_EKS_DETECTED=""

if [ "$MOCK_MODE" != "1" ] && aws iam get-role --role-name "${ROLE_NAME}" >/dev/null 2>&1; then
  ROLE_EXISTS=1

  # --output text flattens whatever shape "sts:ExternalId" is on the wire (a bare
  # string, or a list from a previous multi-infra run) into whitespace-separated
  # tokens. A UUID never contains whitespace, so word-splitting on IFS below recovers
  # the individual ids either way, without jq.
  #
  # Deliberately NOT suppressing errors or falling back to "" here: the role was just
  # confirmed to exist above, so a failure on this read is a real problem (a malformed
  # query, a permissions issue, a throttle) — not evidence of "no existing ExternalIds".
  # Treating it as the latter is exactly the bug this script exists to fix: it would
  # silently proceed to overwrite the trust policy with only this run's id.
  EXTERNAL_ID_QUERY="Role.AssumeRolePolicyDocument.Statement[?Principal.AWS=='${PLATFORM_PRINCIPAL_ARN}' && Effect=='Allow'].Condition.StringEquals.\"sts:ExternalId\""
  EXISTING_EXTERNAL_IDS=$(aws iam get-role --role-name "${ROLE_NAME}" --query "$EXTERNAL_ID_QUERY" --output text)
  [ "$EXISTING_EXTERNAL_IDS" = "None" ] && EXISTING_EXTERNAL_IDS=""

  # A "+" separator, not "," — IAM tag values only allow letters, digits, spaces, and
  # `_ . : / = + - @` (see the tag-write below); a comma-joined value is rejected by
  # TagRole outright.
  COMPUTE_TYPES_TAG_QUERY="Role.Tags[?Key=='launchpad:compute-types'].Value | [0]"
  EXISTING_COMPUTE_TYPES=$(aws iam get-role --role-name "${ROLE_NAME}" --query "$COMPUTE_TYPES_TAG_QUERY" --output text)
  [ "$EXISTING_COMPUTE_TYPES" = "None" ] && EXISTING_COMPUTE_TYPES=""

  # A role created before this tag existed carries no record of which compute types it
  # already serves — trusting only the tag would let this run's ecs_fargate policy
  # silently replace an already-installed EKS document. Detect EKS independently from
  # the role's live state: the EKS statements in policy.json are the only ones that
  # ever grant an "eks:" action, so finding one in the currently attached policy (or
  # finding the 2h max session duration only an EKS run ever sets) is proof enough on
  # its own, with no dependence on the tag.
  EXISTING_MAX_SESSION=$(aws iam get-role --role-name "${ROLE_NAME}" --query 'Role.MaxSessionDuration' --output text)
  if [ "${EXISTING_MAX_SESSION:-0}" -ge 7200 ] 2>/dev/null; then
    LEGACY_EKS_DETECTED="eks"
  fi
  if [ -z "$LEGACY_EKS_DETECTED" ] && aws iam get-policy --policy-arn "${POLICY_ARN}" >/dev/null 2>&1; then
    # Errors past this point are NOT suppressed: the policy was just confirmed to
    # exist, so a failure reading its document is a real problem (permissions,
    # throttling), not evidence the EKS grant is absent. Assuming "not EKS" on a read
    # failure would silently strip an already-granted infra's EKS access exactly the
    # way the comma-tag and single-ExternalId bugs did.
    CURRENT_DEFAULT_VERSION=$(aws iam get-policy --policy-arn "${POLICY_ARN}" --query 'Policy.DefaultVersionId' --output text)
    CURRENT_POLICY_DOCUMENT=$(aws iam get-policy-version --policy-arn "${POLICY_ARN}" \
      --version-id "${CURRENT_DEFAULT_VERSION}" --query 'PolicyVersion.Document' --output json)
    case "$CURRENT_POLICY_DOCUMENT" in
      *'"eks:'*) LEGACY_EKS_DETECTED="eks" ;;
    esac
  fi
fi

# Prints each non-empty argument exactly once, in first-seen order — used below to
# merge ExternalIds and compute-type tags without a jq dependency.
_dedupe_tokens() {
  local seen=" " token
  for token in "$@"; do
    [ -z "$token" ] && continue
    case "$seen" in
      *" $token "*) continue ;;
    esac
    seen="$seen$token "
    printf '%s\n' "$token"
  done
}

# Word-splitting is intentional here: _dedupe_tokens wants each existing compute type
# as its own positional argument, and none of them ever contain whitespace.
# shellcheck disable=SC2046,SC2086
MERGED_COMPUTE_TYPES=$(_dedupe_tokens $(printf '%s' "$EXISTING_COMPUTE_TYPES" | tr '+' ' ') "$LEGACY_EKS_DETECTED" "$COMPUTE_TYPE")
# Never downgrade: if any infra this role already serves (or this run requests) is EKS,
# install the EKS policy document (a strict superset) and keep the 2h max session — see
# the APPLY IAM section below. A role that only ever serves ecs_fargate infras is
# unaffected.
EFFECTIVE_COMPUTE_TYPE="$COMPUTE_TYPE"
for _served in $MERGED_COMPUTE_TYPES; do
  [ "$_served" = "eks" ] && EFFECTIVE_COMPUTE_TYPE="eks"
done
unset _served
if [ "$EFFECTIVE_COMPUTE_TYPE" != "$COMPUTE_TYPE" ]; then
  echo "Role already serves EKS elsewhere; installing the EKS policy document (superset) instead of ${COMPUTE_TYPE}."
fi
COMPUTE_TYPE="$EFFECTIVE_COMPUTE_TYPE"
# shellcheck disable=SC2086
MERGED_COMPUTE_TYPES_TAG_VALUE=$(printf '%s+' $MERGED_COMPUTE_TYPES)
MERGED_COMPUTE_TYPES_TAG_VALUE="${MERGED_COMPUTE_TYPES_TAG_VALUE%+}"

########################################
# POLICY DOCUMENTS
########################################

# ExternalId is mandatory by default — without it, anyone in the Launchpad
# platform account who can call sts:AssumeRole could assume this role against
# any customer who reused the same role name. The escape hatch
# (LAUNCHPAD_ALLOW_NO_EXTERNAL_ID=1) exists only for advanced/manual setups
# that wire their own ExternalId out-of-band.
if [ -n "$ASSUME_EXTERNAL_ID" ]; then
  # shellcheck disable=SC2086
  MERGED_EXTERNAL_IDS=$(_dedupe_tokens $EXISTING_EXTERNAL_IDS "$ASSUME_EXTERNAL_ID")
  # Always rendered as a list ("any-of"), even for a single id: a customer with only
  # one infra sees the same shape a second infra would grow into, so a future refresh
  # never has to switch representations.
  # shellcheck disable=SC2086
  EXTERNAL_ID_JSON_LIST=$(printf '"%s",' $MERGED_EXTERNAL_IDS)
  EXTERNAL_ID_JSON_LIST="[${EXTERNAL_ID_JSON_LIST%,}]"
  cat > "$WORK_DIR/trust-policy.json" <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "AWS": "${PLATFORM_PRINCIPAL_ARN}"
      },
      "Action": "sts:AssumeRole",
      "Condition": {
        "StringEquals": { "sts:ExternalId": ${EXTERNAL_ID_JSON_LIST} }
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

# Everything between the markers below is generated — edit the source file, then run
#   python deployment-services/infrastructure-service/api/cloud_providers/aws/iam_policy/generate.py --write
# A hand-edit here fails CI, because a policy that disagrees with the one documented to
# the customer's security reviewer is worse than either copy alone.
# BEGIN GENERATED: deployment policy — source: deployment-services/infrastructure-service/api/cloud_providers/aws/iam_policy/policy.json
# Permissions granted to Launchpad in YOUR account:
# - ec2/ecs/elb/ecr/logs/codebuild: deploy and manage container infrastructure
# - s3: terraform state bucket + application asset storage
# - dynamodb: terraform state lock table
# - rds/elasticache/secretsmanager: create and manage managed databases you provision
#   and the credentials Launchpad injects into your containers
# - iam:*: create execution roles for ECS tasks. This grant is account-wide — the
#   launchpad-* role naming is a convention, not an enforced boundary
# - kms:*: encrypt state bucket and secrets
# - ce:GetCostAndUsage/ce:ListCostAllocationTags/ce:UpdateCostAllocationTagsStatus:
#   read your Cost Explorer data grouped by the launchpad:app/launchpad:infra tags, and
#   activate those tags for cost allocation. Cost Explorer actions carry no
#   resource-level permissions in AWS's IAM model, so this is account-wide financial
#   visibility covering spend unrelated to Launchpad, not scoped to Launchpad-created
#   resources — and organization-wide if the account you run this in is an AWS
#   Organizations management (payer) account, since Cost Explorer there also covers
#   every linked member account. UpdateCostAllocationTagsStatus fails harmlessly in an
#   AWS Organizations member account — only the payer account can activate cost
#   allocation tags there — and Launchpad reports infra-level-only attribution in that case
# - eks (only granted when LAUNCHPAD_COMPUTE_TYPE=eks): create and manage the
#   customer's EKS cluster, access entries, addons, and node groups named infra-*
# - eks Deny (only granted when LAUNCHPAD_COMPUTE_TYPE=eks): blocks EKS access-entry
#   and access-policy management, and DescribeCluster, outside resources named
#   infra-*, as a defense-in-depth backstop; the iam:* grant above means this is
#   not a hard containment boundary
# - acm:RequestCertificate (v4, TLS activation): only when the request is tagged
#   aws:RequestTag/ManagedBy=launchpad, so a certificate this account did not ask
#   Launchpad to manage can never be requested under this grant
# - acm:DescribeCertificate/ListCertificates/ListTagsForCertificate: read certificate
#   state and validation records to poll issuance and to find an existing tagged
#   certificate for reuse
# - acm:AddTagsToCertificate: only when the call's own tags are exactly
#   ManagedBy=launchpad (aws:RequestTag + aws:TagKeys), so this grant can never be used
#   to attach an unrelated tag value or an extra tag key. It CANNOT be restricted to
#   only certificates Launchpad itself created — aws:RequestTag governs the tags being
#   set, not which existing resource is targeted, and ACM has no aws:ResourceTag-style
#   condition usable here without already having the tag. In practice this is
#   defense-in-depth, not a hard boundary: the iam:* grant above already lets this role
#   do far more than tag a certificate, so a compromised role could reach the same
#   outcome other ways regardless of this condition
# - acm:DeleteCertificate: only when aws:ResourceTag/ManagedBy=launchpad — this account
#   cannot delete a certificate it did not let Launchpad tag as its own. No new
#   elasticloadbalancing actions were needed: elasticloadbalancing:* above already
#   covers the 443 listener and per-app host-header rules
# - cloudwatch:GetMetricData (v5, per-app metrics): read CPU/memory/request/latency
#   metrics for dashboards. Cloudwatch metric reads carry no resource-level permissions
#   in AWS's IAM model, so this is account-wide read access to every CloudWatch metric,
#   not scoped to Launchpad-created resources. No new elasticloadbalancing actions were
#   needed: elasticloadbalancing:* above already covers DescribeTargetGroups/DescribeTags
# - tag:GetResources (v6, Nuke infrastructure verification): list every resource carrying
#   the launchpad:infra tag so a nuke run can confirm nothing Launchpad-tagged is left in
#   your account. Read-only and carries no resource-level permissions in AWS's IAM model.
#   Every delete action a nuke run needs (codebuild:DeleteProject, iam:DeleteRole,
#   logs:DeleteLogGroup, ec2:DeleteSecurityGroup, rds:DeleteDBSnapshot, s3 bucket
#   empty+delete, dynamodb:DeleteTable, ecr:DeleteRepository,
#   ecs:DeleteTaskDefinitions, ...) is already covered by the service-wide grants above —
#   this is the only action nuke needed that wasn't
# Review before running. To narrow scope, edit launchpad-policy.json before this script runs.
POLICY_VERSION=6
case "$COMPUTE_TYPE" in
  ecs_fargate)
    cat > "$WORK_DIR/launchpad-policy.json" <<'EOF'
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
    },
    {
      "Effect": "Allow",
      "Action": [
        "ce:GetCostAndUsage",
        "ce:UpdateCostAllocationTagsStatus",
        "ce:ListCostAllocationTags"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": "acm:RequestCertificate",
      "Resource": "*",
      "Condition": {
        "StringEquals": {
          "aws:RequestTag/ManagedBy": "launchpad"
        }
      }
    },
    {
      "Effect": "Allow",
      "Action": [
        "acm:DescribeCertificate",
        "acm:ListCertificates",
        "acm:ListTagsForCertificate"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": "acm:AddTagsToCertificate",
      "Resource": "*",
      "Condition": {
        "StringEquals": {
          "aws:RequestTag/ManagedBy": "launchpad"
        },
        "ForAllValues:StringEquals": {
          "aws:TagKeys": [
            "ManagedBy"
          ]
        }
      }
    },
    {
      "Effect": "Allow",
      "Action": "acm:DeleteCertificate",
      "Resource": "*",
      "Condition": {
        "StringEquals": {
          "aws:ResourceTag/ManagedBy": "launchpad"
        }
      }
    },
    {
      "Effect": "Allow",
      "Action": "cloudwatch:GetMetricData",
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": "tag:GetResources",
      "Resource": "*"
    }
  ]
}
EOF
    ;;
  eks)
    cat > "$WORK_DIR/launchpad-policy.eks.json" <<'EOF'
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
    },
    {
      "Effect": "Allow",
      "Action": [
        "ce:GetCostAndUsage",
        "ce:UpdateCostAllocationTagsStatus",
        "ce:ListCostAllocationTags"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": "acm:RequestCertificate",
      "Resource": "*",
      "Condition": {
        "StringEquals": {
          "aws:RequestTag/ManagedBy": "launchpad"
        }
      }
    },
    {
      "Effect": "Allow",
      "Action": [
        "acm:DescribeCertificate",
        "acm:ListCertificates",
        "acm:ListTagsForCertificate"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": "acm:AddTagsToCertificate",
      "Resource": "*",
      "Condition": {
        "StringEquals": {
          "aws:RequestTag/ManagedBy": "launchpad"
        },
        "ForAllValues:StringEquals": {
          "aws:TagKeys": [
            "ManagedBy"
          ]
        }
      }
    },
    {
      "Effect": "Allow",
      "Action": "acm:DeleteCertificate",
      "Resource": "*",
      "Condition": {
        "StringEquals": {
          "aws:ResourceTag/ManagedBy": "launchpad"
        }
      }
    },
    {
      "Effect": "Allow",
      "Action": "cloudwatch:GetMetricData",
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": "tag:GetResources",
      "Resource": "*"
    },
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
        "arn:aws:eks:*:__LAUNCHPAD_ACCOUNT_ID__:cluster/infra-*",
        "arn:aws:eks:*:__LAUNCHPAD_ACCOUNT_ID__:access-entry/infra-*/*",
        "arn:aws:eks:*:__LAUNCHPAD_ACCOUNT_ID__:addon/infra-*/*",
        "arn:aws:eks:*:__LAUNCHPAD_ACCOUNT_ID__:nodegroup/infra-*/*"
      ]
    },
    {
      "Effect": "Deny",
      "Action": [
        "eks:*AccessEntr*",
        "eks:*AccessPolic*",
        "eks:DescribeCluster"
      ],
      "NotResource": [
        "arn:aws:eks:*:__LAUNCHPAD_ACCOUNT_ID__:cluster/infra-*",
        "arn:aws:eks:*:__LAUNCHPAD_ACCOUNT_ID__:access-entry/infra-*/*"
      ]
    }
  ]
}
EOF
    # __LAUNCHPAD_ACCOUNT_ID__ is a literal placeholder substituted here, not a
    # shell variable; the heredoc above stays quoted so an IAM action can never be
    # read as a shell expansion.
    sed "s/__LAUNCHPAD_ACCOUNT_ID__/${ACCOUNT_ID}/g" "$WORK_DIR/launchpad-policy.eks.json" > "$WORK_DIR/launchpad-policy.json"
    ;;
  *)
    echo "ERROR: unknown LAUNCHPAD_COMPUTE_TYPE '${COMPUTE_TYPE}' (expected \"ecs_fargate\" or \"eks\")." >&2
    exit 1
    ;;
esac
# END GENERATED

########################################
# APPLY IAM (idempotent; skipped in mock mode)
########################################

if [ "$MOCK_MODE" = "1" ]; then
  echo "Mock mode: skipping IAM role/policy changes."
else
  echo "Ensuring IAM role..."
  if [ "$ROLE_EXISTS" = "1" ]; then
    # A re-run with a new/merged ExternalId list (or a rotated platform principal) must
    # land on the existing role, otherwise the backend's AssumeRole (which always sends
    # ExternalId) fails with AccessDenied for every infra trusted on it.
    echo "Role exists; refreshing trust policy..."
    aws iam update-assume-role-policy \
      --role-name "${ROLE_NAME}" \
      --policy-document file://"$WORK_DIR/trust-policy.json"
    if [ "$COMPUTE_TYPE" = "eks" ]; then
      # An EKS cluster apply can outlive a 1h STS session; ECS-only roles keep the
      # default. Only ever raised, never lowered back down on a later ecs_fargate-only
      # run — see EFFECTIVE_COMPUTE_TYPE above.
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

  # Records which compute types this role serves so a later run for a different infra
  # (possibly a different compute type) can compute the same union again next time.
  # Two IAM constraints, both hard failures if violated:
  #   - --tags must be given as JSON, not the `Key=...,Value=...` shorthand: the
  #     shorthand parser splits on every comma, so a union value would be misread as a
  #     second key/value pair (or rejected outright) instead of one tag value.
  #   - the tag VALUE itself may only contain letters, digits, spaces, and
  #     `_ . : / = + - @` — a comma is rejected by TagRole with ValidationError even
  #     inside a syntactically valid JSON string, which is why the value is "+"-joined
  #     above, not comma-joined.
  echo "Tagging role with served compute types (${MERGED_COMPUTE_TYPES_TAG_VALUE})..."
  aws iam tag-role \
    --role-name "${ROLE_NAME}" \
    --tags "[{\"Key\": \"launchpad:compute-types\", \"Value\": \"${MERGED_COMPUTE_TYPES_TAG_VALUE}\"}]"

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
    -d "{\"infra_id\":\"${LAUNCHPAD_INFRA_ID}\",\"account_id\":\"${ACCOUNT_ID}\",\"onboarding_token\":\"${LAUNCHPAD_ONBOARDING_TOKEN}\",\"policy_version\":${POLICY_VERSION}}" \
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
       -d "{\"infra_id\":\"${LAUNCHPAD_INFRA_ID}\",\"account_id\":\"${ACCOUNT_ID}\",\"caller_arn\":\"${CALLER_ARN}\",\"script\":\"create_aws_role.sh\",\"role_name\":\"${ROLE_NAME}\",\"policy_arn\":\"${POLICY_ARN}\",\"policy_version\":${POLICY_VERSION}}"; then
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
