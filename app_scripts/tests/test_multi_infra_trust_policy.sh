#!/bin/bash
# Exercises create_aws_role.sh's trust-policy / compute-type merge logic against a
# stubbed `aws` CLI (fake_aws.sh) instead of a real AWS account.
#
# Covers:
#   1. single ExternalId -> list merge when a second infra onboards onto the same role
#   2. dedupe: re-running for the same infra never duplicates its own id
#   3. compute-type union: an eks run after an ecs_fargate run installs the eks policy
#      document and never downgrades a later ecs_fargate-only run back off it
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
CREATE_ROLE_SH="$REPO_ROOT/app_scripts/create_aws_role.sh"

FAILURES=0

pass() { echo "ok - $1"; }
fail() { echo "FAIL - $1"; FAILURES=$((FAILURES + 1)); }

assert_contains() {
  local haystack="$1" needle="$2" desc="$3"
  case "$haystack" in
    *"$needle"*) pass "$desc" ;;
    *) fail "$desc (expected to find: $needle)"; echo "--- actual ---"; echo "$haystack"; echo "--------------" ;;
  esac
}

assert_not_contains() {
  local haystack="$1" needle="$2" desc="$3"
  case "$haystack" in
    *"$needle"*) fail "$desc (did not expect to find: $needle)"; echo "--- actual ---"; echo "$haystack"; echo "--------------" ;;
    *) pass "$desc" ;;
  esac
}

run_create_role() {
  # Runs create_aws_role.sh against the stubbed aws, with LAUNCHPAD_MOCK unset so it
  # actually exercises the AWS-call path via fake_aws.sh. No LAUNCHPAD_INFRA_ID /
  # LAUNCHPAD_CALLBACK_URL is set, so the script skips the network callback entirely.
  (
    PATH="$TMP_BIN:$PATH"
    FAKE_AWS_STATE="$STATE_DIR"
    LAUNCHPAD_EXTERNAL_ID="$1"
    LAUNCHPAD_COMPUTE_TYPE="$2"
    export PATH FAKE_AWS_STATE LAUNCHPAD_EXTERNAL_ID LAUNCHPAD_COMPUTE_TYPE
    bash "$CREATE_ROLE_SH"
  ) >"$STATE_DIR/last-run.log" 2>&1
}

setup() {
  TMP_ROOT=$(mktemp -d)
  TMP_BIN="$TMP_ROOT/bin"
  STATE_DIR="$TMP_ROOT/state"
  mkdir -p "$TMP_BIN" "$STATE_DIR"
  cp "$SCRIPT_DIR/fake_aws.sh" "$TMP_BIN/aws"
  chmod +x "$TMP_BIN/aws"
}

teardown() {
  rm -rf "$TMP_ROOT"
}

# ── Test 1: single -> list merge, and principal/action stay exactly right ──────────

test_single_to_list_merge() {
  setup
  run_create_role "infra-aaaa" "ecs_fargate"
  run_create_role "infra-bbbb" "ecs_fargate"

  local trust
  trust=$(cat "$STATE_DIR/last-applied-trust-policy.json")

  assert_contains "$trust" '"infra-aaaa"' "second run keeps the first infra's ExternalId"
  assert_contains "$trust" '"infra-bbbb"' "second run adds the new infra's ExternalId"
  assert_contains "$trust" '"sts:AssumeRole"' "action is still exactly sts:AssumeRole"
  assert_contains "$trust" 'aklamaash-terraform' "principal is still the platform user"

  python3 - "$STATE_DIR/last-applied-trust-policy.json" <<'PYEOF'
import json, sys
doc = json.load(open(sys.argv[1]))
ext_id = doc["Statement"][0]["Condition"]["StringEquals"]["sts:ExternalId"]
assert isinstance(ext_id, list), f"expected a list, got {ext_id!r}"
assert sorted(ext_id) == ["infra-aaaa", "infra-bbbb"], ext_id
print("ok - ExternalId condition is a two-element list, no duplicates")
PYEOF

  teardown
}

# ── Test 2: re-running for the same infra does not duplicate its own id ────────────

test_dedupe_same_infra() {
  setup
  run_create_role "infra-aaaa" "ecs_fargate"
  run_create_role "infra-aaaa" "ecs_fargate"

  python3 - "$STATE_DIR/last-applied-trust-policy.json" <<'PYEOF'
import json, sys
doc = json.load(open(sys.argv[1]))
ext_id = doc["Statement"][0]["Condition"]["StringEquals"]["sts:ExternalId"]
ids = ext_id if isinstance(ext_id, list) else [ext_id]
assert ids == ["infra-aaaa"], f"expected no duplicate, got {ids!r}"
print("ok - re-running for the same infra does not duplicate its ExternalId")
PYEOF

  teardown
}

# ── Test 3: compute-type union, and never-downgrade ────────────────────────────────

