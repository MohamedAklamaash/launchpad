"""Bootstrap tests: get-or-create idempotency, network-policy enablement (C3),
ALB hostname poll success and timeout-to-distinct-error."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from api.services import eks_bootstrap as eb
from kubernetes.client.rest import ApiException


def _conflict():
    return ApiException(status=409, reason="Conflict")


def _not_found():
    return ApiException(status=404, reason="Not Found")


def test_get_or_create_tolerates_conflict():
    lines = []
    eb._get_or_create(MagicMock(side_effect=_conflict()), "Namespace/x", lines)
    assert lines == ["[k8s] Namespace/x already exists"]


def test_get_or_create_raises_other_errors():
    with pytest.raises(ApiException):
        eb._get_or_create(MagicMock(side_effect=ApiException(status=500)), "Namespace/x", [])


def test_enable_network_policy_creates_missing_config_map(monkeypatch):
    core = MagicMock()
    core.read_namespaced_config_map.side_effect = _not_found()
    monkeypatch.setattr(eb.k8s, "CoreV1Api", lambda api: core)

    lines = []
    eb._enable_network_policy_enforcement(object(), lines)

    core.create_namespaced_config_map.assert_called_once()
    namespace, config_map = core.create_namespaced_config_map.call_args.args
    assert namespace == "kube-system"
    assert config_map.data == {"enable-network-policy-controller": "true"}


def test_enable_network_policy_is_idempotent_when_already_enabled(monkeypatch):
    core = MagicMock()
    core.read_namespaced_config_map.return_value = SimpleNamespace(
        data={"enable-network-policy-controller": "true"}
    )
    monkeypatch.setattr(eb.k8s, "CoreV1Api", lambda api: core)

    eb._enable_network_policy_enforcement(object(), [])

    core.patch_namespaced_config_map.assert_not_called()
    core.create_namespaced_config_map.assert_not_called()


def test_ensure_namespace_creator_rbac_grants_exactly_get_and_create_on_namespaces(monkeypatch):
    """The deploy role's access entry is namespace-scoped to app-* (infra/aws/modules/eks/
    main.tf) and namespace creation is cluster-scoped, so this ClusterRole is the only thing
    letting the deploy role create its own app-{slug} namespace. It must not grant anything
    else: no update/delete/list/patch, no other resource. Creating a namespace is the only
    cluster-scoped action the deploy role legitimately needs."""
    rbac = MagicMock()
    monkeypatch.setattr(eb.k8s, "RbacAuthorizationV1Api", lambda api: rbac)

    eb._ensure_namespace_creator_rbac(object(), [])

    (cluster_role,) = rbac.create_cluster_role.call_args.args
    assert cluster_role.metadata.name == eb.NAMESPACE_CREATOR_CLUSTER_ROLE
    assert len(cluster_role.rules) == 1
    rule = cluster_role.rules[0]
    assert rule.api_groups == [""]
    assert rule.resources == ["namespaces"]
    assert set(rule.verbs) == {"get", "create"}


def test_ensure_namespace_creator_rbac_binds_the_deployer_group(monkeypatch):
    rbac = MagicMock()
    monkeypatch.setattr(eb.k8s, "RbacAuthorizationV1Api", lambda api: rbac)

    eb._ensure_namespace_creator_rbac(object(), [])

    (binding,) = rbac.create_cluster_role_binding.call_args.args
    assert binding.metadata.name == eb.NAMESPACE_CREATOR_CLUSTER_ROLE
    assert binding.role_ref.kind == "ClusterRole"
    assert binding.role_ref.name == eb.NAMESPACE_CREATOR_CLUSTER_ROLE
    (subject,) = binding.subjects
    assert subject.kind == "Group"
    assert subject.name == eb.DEPLOYER_GROUP


def test_ensure_namespace_creator_rbac_is_idempotent_on_rerun(monkeypatch):
    rbac = MagicMock()
    rbac.create_cluster_role.side_effect = _conflict()
    rbac.create_cluster_role_binding.side_effect = _conflict()
    monkeypatch.setattr(eb.k8s, "RbacAuthorizationV1Api", lambda api: rbac)

    lines = []
    eb._ensure_namespace_creator_rbac(object(), lines)

    assert lines == [
        f"[k8s] ClusterRole/{eb.NAMESPACE_CREATOR_CLUSTER_ROLE} already exists",
        f"[k8s] ClusterRoleBinding/{eb.NAMESPACE_CREATOR_CLUSTER_ROLE} already exists",
    ]


def test_ensure_bootstrap_ingress_is_rerun_safe(monkeypatch):
    core = MagicMock()
    core.create_namespace.side_effect = _conflict()
    networking = MagicMock()
    networking.create_namespaced_ingress.side_effect = _conflict()
    monkeypatch.setattr(eb.k8s, "CoreV1Api", lambda api: core)
    monkeypatch.setattr(eb.k8s, "NetworkingV1Api", lambda api: networking)

    lines = []
    eb._ensure_bootstrap_ingress(object(), lines)
    assert all("already exists" in line for line in lines)


def test_ensure_bootstrap_ingress_never_creates_an_empty_backend_service(monkeypatch):
    """H6: the old empty-backend Service (no pods ever behind it) is what made unmatched
    traffic hit a target group with zero healthy targets and answer 503. The fixed-response
    action needs no Service at all."""
    core = MagicMock()
    networking = MagicMock()
    monkeypatch.setattr(eb.k8s, "CoreV1Api", lambda api: core)
    monkeypatch.setattr(eb.k8s, "NetworkingV1Api", lambda api: networking)

    eb._ensure_bootstrap_ingress(object(), [])

    core.create_namespaced_service.assert_not_called()


def test_ensure_bootstrap_ingress_declares_a_fixed_404_default_action(monkeypatch):
    """H6: unmatched :443/:80 traffic on the shared ALB group must get a clean 404, not the
    503 an empty-backend Service produced — see plan/H-hardening.md's H6 section."""
    core = MagicMock()
    networking = MagicMock()
    monkeypatch.setattr(eb.k8s, "CoreV1Api", lambda api: core)
    monkeypatch.setattr(eb.k8s, "NetworkingV1Api", lambda api: networking)

    eb._ensure_bootstrap_ingress(object(), [])

    _namespace, ingress = networking.create_namespaced_ingress.call_args.args
    action_annotation = ingress.metadata.annotations[
        f"alb.ingress.kubernetes.io/actions.{eb.DEFAULT_BACKEND_SERVICE}"
    ]
    assert '"type":"fixed-response"' in action_annotation
    assert '"statusCode":"404"' in action_annotation
    backend_port = ingress.spec.default_backend.service.port
    assert backend_port.name == "use-annotation"
    assert backend_port.number is None


