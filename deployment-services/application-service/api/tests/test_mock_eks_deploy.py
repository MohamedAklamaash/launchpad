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
def _eks_host_mode_flag(settings):
    # B1 (security review): EKS host mode is gated behind this setting, default off — most
    # of this file's tests want it on so the underlying logic is actually exercised; the
    # one test that specifically covers the flag being off overrides it back to False.
    settings.EKS_HOST_MODE_ENABLED = True


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
        "host_ingress": "myapp-host",
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
    app_container, nginx = pod.containers
    # Customer Dockerfiles routinely run as root; forcing non-root would CrashLoop them.
    assert app_container.security_context.run_as_non_root is None
    assert app_container.security_context.run_as_user is None
    # The stock nginx image, started as root with ALL capabilities dropped, dies chowning
    # its temp dirs. As its own uid it never chowns, so it needs a writable cache dir and a
    # pid file outside root-owned /var/run.
    assert nginx.security_context.run_as_non_root is True
    assert nginx.security_context.run_as_user == 101
    assert "pid /tmp/nginx.pid;" in " ".join(nginx.command)
    assert "daemon off;" in " ".join(nginx.command)
    cache_mount = next(m for m in nginx.volume_mounts if m.mount_path == "/var/cache/nginx")
    cache_volume = next(v for v in pod.volumes if v.name == cache_mount.name)
    assert cache_volume.empty_dir is not None
    assert app_container.resources.limits == {"cpu": "500m", "memory": "1024Mi"}
    assert nginx.ports[0].container_port == 18080
    assert nginx.readiness_probe.http_get.port == 18080

    ingress = objects[("ingress", "app-myapp", "myapp")]
    assert ingress.spec.ingress_class_name == "launchpad-alb"
    assert ingress.metadata.annotations["alb.ingress.kubernetes.io/healthcheck-path"] == "/"
    assert ingress.metadata.annotations["alb.ingress.kubernetes.io/success-codes"] == "200-499"
    # B1 fix: explicit on both Ingresses, never an implicit no-annotation default — see
    # _ingress_manifest's docstring.
    assert ingress.metadata.annotations["alb.ingress.kubernetes.io/listen-ports"] == '[{"HTTP": 80}]'
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


# ── F1b part 3a: host-mode routing on EKS ────────────────────────────────────────────────

@pytest.fixture
def tls_ready_application(application, settings):
    from api.mock.mock_session import mock_eks_alb_dns
    from api.models.environment import Environment

    settings.PLATFORM_BASE_DOMAIN = "launchpad.aklamaash.me"
    infra = application.infrastructure
    infra.dns_label = "0123456789abcdef"
    infra.tls_status = "ISSUED"
    infra.dns_synced = True
    infra.https_ready = True
    infra.save()
    # B1's live check discovers the shared group ALB via Environment.alb_dns, matched
    # against the mock's own deterministic DescribeLoadBalancers response (see
    # MockClient.describe_load_balancers) — the base `application` fixture's fixed
    # "alb.example.com" deliberately does NOT match anything real, so the live check
    # would (correctly) fail closed. Only the TLS-ready fixture needs the real match.
    Environment.objects.filter(infrastructure=infra).update(
        alb_dns=mock_eks_alb_dns(str(infra.id), "us-west-2"),
    )
    return application


