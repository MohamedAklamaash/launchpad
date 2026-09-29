#!/bin/bash
# Stand-in `aws` CLI for test_multi_infra_trust_policy.sh. Not shipped to customers —
# only used to drive create_aws_role.sh in tests without touching real AWS.
#
# State for one simulated account lives under $FAKE_AWS_STATE (set by the caller).
# python3 is used here for JSON handling; that constraint (avoid jq / prefer
# --query+--output text parsing) applies to create_aws_role.sh itself, not to this test
# double.
set -e

STATE="${FAKE_AWS_STATE:?FAKE_AWS_STATE must be set}"
mkdir -p "$STATE"

ROLE_FILE="$STATE/role.json"
POLICY_FILE="$STATE/policy.json"
POLICY_DOC_FILE="$STATE/policy-document.json"
POLICY_VERSIONS_FILE="$STATE/policy-versions.json"

SERVICE="$1"; shift
ACTION="$1"; shift

# Pulls the value following a --flag from the remaining args.
_flag() {
  local flag="$1"; shift
  local prev=""
  for arg in "$@"; do
    if [ "$prev" = "$flag" ]; then
      printf '%s' "$arg"
      return 0
    fi
    prev="$arg"
  done
  return 1
}

_file_arg_path() {
  # --policy-document file:///tmp/x.json -> /tmp/x.json
  # $1 is the flag name; the rest ("${@:2}") is the real arg list to search — passing
  # "$@" here too would duplicate $1 as a fake leading arg and throw off _flag's scan.
  local raw
  raw=$(_flag "$1" "${@:2}") || return 1
  printf '%s' "${raw#file://}"
}

case "$SERVICE.$ACTION" in
  sts.get-caller-identity)
    echo "111111111111"
    ;;

  iam.get-role)
    if [ ! -f "$ROLE_FILE" ]; then
      echo '{"Error":{"Code":"NoSuchEntity"}}' >&2
      exit 254
    fi
    QUERY=$(_flag --query "$@" || true)
    if [ -z "$QUERY" ]; then
      cat "$ROLE_FILE"
      exit 0
    fi
    case "$QUERY" in
      *ExternalId*)
        # Real create_aws_role.sh only ever issues this one shape of query for
        # ExternalIds; match on that instead of implementing general JMESPath.
        python3 - "$ROLE_FILE" <<'PYEOF'
import json, sys

with open(sys.argv[1]) as f:
    role = json.load(f)

statements = role["Role"]["AssumeRolePolicyDocument"].get("Statement", [])
if isinstance(statements, dict):
    statements = [statements]

tokens = []
for s in statements:
    if s.get("Effect") != "Allow":
        continue
    value = (s.get("Condition") or {}).get("StringEquals", {}).get("sts:ExternalId")
    if value is None:
        continue
    tokens.extend(value if isinstance(value, list) else [value])

# --output text on an empty result prints nothing; on a found value it prints the
# tokens separated by whitespace, matching what create_aws_role.sh word-splits on.
print(" ".join(tokens))
PYEOF
        ;;
      *compute-types*)
        python3 - "$ROLE_FILE" <<'PYEOF'
import json, sys

with open(sys.argv[1]) as f:
    role = json.load(f)

for tag in role["Role"].get("Tags", []):
    if tag.get("Key") == "launchpad:compute-types":
        print(tag["Value"])
        sys.exit(0)
print("None")
PYEOF
        ;;
      *)
        echo "fake_aws.sh: unhandled get-role query: $QUERY" >&2
        exit 1
        ;;
    esac
    ;;

  iam.tag-role)
    # Real create_aws_role.sh must pass --tags as a JSON array, never the
    # `Key=...,Value=...` shorthand — the shorthand parser splits on every comma, which
    # breaks the moment a compute-types union value like "ecs_fargate,eks" appears. Parse
    # it as JSON here so a regression back to shorthand fails this test loudly.
    TAGS_JSON=$(_flag --tags "$@")
    python3 - "$ROLE_FILE" "$TAGS_JSON" <<'PYEOF'
import json, sys
role_file, tags_json = sys.argv[1], sys.argv[2]
new_tags = json.loads(tags_json)
with open(role_file) as f:
    role = json.load(f)
