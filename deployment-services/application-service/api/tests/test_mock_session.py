"""SEAM 3: MockSession drives the real deploy/sleep/cleanup state machine with no boto3.

Exercises the actual ECSClient / ALBClient / CodeBuildClient wrappers and the cleanup
service against a MockSession, asserting every wait loop (build, service-stable,
target-healthy, INACTIVE transition) terminates successfully and client.exceptions.*
are real Exception subclasses.
"""
import re

import pytest

from api.mock.mock_session import MockSession
from aws.alb import ALBClient
from aws.codebuild import CodeBuildClient
from aws.ecs import ECSClient

SG_RE = re.compile(r"^sg-[0-9a-f]{8,17}$")
VPC_RE = re.compile(r"^vpc-[0-9a-f]{8,17}$")
SUBNET_RE = re.compile(r"^subnet-[0-9a-f]{8,17}$")


@pytest.fixture
def session():
    return MockSession(region="us-east-1", account_id="000000000000")


# --- exceptions are real Exception subclasses ------------------------------

def test_client_exceptions_are_exception_subclasses(session):
    c = session.client("ecs")
    for name in ("ResourceInUseException", "ResourceNotFoundException", "AnythingException"):
        exc_cls = getattr(c.exceptions, name)
        assert issubclass(exc_cls, Exception)
        # The mock caches by name so repeated access yields the same class
        # (so `except client.exceptions.X` matches a raised instance).
        assert getattr(c.exceptions, name) is exc_cls
        with pytest.raises(exc_cls):
            raise exc_cls("boom")


def test_meta_region_name_present(session):
    assert session.client("ecs").meta.region_name == "us-east-1"


# --- build wait (CodeBuild) ------------------------------------------------

def test_codebuild_wait_for_build_succeeds(session):
    cb = CodeBuildClient(session)
    build_id = cb.client.start_build(projectName="proj")["build"]["id"]
    assert build_id
    status = cb.get_build_status(build_id)
    assert status["status"] == "SUCCEEDED"
    assert cb.wait_for_build(build_id, timeout=5) is True


# --- service-stable wait (ECS) ---------------------------------------------

def test_ecs_wait_for_service_stable_returns_immediately(session):
    ecs = ECSClient(session)
    # MockClient reports runningCount==desiredCount==1, PRIMARY/COMPLETED.
    assert ecs.wait_for_service_stable("arn:cluster", "svc", timeout=5) is True


def test_ecs_register_task_definition_returns_arn(session):
    ecs = ECSClient(session)
    arn = ecs.create_task_definition(
        family="app",
        image="img:latest",
        cpu=0.25,
        memory=0.5,
        envs={"FOO": "bar"},
        execution_role_arn="arn:aws:iam::000000000000:role/exec",
        container_port=8000,
    )
    assert "task-definition" in arn


# --- target-healthy wait (ALB) ---------------------------------------------

def test_alb_describe_target_health_reports_healthy(session):
    alb = ALBClient(session)
    resp = alb.client.describe_target_health(TargetGroupArn="arn:tg")
    healthy = sum(
        1 for t in resp["TargetHealthDescriptions"] if t["TargetHealth"]["State"] == "healthy"
    )
    assert healthy >= 1


# --- cleanup INACTIVE transition -------------------------------------------

def test_cleanup_delete_service_reaches_inactive(session, monkeypatch):
    import time as _time

    from api.services import application_cleanup_service as cleanup_mod

    # The cleanup service imports `time` locally inside the function; patch the
    # global time.sleep so the INACTIVE poll loop doesn't actually sleep.
    monkeypatch.setattr(_time, "sleep", lambda *_a, **_k: None)
    svc = cleanup_mod.ApplicationCleanupService()
    cluster = "arn:aws:ecs:us-east-1:000000000000:cluster/c"
    service_arn = "arn:aws:ecs:us-east-1:000000000000:service/c/my-svc"
    # delete_service marks it deleted; the subsequent describe_services returns INACTIVE.
    # Should return cleanly (no RuntimeError raised).
    svc._delete_ecs_service(session, cluster, service_arn)


# --- describe_subnets / security groups shapes used during deploy ----------

def test_ec2_describe_subnets_and_create_sg_shapes(session):
    ec2 = session.client("ec2")
    subnets = ec2.describe_subnets()["Subnets"]
    assert len(subnets) >= 1
    for s in subnets:
        assert SUBNET_RE.match(s["SubnetId"]), s["SubnetId"]
    sg = ec2.create_security_group(GroupName="app-sg")["GroupId"]
    assert SG_RE.match(sg), sg


def test_iam_get_role_shape(session):
    iam = session.client("iam")
    role = iam.get_role(RoleName="exec")["Role"]
    assert role["RoleName"] == "exec"
    assert role["Arn"].startswith("arn:aws:iam::")
