"""run_worker.py:_apply_eks_tls_for_issued_certs — the periodic EKS IngressClassParams TLS
patch sweep, and R3's security-review fixes: a cert re-issue must trigger a re-patch even
after eks_ingress_tls_ready was already True, a persistently failing patch must eventually
give up rather than retry forever, and teardown must clear all of this state.
"""
import time as time_module
import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from api.management.commands.run_worker import (
    EKS_TLS_PATCH_TIMEOUT,
    _apply_eks_tls_for_issued_certs,
)
from api.models.environment import Environment
from api.models.infrastructure_certificate import InfrastructureCertificate
from django.utils import timezone
from shared.enums.orchestrator import ComputeType


@pytest.fixture
def make_eks_infra(db):
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    def _make(**env_overrides):
        user = User.objects.create(
            id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@e.io", user_name="u", role="super_admin"
        )
        infra = Infrastructure.objects.create(
            user=user, name=f"i-{uuid.uuid4()}", cloud_provider="aws",
            max_cpu=1.0, max_memory=2.0, code="123456789012", compute_type=ComputeType.EKS,
            is_cloud_authenticated=True, metadata={"aws_region": "us-east-1"}, policy_version=4,
        )
        infra.mint_dns_label()
        defaults = {
            "status": "ACTIVE", "alb_dns": "x.us-east-1.elb.amazonaws.com",
            "cluster_arn": "arn:aws:eks:us-east-1:123456789012:cluster/infra-x",
        }
        defaults.update(env_overrides)
        env = Environment.objects.create(infrastructure=infra, **defaults)
        return infra, env

    return _make


def _issued_cert(infra, cert_arn="arn:aws:acm:us-east-1:123456789012:certificate/abc"):
    return InfrastructureCertificate.objects.create(
        infrastructure=infra, cert_arn=cert_arn,
        tls_status=InfrastructureCertificate.TLS_ISSUED, tls_requested_at=timezone.now(),
    )


_NO_DEADLINE = float("inf")


def test_patches_and_marks_ready_on_first_success(make_eks_infra):
    infra, env = make_eks_infra()
    cert = _issued_cert(infra)

    with patch("api.cloud_providers.aws.authenticate.assume_role_credentials_only", return_value={}), \
            patch("api.services.eks_bootstrap.apply_eks_tls", return_value=True) as apply_tls, \
            patch("api.services.host_readiness.publish_host_readiness"):
        _apply_eks_tls_for_issued_certs(_NO_DEADLINE, dev_mode=False)

    apply_tls.assert_called_once()
    env.refresh_from_db()
    assert env.eks_ingress_tls_ready is True
    assert env.eks_ingress_tls_cert_arn == cert.cert_arn
    assert env.eks_ingress_tls_patch_attempted_at is None


def test_already_patched_for_the_current_cert_is_a_noop(make_eks_infra):
    infra, _env = make_eks_infra(
        eks_ingress_tls_ready=True,
        eks_ingress_tls_cert_arn="arn:aws:acm:us-east-1:123456789012:certificate/abc",
    )
    _issued_cert(infra, cert_arn="arn:aws:acm:us-east-1:123456789012:certificate/abc")

    with patch("api.services.eks_bootstrap.apply_eks_tls") as apply_tls:
        _apply_eks_tls_for_issued_certs(_NO_DEADLINE, dev_mode=False)

    apply_tls.assert_not_called()


# ── R3: a cert re-issue must trigger a re-patch even though ready was already True ───────

def test_cert_reissue_triggers_a_repatch_even_though_ready_was_true(make_eks_infra):
    infra, env = make_eks_infra(
        eks_ingress_tls_ready=True,
        eks_ingress_tls_cert_arn="arn:aws:acm:us-east-1:123456789012:certificate/old",
    )
    new_cert = _issued_cert(infra, cert_arn="arn:aws:acm:us-east-1:123456789012:certificate/new")

    with patch("api.cloud_providers.aws.authenticate.assume_role_credentials_only", return_value={}), \
            patch("api.services.eks_bootstrap.apply_eks_tls", return_value=True) as apply_tls, \
            patch("api.services.host_readiness.publish_host_readiness"):
        _apply_eks_tls_for_issued_certs(_NO_DEADLINE, dev_mode=False)

    apply_tls.assert_called_once()
    assert apply_tls.call_args.kwargs["cert_arn"] == new_cert.cert_arn
    env.refresh_from_db()
    assert env.eks_ingress_tls_ready is True
    assert env.eks_ingress_tls_cert_arn == new_cert.cert_arn