@pytest.mark.django_db
def test_eks_deploy_adds_a_host_rule_additively_when_infra_is_tls_ready(tls_ready_application, deploy):
    """B1 fix: the host rule lives in a SEPARATE Ingress (`{slug}-host`), scoped to
    HTTPS:443 only via a listen-ports annotation, never as a second rule on the path
    Ingress — a single shared Ingress could not be scoped to different listener ports
    per rule."""
    deploy(tls_ready_application)

    path_ingress = _object_body(tls_ready_application, "ingress", "myapp")
    hostname = "myapp.0123456789abcdef.launchpad.aklamaash.me"

    # Path Ingress is untouched — host URLs are additive, never a migration.
    assert [p.path for p in path_ingress.spec.rules[0].http.paths] == ["/myapp", "/myapp/*"]
    assert path_ingress.spec.rules[0].host is None
    assert len(path_ingress.spec.rules) == 1

    host_ingress = _object_body(tls_ready_application, "ingress", "myapp-host")
    assert host_ingress.metadata.annotations["alb.ingress.kubernetes.io/listen-ports"] == '[{"HTTPS": 443}]'
    assert len(host_ingress.spec.rules) == 1
    host_rule = host_ingress.spec.rules[0]
    assert host_rule.host == hostname
    assert [p.path for p in host_rule.http.paths] == ["/"]

    tls_ready_application.refresh_from_db()
    assert tls_ready_application.host_route_applied is True
    assert tls_ready_application.runtime_refs["host_ingress"] == "myapp-host"


@pytest.mark.django_db
def test_eks_deploy_moves_health_check_and_readiness_probe_in_lockstep(tls_ready_application, deploy):
    from aws.container_config import HOST_MODE_HEALTH_CHECK_PATH

    deploy(tls_ready_application)
    objects = _objects(tls_ready_application)

    ingress = objects[("ingress", "app-myapp", "myapp")]
    assert ingress.metadata.annotations["alb.ingress.kubernetes.io/healthcheck-path"] == HOST_MODE_HEALTH_CHECK_PATH

    pod = objects[("deployment", "app-myapp", "myapp")].spec.template.spec
    _app_container, nginx = pod.containers
    assert nginx.readiness_probe.http_get.path == HOST_MODE_HEALTH_CHECK_PATH

    nginx_conf = objects[("configmap", "app-myapp", "myapp-nginx")].data["nginx.conf"]
    assert "server_name myapp.0123456789abcdef.launchpad.aklamaash.me;" in nginx_conf
    assert HOST_MODE_HEALTH_CHECK_PATH in nginx_conf


@pytest.mark.django_db
def test_eks_host_mode_disabled_flag_stays_path_only_even_when_fully_tls_ready(tls_ready_application, deploy, settings):
    """B1: EKS_HOST_MODE_ENABLED gates host mode ahead of every other check. Even an infra
    that is otherwise fully live-ready (the same fixture that gets a host Ingress in
    test_eks_deploy_adds_a_host_rule_additively_when_infra_is_tls_ready) must stay in path
    mode with the flag off, with no host Ingress created at all and a reason on record."""
    from api.k8s.deployer import EKSDeployer
    from api.models.environment import Environment

    settings.EKS_HOST_MODE_ENABLED = False

    infra = tls_ready_application.infrastructure
    env = Environment.objects.get(infrastructure=infra)
    session = MockSession(region="us-west-2", account_id=ACCOUNT_ID, infra_id=str(infra.id))
    deployer = EKSDeployer(session, tls_ready_application, env)
    assert deployer.host_mode is False
    assert deployer.host_reason == "eks_host_mode_disabled"

    deploy(tls_ready_application)

    path_ingress = _object_body(tls_ready_application, "ingress", "myapp")
    assert len(path_ingress.spec.rules) == 1
    assert path_ingress.spec.rules[0].host is None
    assert ("ingress", "app-myapp", "myapp-host") not in _objects(tls_ready_application)
    tls_ready_application.refresh_from_db()
    assert tls_ready_application.host_route_applied is False


@pytest.mark.django_db
def test_eks_deploy_without_tls_ready_stays_path_only(application, deploy):
    """The existing golden path-mode assertions (test_pod_spec_hardening_and_ingress_paths)
    already pin this — this test documents WHY: an infra whose read-model mirror hasn't
    caught up to ISSUED/synced/https_ready yet must never get a host rule."""
    deploy(application)

    ingress = _object_body(application, "ingress", "myapp")
    assert len(ingress.spec.rules) == 1
    assert ingress.spec.rules[0].host is None
    application.refresh_from_db()
    assert application.host_route_applied is False


