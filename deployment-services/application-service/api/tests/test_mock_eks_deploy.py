"""SEAM 3 for EKS: the real deploy state machine against MockSession + mock_k8s.

Exercises ApplicationDeploymentService, EKSDeployer, EKSClient and CodeBuildClient with no
boto3 and no cluster, asserting every wait loop terminates, a rollout failure harvests pod
diagnostics into error_message, and the failure unwind deletes in reverse creation order.
"""
import uuid

import pytest

from api.mock import mock_k8s
from api.mock.mock_session import MockSession

ACCOUNT_ID = "000000000000"
CLUSTER_ARN = f"arn:aws:eks:us-west-2:{ACCOUNT_ID}:cluster/infra-abc123"


@pytest.fixture(autouse=True)
def clean_mock_k8s():
    mock_k8s.reset()
    yield
    mock_k8s.reset()


@pytest.fixture(autouse=True)
def dev_mode(monkeypatch):
    from types import SimpleNamespace

    from api.k8s import deployer as deployer_mod
    monkeypatch.setattr(deployer_mod, "app_config", SimpleNamespace(mode="dev"))


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    import time as _time

    monkeypatch.setattr(_time, "sleep", lambda *_a, **_k: None)


@pytest.fixture
def application(schema_db):
    from api.models.application import Application
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
        alb_dns="alb.example.com", ecr_repository_url=f"{ACCOUNT_ID}.dkr.ecr.us-west-2.amazonaws.com/repo",
    )
    return Application.objects.create(
        user=user, infrastructure=infra, name="myapp", project_remote_url="https://github.com/o/r",
        project_branch="main", project_commit_hash="abcdef0123456789", port=8080,
        alloted_cpu=0.5, alloted_memory=1.0, envs={"FOO": "bar"},
    )


@pytest.fixture
def deploy(monkeypatch):
    from api.services import application_deployment_service as svc

    def _deploy(application):
        session = MockSession(region="us-west-2", account_id=ACCOUNT_ID, infra_id=str(application.infrastructure_id))
        service = svc.ApplicationDeploymentService()
        monkeypatch.setattr(service, "_create_aws_session", lambda _infra: session)
        return service.deploy_application(application)

    return _deploy


def _objects(application):
    return mock_k8s.get_mock_apis(str(application.infrastructure_id)).state.objects


def _object_body(application, kind, name):
    objects = mock_k8s.get_mock_apis(str(application.infrastructure_id)).state.objects
    for (obj_kind, _ns, obj_name), body in objects.items():
        if obj_kind == kind and obj_name == name:
            return body
    raise AssertionError(f"{kind}/{name} was never applied")


@pytest.mark.django_db
def test_full_eks_deploy_reaches_active(application, deploy):
    url = deploy(application)

    assert url == "http://alb.example.com/myapp"
    application.refresh_from_db()
    assert application.status == "ACTIVE"
    assert application.error_message is None
    assert application.runtime_refs == {
        "runtime": "eks", "namespace": "app-myapp", "configmap": "myapp-nginx",
        "deployment": "myapp", "service": "myapp", "ingress": "myapp",
    }

    kinds = {(kind, name) for kind, _ns, name in _objects(application)}
    assert ("namespace", "app-myapp") in kinds
    assert ("resourcequota", "launchpad-quota") in kinds
    assert ("limitrange", "launchpad-limits") in kinds
    assert ("networkpolicy", "default-deny-ingress") in kinds
    assert ("networkpolicy", "allow-serving-port") in kinds
    assert ("networkpolicy", "allow-egress") in kinds

    # Cross-tenant isolation: the serving-port policy must admit the ALB's own public
    # subnets and nothing wider. The VPC CNI gives pods addresses inside the VPC CIDR, so
    # an ipBlock of the whole VPC would readmit every other tenant's pods on this port and
    # quietly undo default-deny-ingress.
    policy = _object_body(application, "networkpolicy", "allow-serving-port")
    blocks = [
        peer.ip_block.cidr
        for rule in policy.spec.ingress
        for peer in rule._from
        if peer.ip_block is not None
    ]
    assert blocks == ["10.0.0.0/24", "10.0.1.0/24"], blocks
    assert "10.0.0.0/16" not in blocks
    assert ("configmap", "myapp-nginx") in kinds
    assert ("deployment", "myapp") in kinds
    assert ("service", "myapp") in kinds
    assert ("ingress", "myapp") in kinds