def test_ensure_bootstrap_ingress_declares_only_http_80_at_creation(monkeypatch):
    """No certificate exists yet at bootstrap time (this runs before any infra has ever
    requested one), and an ALB HTTPS listener cannot be created without one — a hard
    CreateListener constraint, not a controller choice. Declaring HTTPS:443 here would make
    the controller fail to resolve a certificate for the group and never write an ALB
    hostname onto this Ingress's status, timing out bootstrap for every new EKS cluster
    (a real regression caught and reverted during the B1 security review's second round).
    HTTPS:443 is added later, only once a certificate actually exists — see
    apply_eks_tls."""
    core = MagicMock()
    networking = MagicMock()
    monkeypatch.setattr(eb.k8s, "CoreV1Api", lambda api: core)
    monkeypatch.setattr(eb.k8s, "NetworkingV1Api", lambda api: networking)

    eb._ensure_bootstrap_ingress(object(), [])

    _namespace, ingress = networking.create_namespaced_ingress.call_args.args
    assert ingress.metadata.annotations["alb.ingress.kubernetes.io/listen-ports"] == '[{"HTTP": 80}]'


def _ingress_with_hostname(hostname):
    entry = SimpleNamespace(hostname=hostname)
    load_balancer = SimpleNamespace(ingress=[entry] if hostname else [])
    return SimpleNamespace(status=SimpleNamespace(load_balancer=load_balancer))