# ── B1: EKS host mode requires a live AWS check, not just the mirrored https_ready flag ──

@pytest.mark.django_db
def test_eks_host_mode_never_mutates_the_customer_alb(tls_ready_application, deploy, monkeypatch):
    """B1's final shape: host mode is achieved entirely through controller-owned objects —
    the host Ingress's own `listen-ports: [{"HTTPS": 443}]` annotation — never through an
    out-of-band boto3 rule on a listener the AWS Load Balancer Controller reconciles on its
    own. No elbv2 mutating call (create_rule/delete_rule/set_rule_priorities) may happen
    anywhere in the EKS deploy path; only the read-only DescribeLoadBalancers/
    DescribeListeners calls the B1 live check makes are allowed."""
    from api.mock.mock_session import MockClient

    mutating_calls = []
    for method in ("create_rule", "delete_rule", "set_rule_priorities"):
        original = getattr(MockClient, method)

        def _spy(self, *args, _name=method, _original=original, **kwargs):
            mutating_calls.append(_name)
            return _original(self, *args, **kwargs)

        monkeypatch.setattr(MockClient, method, _spy)

    deploy(tls_ready_application)

    assert mutating_calls == []
    host_ingress = _object_body(tls_ready_application, "ingress", "myapp-host")
    assert host_ingress.metadata.annotations["alb.ingress.kubernetes.io/listen-ports"] == '[{"HTTPS": 443}]'
    tls_ready_application.refresh_from_db()
    assert tls_ready_application.host_route_applied is True


@pytest.mark.django_db
def test_eks_host_mode_fails_closed_when_the_group_alb_cannot_be_found(application, settings, deploy):
    """The live-check-failure path: dns_label/tls_status/dns_synced/https_ready are all
    live-ready, but Environment.alb_dns (the base `application` fixture's fixed
    "alb.example.com") doesn't match anything DescribeLoadBalancers returns. The B1 LOW
    fix moved this check out of `__init__` into `deploy()`, so `host_mode` is the DB-only
    *candidate* state right after construction — it is deploy() that must downgrade it and
    the deploy must still succeed, in path mode, with no host Ingress ever created."""
    settings.PLATFORM_BASE_DOMAIN = "launchpad.aklamaash.me"
    infra = application.infrastructure
    infra.dns_label = "0123456789abcdef"
    infra.tls_status = "ISSUED"
    infra.dns_synced = True
    infra.https_ready = True
    infra.save()

    from api.k8s.deployer import EKSDeployer
    from api.models.environment import Environment

    env = Environment.objects.get(infrastructure=infra)
    session = MockSession(region="us-west-2", account_id=ACCOUNT_ID, infra_id=str(infra.id))
    deployer = EKSDeployer(session, application, env)
    assert deployer.host_mode is True

    live_ready, live_reason = deployer._verify_eks_https_listener()
    assert live_ready is False
    assert live_reason == "eks_alb_live_check_failed"

    deploy(application)
    application.refresh_from_db()
    assert application.host_route_applied is False
    assert ("ingress", "app-myapp", "myapp-host") not in _objects(application)


@pytest.mark.django_db
def test_eks_host_mode_fails_closed_without_an_alb_dns_at_all(application, settings):
    """alb_dns unset makes Environment fail _validate_infrastructure's own required-fields
    check well before EKSDeployer is even reached (an EKS environment cannot deploy at all
    without it, host mode or not) — so this only exercises EKSDeployer/the live check
    directly, unlike the sibling fails_closed tests that also assert the full deploy()."""
    settings.PLATFORM_BASE_DOMAIN = "launchpad.aklamaash.me"
    infra = application.infrastructure
    infra.dns_label = "0123456789abcdef"
    infra.tls_status = "ISSUED"
    infra.dns_synced = True
    infra.https_ready = True
    infra.save()

    from api.k8s.deployer import EKSDeployer
    from api.models.environment import Environment

    Environment.objects.filter(infrastructure=infra).update(alb_dns=None)
    env = Environment.objects.get(infrastructure=infra)
    session = MockSession(region="us-west-2", account_id=ACCOUNT_ID, infra_id=str(infra.id))
    deployer = EKSDeployer(session, application, env)
    assert deployer.host_mode is True

    live_ready, live_reason = deployer._verify_eks_https_listener()
    assert live_ready is False
    assert live_reason == "eks_alb_not_discovered"