@pytest.mark.django_db
def test_pod_spec_hardening_and_ingress_paths(application, deploy):
    deploy(application)
    objects = _objects(application)

    pod = objects[("deployment", "app-myapp", "myapp")].spec.template.spec
    assert pod.automount_service_account_token is False
    for container in pod.containers:
        assert container.security_context.allow_privilege_escalation is False
        assert container.security_context.capabilities.drop == ["ALL"]
        # Customer Dockerfiles routinely run as root; forcing non-root would CrashLoop them.
        assert container.security_context.run_as_non_root is None
    app_container, nginx = pod.containers
    assert app_container.resources.limits == {"cpu": "500m", "memory": "1024Mi"}
    assert nginx.ports[0].container_port == 18080
    assert nginx.readiness_probe.http_get.port == 18080

    ingress = objects[("ingress", "app-myapp", "myapp")]
    assert ingress.spec.ingress_class_name == "launchpad-alb"
    assert ingress.metadata.annotations["alb.ingress.kubernetes.io/healthcheck-path"] == "/"
    assert ingress.metadata.annotations["alb.ingress.kubernetes.io/success-codes"] == "200-499"
    assert [p.path for p in ingress.spec.rules[0].http.paths] == ["/myapp", "/myapp/*"]

    nginx_conf = objects[("configmap", "app-myapp", "myapp-nginx")].data["nginx.conf"]
    assert "listen 18080;" in nginx_conf
    assert "X-Forwarded-Prefix /myapp" in nginx_conf


@pytest.mark.django_db
def test_redeploy_is_idempotent_and_unwinds_nothing_preexisting(application, deploy, monkeypatch):
    from api.k8s import deployer as deployer_mod

    deploy(application)
    deleted = []
    real_delete = deployer_mod.delete_object
    monkeypatch.setattr(
        deployer_mod, "delete_object",
        lambda apis, ref: (deleted.append(ref["kind"]), real_delete(apis, ref))[1],
    )
    mock_k8s.get_mock_apis(str(application.infrastructure_id)).state.available_replicas = 0
    monkeypatch.setattr(deployer_mod, "ROLLOUT_TIMEOUT_SECONDS", 0)

    with pytest.raises(deployer_mod.RolloutFailed):
        deploy(application)

    # Everything already existed, so the unwind owns nothing and the live app survives.
    assert deleted == []
    assert ("ingress", "app-myapp", "myapp") in _objects(application)


@pytest.mark.django_db
def test_rollout_failure_harvests_events_and_unwinds_in_reverse(application, deploy, monkeypatch):
    from api.k8s import deployer as deployer_mod

    state = mock_k8s.get_mock_apis(str(application.infrastructure_id)).state
    state.available_replicas = 0
    state.container_waiting = ("ImagePullBackOff", "manifest for repo:tag not found")
    state.warning_events = [("Failed", "Error: ImagePullBackOff")]
    monkeypatch.setattr(deployer_mod, "ROLLOUT_TIMEOUT_SECONDS", 0)

    deleted = []
    real_delete = deployer_mod.delete_object
    monkeypatch.setattr(
        deployer_mod, "delete_object",
        lambda apis, ref: (deleted.append(ref["kind"]), real_delete(apis, ref))[1],
    )

    with pytest.raises(deployer_mod.RolloutFailed):
        deploy(application)

    application.refresh_from_db()
    assert application.status == "FAILED"
    assert "ImagePullBackOff" in application.error_message
    assert "manifest for repo:tag not found" in application.error_message
    assert "event Failed" in application.error_message
    assert deleted == ["ingress", "service", "deployment", "configmap", "namespace"]
    assert _objects(application) == {}


@pytest.mark.django_db
def test_non_dns_safe_name_is_refused_before_any_object_is_created(application, deploy):
    application.name = "my.app"
    application.save()

    with pytest.raises(ValueError, match="not deployable on Kubernetes"):
        deploy(application)

    assert _objects(application) == {}


@pytest.mark.django_db
def test_mock_k8s_refuses_a_real_infrastructure(application):
    from api.k8s.deployer import k8s_apis

    application.infrastructure.is_mock = False
    with (
        pytest.raises(ValueError, match="Refusing mock Kubernetes access"),
        k8s_apis(MockSession(region="us-west-2", account_id=ACCOUNT_ID), application.infrastructure, "c"),
    ):
        pass
