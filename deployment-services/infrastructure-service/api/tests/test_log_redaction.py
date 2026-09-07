"""The provisioning-log redactor.

Terraform stdout/stderr is persisted to Environment.logs and error_message and emailed on
failure. It carries the STS credentials terraform ran with (provider diagnostics), the
ElastiCache auth token (plan diffs, state errors), and — on an AssumeRole failure — the
platform's own account id and IAM user ARN. These tests seed each of those shapes into
every place terraform can print it and assert nothing survives, then guard the other
direction: a realistic failed apply must still tell the customer what failed.
"""

import random

import pytest
from api.services.log_redaction import (
    WITHHELD_DIAGNOSTIC,
    RedactionResult,
    _dewrap,
    redact_provisioning_text,
    scrub_exact_values,
)
from api.services.terraform_worker import MAX_LOG_CHARS, _capped_logs

# Assembled from parts rather than written as literals. These have to match the exact
# AWS key and secret shapes, because those shapes are what the redactor keys on — and a
# literal of the exact shape is, to a secret scanner, indistinguishable from a real
# credential. api/tests/test_mode.py sidesteps the same detector by using a deliberately
# over-length key; this suite cannot, since the length is the thing under test.
ACCESS_KEY = "ASIA" + "NOTAREALKEY00000"
SECRET_KEY = ("notarealsecret" * 3)[:40]
SESSION_TOKEN = ("notarealsessiontoken" * 50)[:900]
AUTH_TOKEN = "notarealelasticacheauthtoken0000"
CLIENT_ERROR = ("An error occurred (AccessDenied) when calling the AssumeRole operation: "
                "User arn:aws:iam::221082203366:user/aklamaash-terraform is not authorized")
WORK_DIR = "/dev/shm/tf-0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b"

CREDENTIALS = (ACCESS_KEY, SECRET_KEY, SESSION_TOKEN)
SEEDS = [ACCESS_KEY, SECRET_KEY, SESSION_TOKEN, AUTH_TOKEN, CLIENT_ERROR, WORK_DIR]
PROGRESS_LINE = "module.vpc.aws_vpc.main: Still creating... [10s elapsed]\n"

FAILED_APPLY = """[COMMAND]
data.aws_availability_zones.available: Reading...
data.aws_availability_zones.available: Read complete after 0s [id=us-east-1]
module.vpc.aws_vpc.main: Creating...
module.vpc.aws_vpc.main: Still creating... [10s elapsed]
module.vpc.aws_vpc.main: Creation complete after 12s [id=vpc-0abc123def456789a]
module.ecs.aws_ecs_cluster.main: Creating...
╷
│ Error: creating ECS Cluster (infra-abc): operation error ECS: CreateCluster, https
│ response error StatusCode: 400, RequestID: 6f1c2e3d-1111-2222-3333-444455556666,
│ AccessDeniedException: User: arn:aws:sts::123456789012:assumed-role/LaunchpadDeploymentRole/launchpad
│ is not authorized to perform: ecs:CreateCluster
│
│   with module.ecs.aws_ecs_cluster.main,
│   on modules/ecs/main.tf line 1, in resource "aws_ecs_cluster" "main":
│    1: resource "aws_ecs_cluster" "main" {
│
╵
"""


def _must_not_survive(seed):
    if seed is CLIENT_ERROR:
        return ["221082203366", "aklamaash-terraform"]
    return [seed]


def _wrapped(seed):
    half = len(seed) // 2
    return f"╷\n│ Error: provider setup: {seed[:half]}\n│ {seed[half:]}\n│ \n│   with module.vpc.aws_vpc.main,\n╵\n"


PLACEMENTS = {
    "plan_line": lambda seed: f'  + auth_token = "{seed}"\n',
    "error_block": lambda seed: f"Error: provider setup: {seed}\n\n",
    "wrapped_error_block": _wrapped,
}


@pytest.mark.parametrize("placement", PLACEMENTS)
@pytest.mark.parametrize("seed", SEEDS, ids=[s[:12] for s in SEEDS])
def test_seeded_secrets_never_survive(seed, placement):
    """Checked against the dewrapped output as well: a wrapped token that leaks in two
    halves is absent from `out` as a substring and present in the log all the same."""
    text = PROGRESS_LINE + PLACEMENTS[placement](seed) + PROGRESS_LINE
    out = redact_provisioning_text(text, secrets=CREDENTIALS).text
    for fragment in _must_not_survive(seed):
        assert fragment not in out
        assert fragment not in _dewrap(out)