def test_wait_for_alb_hostname_returns_hostname(monkeypatch):
    networking = MagicMock()
    networking.read_namespaced_ingress.side_effect = [
        _ingress_with_hostname(None),
        _ingress_with_hostname("k8s-abc.us-east-1.elb.amazonaws.com"),
    ]
    monkeypatch.setattr(eb.k8s, "NetworkingV1Api", lambda api: networking)
    monkeypatch.setattr(eb.time, "sleep", lambda *_: None)

    assert eb._wait_for_alb_hostname(object(), []) == "k8s-abc.us-east-1.elb.amazonaws.com"


def test_wait_for_alb_hostname_timeout_raises_distinct_marker(monkeypatch):
    networking = MagicMock()
    networking.read_namespaced_ingress.return_value = _ingress_with_hostname(None)
    monkeypatch.setattr(eb.k8s, "NetworkingV1Api", lambda api: networking)
    monkeypatch.setattr(eb.time, "sleep", lambda *_: None)
    clock = iter(range(0, 10_000, 100))
    monkeypatch.setattr(eb.time, "monotonic", lambda: next(clock))

    with pytest.raises(eb.EksBootstrapTimeout) as exc_info:
        eb._wait_for_alb_hostname(object(), [])

    assert eb.ALB_TIMEOUT_MARKER in str(exc_info.value)
    assert eb.ALB_TIMEOUT_MARKER in exc_info.value.logs
    # The marker must not trip the transient-retry substring match.
    from api.services.terraform_worker import TerraformWorker
    assert not TerraformWorker._is_transient_error(eb.ALB_TIMEOUT_MARKER)


def test_bootstrap_refuses_dev_or_mock(monkeypatch):
    monkeypatch.setattr(eb, "is_dev_mode", lambda mode: True)
    with pytest.raises(eb.EksBootstrapError, match="dev/mock"):
        eb.bootstrap_eks_environment(
            SimpleNamespace(id="x", is_mock=False),
            credentials={}, region="us-east-1", cluster_name="infra-x",
        )


def test_bootstrap_refuses_a_mock_infra_outside_dev_mode(monkeypatch):
    """The dev-mode case above short-circuits before is_mock is ever read, so it cannot
    catch a regression that lets a mock infrastructure bootstrap against real AWS."""
    monkeypatch.setattr(eb, "is_dev_mode", lambda mode: False)
    with pytest.raises(eb.EksBootstrapError, match="dev/mock"):
        eb.bootstrap_eks_environment(
            SimpleNamespace(id="x", is_mock=True),
            credentials={}, region="us-east-1", cluster_name="infra-x",
        )


# ── F1b part 2: ALB Auto Mode group name uses dns_label, never a UUIDv7 prefix ──────────

def test_ingress_group_name_uses_dns_label():
    infra = SimpleNamespace(id="11111111-2222-3333-4444-555555555555", dns_label="a1b2c3d4e5f6a7b8")
    assert eb._ingress_group_name(infra) == "launchpad-a1b2c3d4e5f6a7b8"


def test_ingress_group_name_falls_back_to_id_prefix_without_a_dns_label():
    infra = SimpleNamespace(id="11111111-2222-3333-4444-555555555555", dns_label=None)
    assert eb._ingress_group_name(infra) == "launchpad-11111111"


def test_ingress_group_name_never_uses_the_id_prefix_when_a_label_exists():
    """The regression this replaces: str(infra.id)[:8] repeats every ~65s platform-wide
    (UUIDv7's leading 48 bits are a millisecond timestamp) and is forceable from the row's
    own created_at — dns_label is independent random entropy instead (CLAUDE.md)."""
    infra = SimpleNamespace(id="11111111-2222-3333-4444-555555555555", dns_label="a1b2c3d4e5f6a7b8")
    group_name = eb._ingress_group_name(infra)
    assert str(infra.id)[:8] not in group_name


