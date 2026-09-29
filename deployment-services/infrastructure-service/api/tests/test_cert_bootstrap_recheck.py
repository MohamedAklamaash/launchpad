"""The periodic ISSUED/FAILED re-check for PENDING TLS certificates — run_worker.py's
check_pending_certificates, F1b part 2 pre-review §2 line 18 ("ISSUED via scheduled
re-checks, don't hold a worker thread") and the B1 security-review fix (never resurrect a
torn-down infrastructure via a background ISSUED transition).
"""
import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from api.management.commands.run_worker import check_pending_certificates
from api.models.environment import Environment
from api.models.infrastructure_certificate import InfrastructureCertificate
from django.utils import timezone


@pytest.fixture
def make_infra(db):
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    def _make(*, env_status="ACTIVE", dns_teardown_requested=False, https_listener_arn=None, exited=False):
        user = User.objects.create(
            id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@e.io", user_name="u", role="super_admin"
        )
        infra = Infrastructure.objects.create(
            user=user, name=f"i-{uuid.uuid4()}", cloud_provider="aws",
            max_cpu=1.0, max_memory=2.0, code="123456789012",
            is_cloud_authenticated=True, metadata={"aws_region": "us-east-1"}, policy_version=4,
        )
        infra.mint_dns_label()
        if dns_teardown_requested:
            Infrastructure.objects.filter(id=infra.id).update(dns_teardown_requested_at=timezone.now())
            infra.refresh_from_db()
        if exited:
            Infrastructure.objects.filter(id=infra.id).update(exited_at=timezone.now())
            infra.refresh_from_db()
        Environment.objects.create(
            infrastructure=infra, status=env_status, alb_dns="x.us-east-1.elb.amazonaws.com",
            https_listener_arn=https_listener_arn,
        )
        return infra

    return _make


def _pending_cert(infra, requested_at=None, cert_arn="arn:aws:acm:us-east-1:123456789012:certificate/abc"):
    return InfrastructureCertificate.objects.create(
        infrastructure=infra, cert_arn=cert_arn,
        tls_status=InfrastructureCertificate.TLS_PENDING,
        tls_requested_at=requested_at or timezone.now(),
    )


def _fake_describe_client(status):
    client = type("C", (), {})()
    client.describe_certificate = lambda self=None, CertificateArn=None: {"Certificate": {"Status": status}}
    return client


def test_issued_status_transitions_and_reenqueues_provision(make_infra):
    infra = make_infra()
    cert = _pending_cert(infra)

    with patch("api.cloud_providers.aws.authenticate.assume_role_credentials_only", return_value={}), \
            patch("api.services.cert_bootstrap._acm_client", return_value=_fake_describe_client("ISSUED")), \
            patch("api.services.infra_queue.InfraQueue.enqueue_provision") as enqueue:
        check_pending_certificates()

    cert.refresh_from_db()
    assert cert.tls_status == InfrastructureCertificate.TLS_ISSUED
    # A cert that just transitioned to ISSUED also matches the "listener not applied yet"
    # recovery sweep in the same tick — harmless in production (InfraQueue's own Redis
    # dedup key makes the second call a no-op), so this only asserts it fired at least once.
    enqueue.assert_any_call(str(infra.id))


def test_acm_failed_status_marks_failed_without_reenqueue(make_infra):
    infra = make_infra()
    cert = _pending_cert(infra)

    with patch("api.cloud_providers.aws.authenticate.assume_role_credentials_only", return_value={}), \
            patch("api.services.cert_bootstrap._acm_client", return_value=_fake_describe_client("FAILED")), \
            patch("api.services.infra_queue.InfraQueue.enqueue_provision") as enqueue:
        check_pending_certificates()

    cert.refresh_from_db()
    assert cert.tls_status == InfrastructureCertificate.TLS_FAILED
    enqueue.assert_not_called()


def test_timeout_marks_failed_without_touching_environment(make_infra):
    infra = make_infra()
    cert = _pending_cert(infra, requested_at=timezone.now() - timedelta(minutes=45))

    with patch("api.cloud_providers.aws.authenticate.assume_role_credentials_only", return_value={}), \
            patch("api.services.cert_bootstrap._acm_client", return_value=_fake_describe_client("PENDING_VALIDATION")):
        check_pending_certificates()

    cert.refresh_from_db()
    assert cert.tls_status == InfrastructureCertificate.TLS_FAILED
    env = Environment.objects.get(infrastructure=infra)
    assert env.status == "ACTIVE"