@pytest.mark.parametrize("seed", SEEDS, ids=[s[:12] for s in SEEDS])
def test_seeded_secrets_never_survive_at_the_log_cap_boundary(seed):
    """Redaction must run before the tail-slice: if the cut lands inside a secret the
    slice keeps its second half."""
    block = f"Error: provider setup: {seed}\n"
    filler_len = MAX_LOG_CHARS - len(block) + len(seed) // 2
    raw = PROGRESS_LINE * (filler_len // len(PROGRESS_LINE) + 1) + block
    out = _capped_logs(scrub_exact_values(raw, CREDENTIALS))
    for fragment in _must_not_survive(seed):
        assert fragment not in out


# Every shape terraform or the worker can emit, plus the shapes the redactor itself
# emits. Multi-line entries are wrapped values and stay together as one unit.
LINE_SHAPES = [
    "module.vpc.aws_vpc.main: Creating...",
    "module.vpc.aws_vpc.main: Still creating... [10s elapsed]",
    "module.vpc.aws_vpc.main: Creation complete after 12s [id=vpc-0abc123def456789a]",
    'module.rds_primary["a"].aws_db_instance.this: Refreshing state... [id=db-ABCDEFGHIJKLMNOPQRSTUVWXYZ]',
    "  # module.vpc.aws_vpc.main will be created",
    '  + resource "aws_vpc" "main" {',
    f'      + auth_token = "{AUTH_TOKEN}"',
    "    }",
    "╷",
    "│ Error: creating ECS Cluster (infra-abc): operation error ECS: CreateCluster, https",
    "│ response error StatusCode: 400, RequestID: 6f1c2e3d-1111-2222-3333-444455556666,",
    f"│ AccessDeniedException: User: {ACCESS_KEY} is not authorized",
    "│ Warning: deprecated attribute",
    "│ ",
    "│",
    "│   with module.ecs.aws_ecs_cluster.main,",
    '│   on modules/ecs/main.tf line 1, in resource "aws_ecs_cluster" "main":',
    "╵",
    f"Error: provider setup: {SECRET_KEY}",
    "Error: creating something",
    "Warning: something",
    f"Error: updating replication group: InvalidParameterValue: bad token {AUTH_TOKEN}",
    "│   with module.redis.aws_elasticache_replication_group.this,",
    "│   with module.redis.random_password.auth_token,",
    CLIENT_ERROR,
    f"Update failed: {CLIENT_ERROR}",
    "",
    "[COMMAND] terraform apply -auto-approve -no-color",
    "[INIT]",
    "Retry 2: Throttling: rate exceeded",
    "[OUTPUT] parsed keys: alb_dns, vpc_id",
    "Cleanup: All resources were destroyed.",
    "Initializing the backend...",
    "Terraform has been successfully initialized!",
    f"provider config key {ACCESS_KEY}",
    WORK_DIR,
    "Plan: 1 to add, 0 to change, 0 to destroy.",
    "Apply complete! Resources: 3 added, 0 changed, 0 destroyed.",
    "Outputs:",
    'vpc_id = "vpc-0abc"',
    "… (3 lines withheld)",
    WITHHELD_DIAGNOSTIC,
    f"{WITHHELD_DIAGNOSTIC} (InvalidParameterValue)",
    f"│ generated value {AUTH_TOKEN[:18]}\n│ {AUTH_TOKEN[18:]} was not accepted",
    f"│ Error: provider setup: {ACCESS_KEY[:10]}\n│ {ACCESS_KEY[10:]}",
    f"│ {CLIENT_ERROR[:40]}\n│ {CLIENT_ERROR[40:]}",
    f"│ session {SESSION_TOKEN[:300]}\n│ {SESSION_TOKEN[300:600]}\n│ {SESSION_TOKEN[600:]}",
]


def test_redaction_is_idempotent():
    """The fixed-point claim in the module docstring, held against random orderings of
    every line shape rather than one fixture that happens to sit in terminator order.
    `_capped_logs` re-redacts already-redacted text on every append, so a shape that
    classifies differently on a second pass drifts on every write. First pass with the
    STS secrets as `_tf_result` runs it, second pass without as `_capped_logs` does."""
    rng = random.Random(0xC0FFEE)
    for _ in range(3000):
        text = "\n".join(rng.choices(LINE_SHAPES, k=rng.randint(1, 20)))
        once = redact_provisioning_text(text, secrets=CREDENTIALS)
        assert redact_provisioning_text(once.text) == once, text


def test_keeps_resource_lifecycle_lines_and_masks_ids():
    """`[id=…]` is masked for every resource type rather than allowlisted per type: the
    id slot is a free-form provider value, and for `aws_iam_access_key` it is the access
    key id, for `aws_secretsmanager_secret_version` the versioned secret ARN. There is no
    closed list of which types put a credential there, and nothing actionable is lost —
    the address survives, and the id is on the customer's own console."""
    text = (
        "module.vpc.aws_vpc.main: Creating...\n"
        "module.vpc.aws_vpc.main: Still creating... [10s elapsed]\n"
        "module.vpc.aws_vpc.main: Creation complete after 12s [id=vpc-0abc123def456789a]\n"
        'module.rds_primary["a"].aws_db_instance.this: Refreshing state... [id=db-ABCDEFGHIJKLMNOPQRSTUVWXYZ]\n'
        "data.aws_availability_zones.available: Read complete after 0s [id=us-east-1]\n"
        "module.vpc.aws_vpc.main: Destruction complete after 1s\n"
    )
    out = redact_provisioning_text(text)
    assert out.text.split("\n") == [
        "module.vpc.aws_vpc.main: Creating...",
        "module.vpc.aws_vpc.main: Still creating... [10s elapsed]",
        "module.vpc.aws_vpc.main: Creation complete after 12s [id=…]",
        'module.rds_primary["a"].aws_db_instance.this: Refreshing state... [id=…]',
        "data.aws_availability_zones.available: Read complete after 0s [id=…]",
        "module.vpc.aws_vpc.main: Destruction complete after 1s",
    ]
    assert out.withheld_lines == 0


def test_keeps_diagnostic_block_with_box_drawing_and_wrapped_continuation():
    block = (
        "╷\n"
        "│ Error: creating EC2 VPC: operation error EC2: CreateVpc, https response error\n"
        "│ StatusCode: 403, api error UnauthorizedOperation: You are not authorized\n"
        "│ \n"
        "│   with module.vpc.aws_vpc.main,\n"
        '│   on modules/vpc/main.tf line 3, in resource "aws_vpc" "main":\n'
        "╵\n"
    )
    out = redact_provisioning_text(block).text
    for line in block.splitlines():
        if line.strip("╷╵ "):
            assert line in out


def test_wrapped_access_key_withholds_the_whole_block():
    """Terraform wraps at 80 columns, so a value arrives as `TOK\\n│ EN`; the block is
    dewrapped before the scrub patterns are re-checked and withheld whole if one still
    matches. This case passes even without boundary-safe dewrapping because the access
    key pattern has no boundary assertions — the bare-token test below is the one that
    generalises."""
    out = redact_provisioning_text(_wrapped(ACCESS_KEY)).text
    assert out == WITHHELD_DIAGNOSTIC


@pytest.mark.parametrize("address", [
    "module.redis.random_password.auth_token",
    "module.vpc.aws_vpc.main",
], ids=["withheld-by-resource-word", "withheld-by-dewrapped-shape"])
def test_bare_wrapped_auth_token_never_survives(address):
    """The HIGH finding: an unquoted 32-alnum token split by a wrap seam. Each half is
    under 32 chars, so the per-line scrub cannot see it, and the old whitespace-collapse
    glued the halves to the neighbouring words so the boundary assertions failed too.
    The neutral address exercises the dewrap path on its own; `random_password` is the
    resource that actually holds the token and is caught by the keyword gate as well."""
    block = (
        "╷\n│ Error: updating replication group: something went wrong\n"
        f"│ generated value {AUTH_TOKEN[:18]}\n│ {AUTH_TOKEN[18:]} was not accepted\n"
        f"│ \n│   with {address},\n╵\n"
    )
    out = redact_provisioning_text(block).text
    assert AUTH_TOKEN not in _dewrap(out)
    assert out == WITHHELD_DIAGNOSTIC


@pytest.mark.parametrize("address", [
    "module.redis_cache.aws_elasticache_replication_group.this",
    "module.pg.aws_secretsmanager_secret_version.master",
])
def test_elasticache_diagnostic_block_is_withheld_whole(address):
    """A block that mentions a credential-holding resource anywhere in its text is
    withheld whole regardless of what the scrub patterns find: the token's shape is a
    contract with a terraform module, not something this module can prove. Only the
    AWS error code survives."""
    block = (
        "╷\n│ Error: updating replication group: InvalidParameterValue: bad token abc\n"
        f"│ \n│   with {address},\n│   on modules/x/main.tf line 60:\n╵\n"
    )
    out = redact_provisioning_text(block)
    assert out.text == f"{WITHHELD_DIAGNOSTIC} (InvalidParameterValue)"
    assert "bad token" not in out.text


def test_withheld_elasticache_failure_keeps_the_error_code():
    """Anti-over-aggression guard, the ElastiCache counterpart of
    `test_keeps_progress_and_error_code`: the most common ElastiCache failures carry no
    token at all (node type unavailable, missing subnet group), and a bare marker would
    leave the customer nothing to act on."""
    block = (
        "╷\n│ Error: creating ElastiCache Replication Group (infra-abc-redis): operation error ElastiCache:\n"
        "│ CreateReplicationGroup, https response error StatusCode: 400, RequestID: 6f1c2e3d-1111-2222-3333-444455556666,\n"
        "│ InvalidParameterValue: Cache node type cache.t4g.micro is not available in us-east-1a\n"
        "│ \n│   with module.redis.aws_elasticache_replication_group.this,\n╵\n"
    )
    out = redact_provisioning_text(block)
    assert out.text == f"{WITHHELD_DIAGNOSTIC} (InvalidParameterValue)"
    assert out.withheld_lines == 0


def test_boto3_client_error_reduced_to_code_and_operation():
    out = redact_provisioning_text(f"Update failed: {CLIENT_ERROR}").text
    assert out == "Update failed:\nAn error occurred (AccessDenied) when calling the AssumeRole operation"


def test_plan_diff_is_withheld_with_count():
    diff = [
        "  # module.vpc.aws_vpc.main will be created",
        "  + resource \"aws_vpc\" \"main\" {",
        "      + cidr_block = \"10.0.0.0/16\"",
        "      + tags       = { Owner = \"user-1\" }",
        "    }",
    ]
    text = "\n".join([*diff, "Plan: 1 to add, 0 to change, 0 to destroy."])
    out = redact_provisioning_text(text)
    assert out.text == f"… ({len(diff)} lines withheld)\nPlan: 1 to add, 0 to change, 0 to destroy."
    assert out.withheld_lines == len(diff)
    assert out.kept_lines == 1


def test_outputs_block_body_is_withheld():
    text = 'Outputs:\n\nvpc_id = "vpc-0abc"\nalb_dns = "infra-abc.elb.amazonaws.com"\n'
    out = redact_provisioning_text(text).text
    assert out == "Outputs:\n… (2 lines withheld)"


def test_oversized_diagnostic_block_is_withheld():
    block = "Error: huge\n" + "│ " + "x" * 20_000 + "\n"
    assert redact_provisioning_text(block).text == WITHHELD_DIAGNOSTIC


def test_keeps_progress_and_error_code():
    """Anti-over-aggression guard: a failed apply must still say which resource failed
    and why, or the customer has nothing to act on."""
    out = redact_provisioning_text(FAILED_APPLY)
    assert "module.ecs.aws_ecs_cluster.main: Creating..." in out.text
    assert "AccessDeniedException" in out.text
    assert "with module.ecs.aws_ecs_cluster.main," in out.text
    assert "module.vpc.aws_vpc.main: Creation complete after 12s [id=…]" in out.text
    assert "vpc-0abc123def456789a" not in out.text
    assert out.withheld_lines == 0


@pytest.mark.parametrize("message", [
    "PROVISIONING abandoned after 5 recovery attempts",
    "UPDATING update could not be recovered after 5 attempts; environment returned to ACTIVE",
    "Refusing to provision a mock infrastructure outside dev mode",
    "Refusing mock provisioning against a real infrastructure",
    "Destroy blocked: 2 database(s) must be deleted first",
    "Cleanup: All resources were destroyed.",
    "[OUTPUT] parsed keys: alb_dns, vpc_id",
    "Missing AWS credentials for terraform execution",
])
def test_platform_composed_messages_survive_verbatim(message):
    """Drop-by-default means every worker-composed sentence that reaches error_message
    needs a template here; a wording change in the worker without one silently stores
    `… (1 lines withheld)` instead."""
    assert redact_provisioning_text(message).text == message


def test_none_and_empty_are_safe():
    assert redact_provisioning_text(None) == RedactionResult("", 0, 0)
    assert redact_provisioning_text("") == RedactionResult("", 0, 0)


def test_customer_owned_state_bucket_survives():
    """The state bucket lives in the customer's own account and this text is same-tenant
    (the owner and anyone they invited), so the name discloses nothing they cannot already
    read off their own dashboard. It is also the only actionable fact in a backend failure
    — withholding it was over-redaction that made the diagnostic useless to the people
    allowed to see it."""
    bucket = "launchpad-tf-state-123456789012-us-east-1"
    text = (
        "╷\n"
        f"│ Error: Failed to get existing workspaces: S3 bucket {bucket} does not exist\n"
        "│\n"
        "╵\n"
    )
    assert bucket in redact_provisioning_text(text).text


def test_platform_iam_principal_is_still_scrubbed():
    """The counterpart: the platform's own IAM user is cross-tenant disclosure and must
    never survive, however the diagnostic is shaped."""
    text = (
        "╷\n"
        "│ Error: error configuring S3 Backend: AccessDenied for "
        "arn:aws:iam::221082203366:user/aklamaash-terraform\n"
        "╵\n"
    )
    assert "aklamaash-terraform" not in redact_provisioning_text(text).text