def test_bootstrap_ensures_ingress_class_with_the_dns_label_group_name(monkeypatch):
    """Existing clusters are unaffected: _ensure_ingress_class only creates the
    IngressClassParams object once (_get_or_create swallows 409, never updates it), so
    this only changes what a *first-time* bootstrap requests, never an existing cluster's
    already-recorded group."""
    infra = SimpleNamespace(id="11111111-2222-3333-4444-555555555555", dns_label="a1b2c3d4e5f6a7b8", is_mock=False)
    monkeypatch.setattr(eb, "is_dev_mode", lambda mode: False)

    session = MagicMock()
    session.client.return_value.describe_cluster.return_value = {
        "cluster": {"endpoint": "https://x", "certificateAuthority": {"data": "ca"}}
    }
    monkeypatch.setattr(eb, "_boto_session", lambda credentials, region: session)
    monkeypatch.setattr(eb, "mint_eks_token", lambda *a, **k: "token")

    api_cm = MagicMock()
    monkeypatch.setattr(eb, "k8s_api_client", lambda *a, **k: api_cm)
    api_cm.__enter__.return_value = object()
    api_cm.__exit__.return_value = False

    calls = {}

    def _fake_ensure_ingress_class(api, group_name, lines):
        calls["group_name"] = group_name

    monkeypatch.setattr(eb, "_ensure_ingress_class", _fake_ensure_ingress_class)
    monkeypatch.setattr(eb, "_enable_network_policy_enforcement", lambda api, lines: None)
    monkeypatch.setattr(eb, "_ensure_namespace_creator_rbac", lambda api, lines: None)
    monkeypatch.setattr(eb, "_ensure_bootstrap_ingress", lambda api, lines: None)
    monkeypatch.setattr(eb, "_wait_for_alb_hostname", lambda api, lines: "alb.example.com")

    eb.bootstrap_eks_environment(infra, credentials={}, region="us-east-1", cluster_name="infra-x")

    assert calls["group_name"] == "launchpad-a1b2c3d4e5f6a7b8"


def test_bootstrap_wires_up_the_namespace_creator_rbac(monkeypatch):
    """Without this step the deploy role can never create its own app-{slug} namespace on a
    freshly provisioned cluster: the real bug this fix addresses."""
    infra = SimpleNamespace(id="x", dns_label="a1b2c3d4e5f6a7b8", is_mock=False)
    monkeypatch.setattr(eb, "is_dev_mode", lambda mode: False)

    session = MagicMock()
    session.client.return_value.describe_cluster.return_value = {
        "cluster": {"endpoint": "https://x", "certificateAuthority": {"data": "ca"}}
    }
    monkeypatch.setattr(eb, "_boto_session", lambda credentials, region: session)
    monkeypatch.setattr(eb, "mint_eks_token", lambda *a, **k: "token")

    api_cm = MagicMock()
    monkeypatch.setattr(eb, "k8s_api_client", lambda *a, **k: api_cm)
    api_cm.__enter__.return_value = object()
    api_cm.__exit__.return_value = False

    rbac_calls = []
    monkeypatch.setattr(eb, "_ensure_namespace_creator_rbac", lambda api, lines: rbac_calls.append(1))
    monkeypatch.setattr(eb, "_ensure_ingress_class", lambda api, group_name, lines: None)
    monkeypatch.setattr(eb, "_enable_network_policy_enforcement", lambda api, lines: None)
    monkeypatch.setattr(eb, "_ensure_bootstrap_ingress", lambda api, lines: None)
    monkeypatch.setattr(eb, "_wait_for_alb_hostname", lambda api, lines: "alb.example.com")

    eb.bootstrap_eks_environment(infra, credentials={}, region="us-east-1", cluster_name="infra-x")

    assert rbac_calls == [1]


# ── F1b part 3a: apply_eks_tls ──────────────────────────────────────────────────────────

def test_apply_eks_tls_refuses_mock_infra_outside_dev_mode():
    from api.services.platform_dns.route53_client import MockRealMismatch

    with pytest.raises(MockRealMismatch):
        eb.apply_eks_tls(
            SimpleNamespace(id="x"), credentials={}, region="us-east-1", cluster_name="c",
            cert_arn="arn:aws:acm:us-east-1:1:certificate/x", infra_is_mock=True, dev_mode=False,
        )