def test_certificate_issued_after_the_deadline_is_adopted_not_failed(make_infra):
    """Found on real AWS: DNS delegation landed after the 30-minute window, ACM issued the
    certificate, and the old ordering (timeout checked first) marked it FAILED anyway."""
    infra = make_infra()
    cert = _pending_cert(infra, requested_at=timezone.now() - timedelta(minutes=45))

    with patch("api.cloud_providers.aws.authenticate.assume_role_credentials_only", return_value={}), \
            patch("api.services.cert_bootstrap._acm_client", return_value=_fake_describe_client("ISSUED")), \
            patch("api.services.infra_queue.InfraQueue.enqueue_provision"):
        check_pending_certificates()

    cert.refresh_from_db()
    assert cert.tls_status == InfrastructureCertificate.TLS_ISSUED


def test_timed_out_row_with_unreadable_status_still_ages_out(make_infra):
    infra = make_infra()
    cert = _pending_cert(infra, requested_at=timezone.now() - timedelta(minutes=45))

    with patch("api.cloud_providers.aws.authenticate.assume_role_credentials_only", side_effect=RuntimeError("role gone")):
        check_pending_certificates()

    cert.refresh_from_db()
    assert cert.tls_status == InfrastructureCertificate.TLS_FAILED


def test_describe_error_leaves_row_pending_for_next_tick(make_infra):
    infra = make_infra()
    cert = _pending_cert(infra)

    with patch("api.cloud_providers.aws.authenticate.assume_role_credentials_only", side_effect=RuntimeError("assume role failed")):
        check_pending_certificates()

    cert.refresh_from_db()
    assert cert.tls_status == InfrastructureCertificate.TLS_PENDING


def test_certificate_with_no_arn_is_left_alone(make_infra):
    """R1 defense in depth: cert_arn should now always be set by the time a row is
    PENDING, but a null-ARN row (a pre-fix row, or a bug) must not attempt a DescribeCertificate
    call it cannot make — it simply isn't advanced this tick."""
    infra = make_infra()
    InfrastructureCertificate.objects.create(
        infrastructure=infra, tls_status=InfrastructureCertificate.TLS_PENDING, cert_arn=None,
    )

    with patch("api.cloud_providers.aws.authenticate.assume_role_credentials_only") as auth:
        check_pending_certificates()

    auth.assert_not_called()


# ---- B1: never resurrect a torn-down infrastructure via a background ISSUED transition ----

def test_skips_a_cert_whose_infra_has_requested_dns_teardown(make_infra):
    infra = make_infra(dns_teardown_requested=True)
    cert = _pending_cert(infra)

    with patch("api.cloud_providers.aws.authenticate.assume_role_credentials_only") as auth, \
            patch("api.services.infra_queue.InfraQueue.enqueue_provision") as enqueue:
        check_pending_certificates()

    auth.assert_not_called()
    enqueue.assert_not_called()
    cert.refresh_from_db()
    assert cert.tls_status == InfrastructureCertificate.TLS_PENDING


def test_skips_a_cert_whose_infra_has_exited(make_infra):
    """F6's exited_at (owner completed the exit flow) — an infra can be ACTIVE with no
    dns_teardown_requested_at set... in theory; complete_exit always sets teardown first
    in practice, but this is asserted directly rather than relying on that ordering."""
    infra = make_infra(exited=True)
    cert = _pending_cert(infra)

    with patch("api.cloud_providers.aws.authenticate.assume_role_credentials_only") as auth, \
            patch("api.services.infra_queue.InfraQueue.enqueue_provision") as enqueue:
        check_pending_certificates()

    auth.assert_not_called()
    enqueue.assert_not_called()
    cert.refresh_from_db()
    assert cert.tls_status == InfrastructureCertificate.TLS_PENDING


@pytest.mark.parametrize("env_status", ["DESTROYING", "DESTROYED", "ERROR", "PROVISIONING", "UPDATING", "PENDING"])
def test_skips_a_cert_whose_environment_is_not_active(make_infra, env_status):
    infra = make_infra(env_status=env_status)
    cert = _pending_cert(infra)

    with patch("api.cloud_providers.aws.authenticate.assume_role_credentials_only") as auth, \
            patch("api.services.infra_queue.InfraQueue.enqueue_provision") as enqueue, \
            patch("api.services.cert_bootstrap._acm_client", return_value=_fake_describe_client("ISSUED")):
        check_pending_certificates()

    auth.assert_not_called()
    enqueue.assert_not_called()
    cert.refresh_from_db()
    assert cert.tls_status == InfrastructureCertificate.TLS_PENDING


def test_skips_a_cert_with_no_environment_row_at_all(make_infra):
    infra = make_infra()
    Environment.objects.filter(infrastructure=infra).delete()
    cert = _pending_cert(infra)

    with patch("api.cloud_providers.aws.authenticate.assume_role_credentials_only") as auth:
        check_pending_certificates()

    auth.assert_not_called()
    cert.refresh_from_db()
    assert cert.tls_status == InfrastructureCertificate.TLS_PENDING


