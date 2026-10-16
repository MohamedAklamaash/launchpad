#!/usr/bin/env bash
# Standing release action (plan/F8-cloudformation-onboarding.md §3): publish both
# generated CFN templates to this bucket, keyed by a full commit SHA, using a
# conditional PUT so a previously published ref can never be overwritten.
#
# Usage:
#   infra/platform-templates/publish.sh <bucket> <git-ref> [templates-prefix]
#
# <bucket>            bucket_name output from `terraform output -raw bucket_name`.
# <git-ref>           any ref git can resolve (tag, branch, short SHA). Resolved to the
#                      full commit SHA before publishing — a branch name is never used
#                      as the key itself, since it would silently point at different
#                      bytes on every future commit to that branch.
# [templates-prefix]  defaults to "templates", must match the bucket's
#                      templates_prefix variable (and therefore the bucket policy's
#                      public-read scope).
#
# Requires an AWS CLI new enough to support `s3api put-object --if-none-match` (v2.x
# with a reasonably current build; confirmed present in aws-cli/2.35.15). Credentials
# need only s3:PutObject under <templates-prefix>/* on this bucket — see README for the
# out-of-band publisher principal this is meant to run as.
set -euo pipefail

if [ "$#" -lt 2 ] || [ "$#" -gt 3 ]; then
  echo "usage: $0 <bucket> <git-ref> [templates-prefix]" >&2
  exit 1
fi

BUCKET="$1"
REF_INPUT="$2"
PREFIX="${3:-templates}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CFN_DIR="$REPO_ROOT/deployment-services/infrastructure-service/api/cloud_providers/aws/iam_policy/cloudformation"
GENERATE_PY="$REPO_ROOT/deployment-services/infrastructure-service/api/cloud_providers/aws/iam_policy/generate.py"

TEMPLATES=(
  "launchpad-deployment-role.ecs-fargate.template.json"
  "launchpad-deployment-role.eks.template.json"
)

echo "Checking generated templates are not drifted from policy.json ..." >&2
python3 "$GENERATE_PY" --check

REF="$(git -C "$REPO_ROOT" rev-parse --verify "${REF_INPUT}^{commit}")"
echo "Resolved $REF_INPUT -> $REF" >&2

FAILED=0
for FILE in "${TEMPLATES[@]}"; do
  SRC="$CFN_DIR/$FILE"
  KEY="$PREFIX/$REF/$FILE"

  if [ ! -f "$SRC" ]; then
    echo "missing generated template: $SRC" >&2
    FAILED=1
    continue
  fi

  echo "Publishing s3://$BUCKET/$KEY ..." >&2
  if aws s3api put-object \
    --bucket "$BUCKET" \
    --key "$KEY" \
    --body "$SRC" \
    --content-type application/json \
    --if-none-match '*' \
    >/dev/null 2>/tmp/publish-put-object.err; then
    echo "  published: https://$BUCKET.s3.amazonaws.com/$KEY" >&2
  else
    if grep -qi "PreconditionFailed\|At least one of the pre-conditions" /tmp/publish-put-object.err; then
      echo "  already published at this ref — refusing to overwrite (this is success, not an error)" >&2
    else
      echo "  FAILED:" >&2
      cat /tmp/publish-put-object.err >&2
      FAILED=1
    fi
  fi
done
rm -f /tmp/publish-put-object.err

if [ "$FAILED" -ne 0 ]; then
  exit 1
fi

echo "" >&2
echo "Published ref: $REF" >&2
echo "Set NEXT_PUBLIC_LAUNCHPAD_CFN_TEMPLATE_REF=$REF" >&2
echo "Set NEXT_PUBLIC_LAUNCHPAD_CFN_TEMPLATE_BASE_URL to the bucket's template_base_url output." >&2