def test_apply_eks_tls_refuses_real_infra_inside_dev_mode():
    from api.services.platform_dns.route53_client import MockRealMismatch

    with pytest.raises(MockRealMismatch):
        eb.apply_eks_tls(
            SimpleNamespace(id="x"), credentials={}, region="us-east-1", cluster_name="c",
            cert_arn="arn:aws:acm:us-east-1:1:certificate/x", infra_is_mock=False, dev_mode=True,
        )


def test_apply_eks_tls_dev_mode_skips_k8s_and_returns_true(monkeypatch):
    """Mock end-to-end: there is no real cluster to patch (bootstrap_eks_environment already
    refuses to run against one), so a mock/dev caller gets True with no k8s API traffic."""
    session = MagicMock()
    monkeypatch.setattr(eb, "_boto_session", lambda credentials, region: session)

    result = eb.apply_eks_tls(
        SimpleNamespace(id="x"), credentials={}, region="us-east-1", cluster_name="c",
        cert_arn="arn:aws:acm:us-east-1:1:certificate/x", infra_is_mock=True, dev_mode=True,
    )

    assert result is True
    session.client.assert_not_called()


def test_apply_eks_tls_patches_the_cert_onto_the_class_and_443_onto_the_bootstrap_ingress(monkeypatch):
    infra = SimpleNamespace(id="x", is_mock=False)
    session = MagicMock()
    session.client.return_value.describe_cluster.return_value = {
        "cluster": {"endpoint": "https://x", "certificateAuthority": {"data": "ca"}}
    }
    monkeypatch.setattr(eb, "_boto_session", lambda credentials, region: session)
    monkeypatch.setattr(eb, "mint_eks_token", lambda *a, **k: "token")

    api_cm = MagicMock()
    monkeypatch.setattr(eb, "k8s_api_client", lambda *a, **k: api_cm)
    api_cm.__enter__.return_value = object()
    api_cm.__exit__.return_value = False

    manager = MagicMock()
    monkeypatch.setattr(eb.k8s, "CustomObjectsApi", lambda api: manager.custom)
    monkeypatch.setattr(eb.k8s, "NetworkingV1Api", lambda api: manager.networking)

    cert_arn = "arn:aws:acm:us-east-1:1:certificate/abc"
    result = eb.apply_eks_tls(
        infra, credentials={}, region="us-east-1", cluster_name="infra-x",
        cert_arn=cert_arn, infra_is_mock=False, dev_mode=False,
    )

    assert result is True
    # B1 (security review, second round): listenPorts is deliberately NOT patched onto the
    # shared class — the AWS Load Balancer Controller documents class-level
    # IngressClassParams fields as overriding the equivalent per-Ingress annotation, so
    # setting it here would silently widen every Ingress (including the host-only one
    # scoped to HTTPS:443) back onto both ports.
    manager.custom.patch_cluster_custom_object.assert_called_once_with(
        "eks.amazonaws.com", "v1", "ingressclassparams", eb.INGRESS_CLASS_NAME,
        {"spec": {"certificateARNs": [cert_arn]}},
    )
    # HTTPS:443 is added to the bootstrap Ingress here — the first point a certificate is
    # known to exist — not at _ensure_bootstrap_ingress time (no cert exists there yet, and
    # an ALB HTTPS listener cannot be created without one).
    manager.networking.patch_namespaced_ingress.assert_called_once_with(
        "bootstrap", eb.BOOTSTRAP_NAMESPACE,
        {"metadata": {"annotations": {
            "alb.ingress.kubernetes.io/listen-ports": '[{"HTTP": 80}, {"HTTPS": 443}]',
        }}},
    )
    # The certificate patch must land before the Ingress patch that triggers the
    # controller's reconcile of it, or the reconcile could run against a class with no
    # resolvable certificate yet.
    call_order = [name for name, _args, _kwargs in manager.mock_calls]
    assert call_order.index("custom.patch_cluster_custom_object") < call_order.index(
        "networking.patch_namespaced_ingress"
    )