@pytest.mark.django_db
def test_eks_host_mode_fails_closed_when_https_listener_is_missing(tls_ready_application, monkeypatch, deploy):
    """The group ALB is found, but the :443 listener the IngressClassParams listenPorts
    patch was supposed to expose never actually reconciled onto it (B1's REAL-AWS
    unverified-schema concern) — host mode must not be granted even though the ALB itself
    was discovered successfully."""
    from api.k8s import deployer as deployer_mod
    from api.models.environment import Environment

    infra = tls_ready_application.infrastructure
    env = Environment.objects.get(infrastructure=infra)
    session = MockSession(region="us-west-2", account_id=ACCOUNT_ID, infra_id=str(infra.id))

    real_get_listener_arn = deployer_mod.ALBClient.get_listener_arn

    def _no_https(self, alb_arn, port=80):
        if port == 443:
            return None
        return real_get_listener_arn(self, alb_arn, port=port)

    monkeypatch.setattr(deployer_mod.ALBClient, "get_listener_arn", _no_https)

    deployer = deployer_mod.EKSDeployer(session, tls_ready_application, env)
    assert deployer.host_mode is True

    live_ready, live_reason = deployer._verify_eks_https_listener()
    assert live_ready is False
    assert live_reason == "eks_https_listener_not_applied"

    deploy(tls_ready_application)
    tls_ready_application.refresh_from_db()
    assert tls_ready_application.host_route_applied is False
    assert ("ingress", "app-myapp", "myapp-host") not in _objects(tls_ready_application)


@pytest.mark.django_db
def test_pod_template_carries_the_nginx_config_hash(application, deploy):
    """nginx.conf is a subPath mount: a patched ConfigMap alone never reaches running pods.
    The hash in the pod template is what rolls them when (and only when) the config changes."""
    import hashlib

    from api.k8s.deployer import NGINX_CONFIG_HASH_ANNOTATION

    deploy(application)
    objects = _objects(application)

    template = objects[("deployment", "app-myapp", "myapp")].spec.template
    config = objects[("configmap", "app-myapp", "myapp-nginx")].data["nginx.conf"]
    assert template.metadata.annotations[NGINX_CONFIG_HASH_ANNOTATION] == hashlib.sha256(config.encode()).hexdigest()


def _deployment(generation, observed, replicas, updated, available):
    from kubernetes import client as k8s

    return k8s.V1Deployment(
        metadata=k8s.V1ObjectMeta(generation=generation),
        spec=k8s.V1DeploymentSpec(replicas=1, selector=k8s.V1LabelSelector(), template=k8s.V1PodTemplateSpec()),
        status=k8s.V1DeploymentStatus(
            observed_generation=observed, replicas=replicas, updated_replicas=updated, available_replicas=available,
        ),
    )


@pytest.mark.parametrize("state,complete", [
    # e2e-kube incident: right after the patch the old pod is available, but the controller
    # hasn't observed the new generation — the old wait returned here.
    ((2, 1, 1, 1, 1), False),
    # Mid-roll: new pod up, old pod still around.
    ((2, 2, 2, 1, 1), False),
    # New pod created but not yet available.
    ((2, 2, 1, 1, 0), False),
    ((2, 2, 1, 1, 1), True),
])
def test_rollout_complete_waits_for_the_new_generation_to_replace_the_old(state, complete):
    from api.k8s.deployer import _rollout_complete

    assert _rollout_complete(_deployment(*state)) is complete

