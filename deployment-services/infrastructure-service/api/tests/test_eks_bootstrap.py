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
    custom = MagicMock()
    custom.get_cluster_custom_object.return_value = {"spec": {}}
    monkeypatch.setattr(eb.k8s, "CoreV1Api", lambda api: core)
    monkeypatch.setattr(eb.k8s, "CustomObjectsApi", lambda api: custom)

    lines = []
    eb._enable_network_policy_enforcement(object(), lines)

    core.create_namespaced_config_map.assert_called_once()
    namespace, config_map = core.create_namespaced_config_map.call_args.args
    assert namespace == "kube-system"
    assert config_map.data == {"enable-network-policy-controller": "true"}
    custom.patch_cluster_custom_object.assert_called_once_with(
        "eks.amazonaws.com", "v1", "nodeclasses", "default",
        {"spec": {"networkPolicy": "DefaultAllow"}},
    )


def test_enable_network_policy_is_idempotent_when_already_enabled(monkeypatch):
    core = MagicMock()
    core.read_namespaced_config_map.return_value = SimpleNamespace(
        data={"enable-network-policy-controller": "true"}
    )
    custom = MagicMock()
    custom.get_cluster_custom_object.return_value = {"spec": {"networkPolicy": "DefaultAllow"}}
    monkeypatch.setattr(eb.k8s, "CoreV1Api", lambda api: core)
    monkeypatch.setattr(eb.k8s, "CustomObjectsApi", lambda api: custom)

    eb._enable_network_policy_enforcement(object(), [])

    core.patch_namespaced_config_map.assert_not_called()
    core.create_namespaced_config_map.assert_not_called()
    custom.patch_cluster_custom_object.assert_not_called()


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