def test_still_marks_timeout_failed_only_when_eligible_not_when_torn_down(make_infra):
    """A torn-down infra's stale PENDING row is left exactly alone — not even flipped to
    FAILED — since nothing about it matters once teardown has started."""
    infra = make_infra(dns_teardown_requested=True)
    cert = _pending_cert(infra, requested_at=timezone.now() - timedelta(minutes=45))

    check_pending_certificates()

    cert.refresh_from_db()
    assert cert.tls_status == InfrastructureCertificate.TLS_PENDING


def test_does_not_use_authenticate_infrastructure_which_has_side_effects():
    """assume_role_credentials_only must be the one called — authenticate_infrastructure
    writes is_cloud_authenticated/metadata on every call, which a transient failure in this
    background poll must never be allowed to flip on the customer-facing row."""
    import inspect

    from api.management.commands import run_worker
    source = inspect.getsource(run_worker.check_pending_certificates)
    assert "assume_role_credentials_only" in source
    assert "authenticate_infrastructure(" not in source


# ---- time budget ----

def test_time_budget_defers_remaining_rows_to_the_next_tick(make_infra):
    infra_a = make_infra()
    infra_b = make_infra()
    _pending_cert(infra_a)
    _pending_cert(infra_b)

    import time as time_mod

    from api.management.commands import run_worker

    call_times = iter([0, 0, 999])  # deadline computed at 0 + budget; second row sees 999 > deadline

    with patch.object(time_mod, "monotonic", side_effect=lambda: next(call_times, 999)), \
            patch("api.cloud_providers.aws.authenticate.assume_role_credentials_only", return_value={}), \
            patch("api.services.cert_bootstrap._acm_client", return_value=_fake_describe_client("ISSUED")), \
            patch("api.services.infra_queue.InfraQueue.enqueue_provision") as enqueue:
        run_worker.check_pending_certificates()

    # Only one of the two PENDING rows should have been processed before the budget hit.
    assert enqueue.call_count <= 1


# ---- ISSUED-but-listener-missing recovery sweep ----

def test_reenqueues_issued_cert_missing_https_listener(make_infra):
    infra = make_infra(https_listener_arn=None)
    InfrastructureCertificate.objects.create(
        infrastructure=infra, cert_arn="arn:aws:acm:us-east-1:123456789012:certificate/abc",
        tls_status=InfrastructureCertificate.TLS_ISSUED, tls_requested_at=timezone.now(),
    )

    with patch("api.services.infra_queue.InfraQueue.enqueue_provision") as enqueue:
        check_pending_certificates()

    enqueue.assert_called_once_with(str(infra.id))


def test_does_not_reenqueue_issued_cert_once_listener_is_applied(make_infra):
    infra = make_infra(https_listener_arn="arn:aws:elasticloadbalancing:us-east-1:123456789012:listener/app/x/y/z")
    InfrastructureCertificate.objects.create(
        infrastructure=infra, cert_arn="arn:aws:acm:us-east-1:123456789012:certificate/abc",
        tls_status=InfrastructureCertificate.TLS_ISSUED, tls_requested_at=timezone.now(),
    )

    with patch("api.services.infra_queue.InfraQueue.enqueue_provision") as enqueue:
        check_pending_certificates()

    enqueue.assert_not_called()


def test_does_not_reenqueue_issued_cert_for_a_torn_down_infra(make_infra):
    infra = make_infra(https_listener_arn=None, dns_teardown_requested=True)
    InfrastructureCertificate.objects.create(
        infrastructure=infra, cert_arn="arn:aws:acm:us-east-1:123456789012:certificate/abc",
        tls_status=InfrastructureCertificate.TLS_ISSUED, tls_requested_at=timezone.now(),
    )

    with patch("api.services.infra_queue.InfraQueue.enqueue_provision") as enqueue:
        check_pending_certificates()

    enqueue.assert_not_called()


def test_does_not_reenqueue_issued_cert_for_eks(make_infra):
    from api.models.infrastructure import Infrastructure
    from shared.enums.orchestrator import ComputeType

    infra = make_infra(https_listener_arn=None)
    Infrastructure.objects.filter(id=infra.id).update(compute_type=ComputeType.EKS)

    InfrastructureCertificate.objects.create(
        infrastructure=infra, cert_arn="arn:aws:acm:us-east-1:123456789012:certificate/abc",
        tls_status=InfrastructureCertificate.TLS_ISSUED, tls_requested_at=timezone.now(),
    )

    with patch("api.services.infra_queue.InfraQueue.enqueue_provision") as enqueue:
        check_pending_certificates()

    enqueue.assert_not_called()