def test_cert_reissue_drops_ready_flag_immediately_even_before_the_patch_attempt_runs(make_eks_infra):
    """If the patch attempt itself fails, the row must not be left claiming
    eks_ingress_tls_ready=True for a certificate that has already been superseded."""
    infra, env = make_eks_infra(
        eks_ingress_tls_ready=True,
        eks_ingress_tls_cert_arn="arn:aws:acm:us-east-1:123456789012:certificate/old",
    )
    _issued_cert(infra, cert_arn="arn:aws:acm:us-east-1:123456789012:certificate/new")

    with patch("api.cloud_providers.aws.authenticate.assume_role_credentials_only", return_value={}), \
            patch("api.services.eks_bootstrap.apply_eks_tls", side_effect=RuntimeError("boom")), \
            patch("api.services.host_readiness.publish_host_readiness"):
        _apply_eks_tls_for_issued_certs(_NO_DEADLINE, dev_mode=False)

    env.refresh_from_db()
    assert env.eks_ingress_tls_ready is False
    assert env.eks_ingress_tls_cert_arn == "arn:aws:acm:us-east-1:123456789012:certificate/new"


# ── R3: bounded retry against a persistently failing, stable target ─────────────────────

def test_retries_on_transient_failure_within_the_timeout_window(make_eks_infra):
    infra, _env = make_eks_infra(
        eks_ingress_tls_cert_arn="arn:aws:acm:us-east-1:123456789012:certificate/abc",
        eks_ingress_tls_patch_attempted_at=timezone.now() - timedelta(minutes=5),
    )
    _issued_cert(infra)

    with patch("api.cloud_providers.aws.authenticate.assume_role_credentials_only", return_value={}), \
            patch("api.services.eks_bootstrap.apply_eks_tls", return_value=True) as apply_tls, \
            patch("api.services.host_readiness.publish_host_readiness"):
        _apply_eks_tls_for_issued_certs(_NO_DEADLINE, dev_mode=False)

    apply_tls.assert_called_once()


def test_gives_up_after_the_timeout_and_stops_calling_apply_eks_tls(make_eks_infra):
    infra, env = make_eks_infra(
        eks_ingress_tls_cert_arn="arn:aws:acm:us-east-1:123456789012:certificate/abc",
        eks_ingress_tls_patch_attempted_at=timezone.now() - EKS_TLS_PATCH_TIMEOUT - timedelta(minutes=1),
    )
    _issued_cert(infra)

    with patch("api.cloud_providers.aws.authenticate.assume_role_credentials_only", return_value={}), \
            patch("api.services.eks_bootstrap.apply_eks_tls") as apply_tls:
        _apply_eks_tls_for_issued_certs(_NO_DEADLINE, dev_mode=False)

    apply_tls.assert_not_called()
    env.refresh_from_db()
    assert env.eks_ingress_tls_ready is False


def test_giving_up_does_not_clear_the_attempted_at_marker(make_eks_infra):
    """Giving up is a 'stop retrying this cycle' decision, not a reset — only a cert
    change (a new target) or a successful patch should ever move the clock."""
    old_attempted_at = timezone.now() - EKS_TLS_PATCH_TIMEOUT - timedelta(minutes=1)
    infra, env = make_eks_infra(
        eks_ingress_tls_cert_arn="arn:aws:acm:us-east-1:123456789012:certificate/abc",
        eks_ingress_tls_patch_attempted_at=old_attempted_at,
    )
    _issued_cert(infra)

    with patch("api.cloud_providers.aws.authenticate.assume_role_credentials_only", return_value={}), \
            patch("api.services.eks_bootstrap.apply_eks_tls"):
        _apply_eks_tls_for_issued_certs(_NO_DEADLINE, dev_mode=False)

    env.refresh_from_db()
    assert env.eks_ingress_tls_patch_attempted_at == old_attempted_at


def test_time_budget_stops_the_sweep_before_more_rows_are_processed(make_eks_infra):
    infra, _env = make_eks_infra()
    _issued_cert(infra)

    with patch("api.services.eks_bootstrap.apply_eks_tls") as apply_tls:
        _apply_eks_tls_for_issued_certs(time_module.monotonic() - 1, dev_mode=False)

    apply_tls.assert_not_called()


# ── teardown clears every EKS TLS field ──────────────────────────────────────────────────

def test_teardown_clears_eks_ingress_tls_state(make_eks_infra, monkeypatch):
    from api.services.terraform_worker import TerraformWorker

    infra, env = make_eks_infra(
        eks_ingress_tls_ready=True,
        eks_ingress_tls_cert_arn="arn:aws:acm:us-east-1:123456789012:certificate/abc",
    )
    monkeypatch.setattr("api.services.terraform_worker.time.sleep", lambda *_a, **_k: None)

    with patch("api.services.terraform_worker.is_dev_mode", return_value=True), \
            patch("api.cloud_providers.aws.authenticate.is_dev_mode", return_value=True):
        Infrastructure = infra.__class__
        Infrastructure.objects.filter(id=infra.id).update(is_mock=True)
        TerraformWorker.destroy(str(infra.id))

    env.refresh_from_db()
    assert env.status == "DESTROYED"
    assert env.eks_ingress_tls_ready is False
    assert env.eks_ingress_tls_cert_arn is None
    assert env.eks_ingress_tls_patch_attempted_at is None
