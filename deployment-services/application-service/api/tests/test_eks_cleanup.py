"""Cleanup routes on the job's runtime; jobs enqueued before this release still route to ECS."""
import json
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from api.mock import mock_k8s
from api.mock.mock_session import MockSession
from api.services.deployment_queue import DeploymentQueue

ACCOUNT_ID = "000000000000"
CLUSTER_ARN = f"arn:aws:eks:us-west-2:{ACCOUNT_ID}:cluster/infra-abc123"
REFS = {
    "runtime": "eks", "namespace": "app-myapp", "configmap": "myapp-nginx",
    "deployment": "myapp", "service": "myapp", "ingress": "myapp",
}


@pytest.fixture(autouse=True)
def clean_mock_k8s():
    mock_k8s.reset()
    yield
    mock_k8s.reset()


def _enqueued_job(monkeypatch, **kwargs):
    redis = MagicMock()
    monkeypatch.setattr(DeploymentQueue, "get_redis", staticmethod(lambda: redis))
    DeploymentQueue.enqueue_cleanup(app_id="a1", infrastructure_id="i1", **kwargs)
    return json.loads(redis.rpush.call_args[0][1])


def test_legacy_cleanup_job_carries_no_runtime(monkeypatch):
    job = _enqueued_job(monkeypatch, service_arn="arn:svc", target_group_arn="arn:tg")

    assert "runtime" not in job
    assert "refs" not in job
    assert job["service_arn"] == "arn:svc"


def test_eks_cleanup_job_carries_runtime_and_refs(monkeypatch):
    job = _enqueued_job(monkeypatch, runtime="eks", refs=REFS)

    assert job["runtime"] == "eks"
    assert job["refs"] == REFS


@pytest.fixture
def infrastructure(schema_db):
    from api.models.environment import Environment
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    user = User.objects.create(id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@x.com", user_name="t")
    infra = Infrastructure.objects.create(
        user=user, name="infra-x", cloud_provider="aws", compute_type="eks",
        max_cpu=4.0, max_memory=8.0, is_mock=True, code=ACCOUNT_ID,
        is_cloud_authenticated=True, metadata={"aws_region": "us-west-2"},
    )
    Environment.objects.create(
        infrastructure=infra, status="ACTIVE", vpc_id="vpc-1", cluster_arn=CLUSTER_ARN,
        alb_dns="alb.example.com", ecr_repository_url="repo",
    )
    return infra


@pytest.fixture
def dev_mode(monkeypatch):
    from aws import session as session_mod

    from api.k8s import deployer as deployer_mod

    monkeypatch.setattr(deployer_mod, "app_config", SimpleNamespace(mode="dev"))
    monkeypatch.setattr(session_mod, "app_config", SimpleNamespace(mode="dev"))


@pytest.mark.django_db
def test_runtime_refs_drive_k8s_deletion_in_order(infrastructure, dev_mode, monkeypatch):
    from api.k8s import deployer as deployer_mod
    from api.models.application import Application
    from api.services.application_cleanup_service import ApplicationCleanupService

    apis = mock_k8s.get_mock_apis(str(infrastructure.id))
    session = MockSession(region="us-west-2", account_id=ACCOUNT_ID, infra_id=str(infrastructure.id))
    for kind, name in (
        ("namespace", "app-myapp"), ("configmap", "myapp-nginx"),
        ("deployment", "myapp"), ("service", "myapp"), ("ingress", "myapp"),
    ):
        apis.state.objects[(kind, "" if kind == "namespace" else "app-myapp", name)] = object()

    deleted = []
    real_delete = deployer_mod.delete_object
    monkeypatch.setattr(
        deployer_mod, "delete_object",
        lambda a, ref: (deleted.append(ref["kind"]), real_delete(a, ref))[1],
    )
    application = Application.objects.create(
        user=infrastructure.user, infrastructure=infrastructure, name="myapp",
        project_remote_url="https://github.com/o/r", project_branch="main",
        project_commit_hash="", runtime_refs=REFS,
    )
    monkeypatch.setattr(
        "api.services.application_cleanup_service.create_boto3_session", lambda _i: session
    )

    ApplicationCleanupService().cleanup_application(application)

    assert deleted == ["ingress", "service", "deployment", "configmap", "namespace"]
    assert apis.state.objects == {}


@pytest.mark.django_db
def test_deleting_already_absent_objects_is_tolerated(infrastructure, dev_mode):
    from api.k8s.deployer import delete_runtime_resources

    session = MockSession(region="us-west-2", account_id=ACCOUNT_ID, infra_id=str(infrastructure.id))
    delete_runtime_resources(session, infrastructure, infrastructure.environments.first(), REFS)


@pytest.mark.django_db
def test_application_without_runtime_refs_takes_the_ecs_path(infrastructure, dev_mode, monkeypatch):
    from api.models.application import Application
    from api.services.application_cleanup_service import ApplicationCleanupService

    session = MockSession(region="us-west-2", account_id=ACCOUNT_ID, infra_id=str(infrastructure.id))
    monkeypatch.setattr(
        "api.services.application_cleanup_service.create_boto3_session", lambda _i: session
    )
    monkeypatch.setattr(
        "api.services.application_cleanup_service.delete_runtime_resources",
        lambda *a, **k: pytest.fail("legacy application must not take the Kubernetes path"),
    )
    application = Application.objects.create(
        user=infrastructure.user, infrastructure=infrastructure, name="legacy",
        project_remote_url="https://github.com/o/r", project_branch="main",
        project_commit_hash="", target_group_arn="arn:aws:elasticloadbalancing:::targetgroup/t/1",
    )

    ApplicationCleanupService().cleanup_application(application)