tags = [t for t in role["Role"].get("Tags", []) if t["Key"] not in {nt["Key"] for nt in new_tags}]
tags.extend(new_tags)
role["Role"]["Tags"] = tags
with open(role_file, "w") as f:
    json.dump(role, f)
PYEOF
    ;;

  iam.create-role)
    DOC_PATH=$(_file_arg_path --assume-role-policy-document "$@")
    MAX_SESSION=$(_flag --max-session-duration "$@" || echo 3600)
    python3 - "$ROLE_FILE" "$DOC_PATH" "$MAX_SESSION" <<'PYEOF'
import json, sys
role_file, doc_path, max_session = sys.argv[1], sys.argv[2], sys.argv[3]
with open(doc_path) as f:
    doc = json.load(f)
role = {"Role": {"AssumeRolePolicyDocument": doc, "MaxSessionDuration": int(max_session), "Tags": []}}
with open(role_file, "w") as f:
    json.dump(role, f)
PYEOF
    python3 -c "import json;print(json.dumps(json.load(open('$ROLE_FILE'))['Role']['AssumeRolePolicyDocument']))" > "$STATE/last-applied-trust-policy.json"
    ;;

  iam.update-assume-role-policy)
    DOC_PATH=$(_file_arg_path --policy-document "$@")
    python3 - "$ROLE_FILE" "$DOC_PATH" <<'PYEOF'
import json, sys
role_file, doc_path = sys.argv[1], sys.argv[2]
with open(doc_path) as f:
    doc = json.load(f)
with open(role_file) as f:
    role = json.load(f)
role["Role"]["AssumeRolePolicyDocument"] = doc
with open(role_file, "w") as f:
    json.dump(role, f)
PYEOF
    python3 -c "import json;print(json.dumps(json.load(open('$ROLE_FILE'))['Role']['AssumeRolePolicyDocument']))" > "$STATE/last-applied-trust-policy.json"
    ;;

  iam.update-role)
    MAX_SESSION=$(_flag --max-session-duration "$@")
    python3 - "$ROLE_FILE" "$MAX_SESSION" <<'PYEOF'
import json, sys
role_file, max_session = sys.argv[1], sys.argv[2]
with open(role_file) as f:
    role = json.load(f)
role["Role"]["MaxSessionDuration"] = int(max_session)
with open(role_file, "w") as f:
    json.dump(role, f)
PYEOF
    ;;

  iam.get-policy)
    if [ ! -f "$POLICY_FILE" ]; then
      echo '{"Error":{"Code":"NoSuchEntity"}}' >&2
      exit 254
    fi
    QUERY=$(_flag --query "$@" || true)
    if [ "$QUERY" = "Policy.DefaultVersionId" ]; then
      python3 -c "import json;print(json.load(open('$POLICY_FILE'))['DefaultVersionId'])"
    else
      cat "$POLICY_FILE"
    fi
    ;;

  iam.list-policy-versions)
    [ -f "$POLICY_VERSIONS_FILE" ] || echo '["v1"]' > "$POLICY_VERSIONS_FILE"
    QUERY=$(_flag --query "$@" || true)
    case "$QUERY" in
      "length(Versions)")
        python3 -c "import json;print(len(json.load(open('$POLICY_VERSIONS_FILE'))))"
        ;;
      *)
        echo ""
        ;;
    esac
    ;;

  iam.create-policy-version)
    DOC_PATH=$(_file_arg_path --policy-document "$@")
    cp "$DOC_PATH" "$POLICY_DOC_FILE"
    cp "$DOC_PATH" "$STATE/last-applied-policy-document.json"
    python3 -c "import json;json.dump({'DefaultVersionId':'v2'}, open('$POLICY_FILE','w'))"
    ;;

  iam.delete-policy-version)
    : # no-op for the test double
    ;;

  iam.create-policy)
    DOC_PATH=$(_file_arg_path --policy-document "$@")
    cp "$DOC_PATH" "$POLICY_DOC_FILE"
    cp "$DOC_PATH" "$STATE/last-applied-policy-document.json"
    python3 -c "import json;json.dump({'DefaultVersionId':'v1'}, open('$POLICY_FILE','w'))"
    ;;

  iam.attach-role-policy)
    : # no-op
    ;;

  *)
    echo "fake_aws.sh: unhandled command: $SERVICE $ACTION $*" >&2
    exit 1
    ;;
esac
