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


def test_ensure_bootstrap_ingress_is_rerun_safe(monkeypatch):
    core = MagicMock()
    core.create_namespace.side_effect = _conflict()
    core.create_namespaced_service.side_effect = _conflict()
    networking = MagicMock()
    networking.create_namespaced_ingress.side_effect = _conflict()
    monkeypatch.setattr(eb.k8s, "CoreV1Api", lambda api: core)
    monkeypatch.setattr(eb.k8s, "NetworkingV1Api", lambda api: networking)

    lines = []
    eb._ensure_bootstrap_ingress(object(), lines)
    assert all("already exists" in line for line in lines)


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
    monkeypatch.setattr(eb, "_ensure_bootstrap_ingress", lambda api, lines: None)
    monkeypatch.setattr(eb, "_wait_for_alb_hostname", lambda api, lines: "alb.example.com")

    eb.bootstrap_eks_environment(infra, credentials={}, region="us-east-1", cluster_name="infra-x")

    assert calls["group_name"] == "launchpad-a1b2c3d4e5f6a7b8"
