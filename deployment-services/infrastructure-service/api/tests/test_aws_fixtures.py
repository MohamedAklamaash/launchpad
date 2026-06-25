"""Tests for api.mock.aws_fixtures — synthesized dev-mode AWS outputs/metadata.

Validates the synthesized values satisfy the same shape/validation the real
publish path enforces (alb_dns pattern, hex-only sg-/vpc- ids).
"""
import re
import uuid

from api.mock import aws_fixtures

# Same regex the publish path in terraform_worker._save_outputs uses.
SG_RE = re.compile(r"^sg-[0-9a-f]{8,17}$")
ALB_DNS_RE = re.compile(r"^dev-mock-.*\.elb\.amazonaws\.com$")
VPC_RE = re.compile(r"^vpc-[0-9a-f]{8,17}$")


class _FakeInfra:
    def __init__(self, id=None, code="123456789012", metadata=None):
        self.id = id or uuid.uuid4()
        self.code = code
        self.metadata = metadata


def test_resolve_region_from_metadata():
    infra = _FakeInfra(metadata={"aws_region": "eu-west-1"})
    assert aws_fixtures.resolve_region(infra) == "eu-west-1"


def test_resolve_region_defaults_when_missing():
    assert aws_fixtures.resolve_region(_FakeInfra(metadata=None)) == "us-west-2"
    assert aws_fixtures.resolve_region(_FakeInfra(metadata={})) == "us-west-2"


def test_synthesize_outputs_has_all_eight_fields():
    infra = _FakeInfra()
    outputs = aws_fixtures.synthesize_environment_outputs(infra, "us-east-1")
    expected = {
        "vpc_id",
        "cluster_arn",
        "alb_arn",
        "alb_dns",
        "alb_security_group_id",
        "target_group_arn",
        "ecr_repository_url",
        "ecs_task_execution_role_arn",
    }
    assert set(outputs) == expected
    assert all(outputs[k] for k in expected)


def test_alb_dns_matches_publish_pattern():
    outputs = aws_fixtures.synthesize_environment_outputs(_FakeInfra(), "us-east-1")
    assert ALB_DNS_RE.match(outputs["alb_dns"])
    assert outputs["alb_dns"].endswith(".elb.amazonaws.com")


def test_sg_and_vpc_ids_are_hex_only_and_pass_validation():
    outputs = aws_fixtures.synthesize_environment_outputs(_FakeInfra(), "us-east-1")
    assert SG_RE.match(outputs["alb_security_group_id"]), outputs["alb_security_group_id"]
    assert VPC_RE.match(outputs["vpc_id"]), outputs["vpc_id"]


def test_assumed_role_metadata_is_marked_mock():
    meta = aws_fixtures.synthesize_assumed_role_metadata(_FakeInfra())
    assert meta["is_mock"] is True
    assert meta["aws_access_key_id"].startswith("ASIAMOCK")
    assert "assumed_role_arn" in meta
    assert "expiration" in meta