test_compute_type_union_never_downgrades() {
  setup
  run_create_role "infra-aaaa" "ecs_fargate"
  local policy_after_ecs
  policy_after_ecs=$(cat "$STATE_DIR/last-applied-policy-document.json")
  assert_not_contains "$policy_after_ecs" "eks:CreateCluster" "ecs_fargate-only role does not get EKS actions"

  run_create_role "infra-bbbb" "eks"
  local role_after_eks policy_after_eks
  role_after_eks=$(cat "$STATE_DIR/role.json")
  policy_after_eks=$(cat "$STATE_DIR/last-applied-policy-document.json")
  assert_contains "$policy_after_eks" "eks:CreateCluster" "adding an eks infra installs the eks policy document"
  assert_contains "$role_after_eks" '"MaxSessionDuration": 7200' "adding an eks infra raises max session duration to 2h"
  assert_contains "$role_after_eks" "ecs_fargate+eks" "compute-types tag records the union (+ separated: IAM tag values reject commas)"

  run_create_role "infra-aaaa" "ecs_fargate"
  local role_after_refresh policy_after_refresh
  role_after_refresh=$(cat "$STATE_DIR/role.json")
  policy_after_refresh=$(cat "$STATE_DIR/last-applied-policy-document.json")
  assert_contains "$policy_after_refresh" "eks:CreateCluster" "refreshing the ecs_fargate infra does not downgrade the shared role's policy"
  assert_contains "$role_after_refresh" '"MaxSessionDuration": 7200' "refreshing the ecs_fargate infra does not lower max session duration back down"

  teardown
}

# ── Test 4: a legacy, untagged role that already serves EKS ────────────────────────
# (created by a script version from before the launchpad:compute-types tag existed)
# must not be downgraded just because the tag is missing. The script infers this two
# independent ways (an "eks:" action in the live policy document, OR a 2h max session)
# — each seed below isolates one signal by deliberately leaving the other one absent,
# so a regression that breaks just one detection path still fails a test.

_seed_legacy_role() {
  # $1: MaxSessionDuration to seed. $2: "with_eks_actions" or "ecs_only" for the seeded
  # policy document.
  cat > "$STATE_DIR/role.json" <<JSON
{
  "Role": {
    "AssumeRolePolicyDocument": {
      "Version": "2012-10-17",
      "Statement": [
        {
          "Effect": "Allow",
          "Principal": {"AWS": "arn:aws:iam::221082203366:user/aklamaash-terraform"},
          "Action": "sts:AssumeRole",
          "Condition": {"StringEquals": {"sts:ExternalId": "infra-legacy"}}
        }
      ]
    },
    "MaxSessionDuration": $1,
    "Tags": []
  }
}
JSON
  echo '{"DefaultVersionId": "v1"}' > "$STATE_DIR/policy.json"
  if [ "$2" = "with_eks_actions" ]; then
    cat > "$STATE_DIR/policy-document.json" <<'JSON'
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": ["ec2:*", "ecs:*"], "Resource": "*"},
    {"Effect": "Allow", "Action": ["eks:CreateCluster", "eks:List*", "eks:Describe*"], "Resource": "*"}
  ]
}
JSON
  else
    cat > "$STATE_DIR/policy-document.json" <<'JSON'
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": ["ec2:*", "ecs:*"], "Resource": "*"}
  ]
}
JSON
  fi
}

test_legacy_role_detected_via_policy_document_alone() {
  # MaxSessionDuration is left at the ecs_fargate default (3600): if detection relied
  # on the max-session signal, this would wrongly look like an ecs_fargate-only role.
  # Only the policy document's own "eks:" actions can catch it here.
  setup
  _seed_legacy_role 3600 with_eks_actions

  run_create_role "infra-new" "ecs_fargate"

  local trust role policy
  trust=$(cat "$STATE_DIR/last-applied-trust-policy.json")
  role=$(cat "$STATE_DIR/role.json")
  policy=$(cat "$STATE_DIR/last-applied-policy-document.json")

  assert_contains "$trust" '"infra-legacy"' "legacy infra's ExternalId survives an untagged legacy role's first post-fix run"
  assert_contains "$trust" '"infra-new"' "new infra's ExternalId is added alongside the legacy one"
  assert_contains "$policy" "eks:CreateCluster" "an untagged legacy role's already-installed EKS grant (detected from the policy document) is not stripped by an ecs_fargate-only run"
  assert_contains "$role" '"MaxSessionDuration": 7200' "detecting EKS from the policy document alone still raises max session duration to 2h"
  assert_contains "$role" "eks" "the compute-types tag is backfilled with the inferred eks type"

  teardown
}

test_legacy_role_detected_via_max_session_alone() {
  # The policy document has no "eks:" action at all: if detection relied on the
  # document, this would wrongly look like an ecs_fargate-only role. Only the existing
  # 2h max session duration can catch it here.
  setup
  _seed_legacy_role 7200 ecs_only

  run_create_role "infra-new" "ecs_fargate"

  local policy role
  policy=$(cat "$STATE_DIR/last-applied-policy-document.json")
  role=$(cat "$STATE_DIR/role.json")

  assert_contains "$policy" "eks:CreateCluster" "detecting EKS from a 2h max session alone still installs the eks policy document"
  assert_contains "$role" '"MaxSessionDuration": 7200' "the pre-existing 2h max session is preserved, not lowered back to the ecs_fargate default"
  assert_contains "$role" "eks" "the compute-types tag is backfilled with the inferred eks type"

  teardown
}

test_single_to_list_merge
test_dedupe_same_infra
test_compute_type_union_never_downgrades
test_legacy_role_detected_via_policy_document_alone
test_legacy_role_detected_via_max_session_alone

echo ""
if [ "$FAILURES" -gt 0 ]; then
  echo "$FAILURES assertion(s) failed."
  exit 1
fi
echo "All assertions passed."
