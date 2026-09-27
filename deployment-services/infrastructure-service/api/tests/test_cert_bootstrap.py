"""ACM certificate bootstrap — F1b part 2 pre-review §2 and §6 item 6.

Idempotency, ISSUED poll success/timeout, policy_version gate, and shape validation on
whatever ACM hands back — exercised against the FakeAcmClient/BotoCore Stubber, never a
real ACM account.
"""
import uuid
from unittest.mock import patch

import pytest
from api.models.infrastructure_certificate import InfrastructureCertificate
from api.services import cert_bootstrap
from api.services.cert_bootstrap import (
    MIN_POLICY_VERSION_FOR_TLS,
    ensure_certificate,
)
from django.utils import timezone


@pytest.fixture(autouse=True)
def _reset_fake_acm():
    cert_bootstrap.reset_fake_acm_for_tests()
    yield
    cert_bootstrap.reset_fake_acm_for_tests()


@pytest.fixture
def make_infra(db):
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    def _make(*, policy_version=MIN_POLICY_VERSION_FOR_TLS):
        user = User.objects.create(
            id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@e.io", user_name="u", role="super_admin"
        )
        infra = Infrastructure.objects.create(
            user=user, name=f"i-{uuid.uuid4()}", cloud_provider="aws",
            max_cpu=1.0, max_memory=2.0, code="123456789012",
            is_cloud_authenticated=True, metadata={"aws_region": "us-east-1"},
            policy_version=policy_version,
        )
        infra.mint_dns_label()
        return infra

    return _make


def _dev_ensure(infra):
    ensure_certificate(
        infra, credentials={}, region="us-east-1", infra_is_mock=True, dev_mode=True,
    )


# ---- policy_version gate ----

def test_policy_version_below_minimum_skips_tls_without_any_acm_call(make_infra):
    infra = make_infra(policy_version=MIN_POLICY_VERSION_FOR_TLS - 1)

    _dev_ensure(infra)

    cert = InfrastructureCertificate.objects.get(infrastructure=infra)
    assert cert.tls_status == InfrastructureCertificate.TLS_POLICY_STALE
    assert cert.cert_arn is None
    assert cert.tls_requested_at is None


def test_null_policy_version_skips_tls(make_infra):
    infra = make_infra(policy_version=None)

    _dev_ensure(infra)

    cert = InfrastructureCertificate.objects.get(infrastructure=infra)
    assert cert.tls_status == InfrastructureCertificate.TLS_POLICY_STALE


def test_policy_stale_does_not_clobber_an_already_issued_certificate(make_infra):
    infra = make_infra()
    cert = InfrastructureCertificate.objects.create(
        infrastructure=infra, tls_status=InfrastructureCertificate.TLS_ISSUED,
        cert_arn="arn:aws:acm:mock:0:certificate/x",
    )
    from api.models.infrastructure import Infrastructure
    Infrastructure.objects.filter(id=infra.id).update(policy_version=MIN_POLICY_VERSION_FOR_TLS - 1)
    infra.refresh_from_db()

    _dev_ensure(infra)

    cert.refresh_from_db()
    assert cert.tls_status == InfrastructureCertificate.TLS_ISSUED


# ---- happy path / idempotency ----

def test_first_call_requests_a_certificate_and_writes_pending_row(make_infra):
    infra = make_infra()

    _dev_ensure(infra)

    cert = InfrastructureCertificate.objects.get(infrastructure=infra)
    assert cert.tls_status == InfrastructureCertificate.TLS_PENDING
    assert cert.cert_arn is not None
    assert cert.tls_requested_at is not None
    assert cert.validation_name is not None
    assert cert.validation_value is not None
    assert cert.validation_name.endswith(f".{infra.dns_label}.launchpad.app.")
    assert cert.validation_value.endswith(".acm-validations.aws.")


def test_second_call_with_pending_row_is_a_noop(make_infra):
    infra = make_infra()
    _dev_ensure(infra)
    first = InfrastructureCertificate.objects.get(infrastructure=infra)
    first_arn = first.cert_arn
    first_requested_at = first.tls_requested_at

    _dev_ensure(infra)

    second = InfrastructureCertificate.objects.get(infrastructure=infra)
    assert second.cert_arn == first_arn
    assert second.tls_requested_at == first_requested_at


def test_issued_certificate_is_never_re_requested(make_infra):
    infra = make_infra()
    InfrastructureCertificate.objects.create(
        infrastructure=infra, tls_status=InfrastructureCertificate.TLS_ISSUED,
        cert_arn="arn:aws:acm:mock:0:certificate/already-issued",
    )

    with patch.object(cert_bootstrap, "_reuse_or_request_certificate") as reuse_or_request:
        _dev_ensure(infra)

    reuse_or_request.assert_not_called()


def test_failed_certificate_is_deleted_and_re_requested(make_infra):
    infra = make_infra()
    stale_arn = "arn:aws:acm:mock:0:certificate/stale"
    InfrastructureCertificate.objects.create(
        infrastructure=infra, tls_status=InfrastructureCertificate.TLS_FAILED, cert_arn=stale_arn,
    )

    with patch.object(cert_bootstrap, "_best_effort_delete") as best_effort_delete:
        _dev_ensure(infra)
        best_effort_delete.assert_called_once()
        assert best_effort_delete.call_args.args[1] == stale_arn

    cert = InfrastructureCertificate.objects.get(infrastructure=infra)
    assert cert.tls_status == InfrastructureCertificate.TLS_PENDING
    assert cert.cert_arn != stale_arn


def test_reuses_an_existing_tagged_certificate_for_the_same_domain(make_infra):
    infra = make_infra()
    from api.services.platform_dns import naming

    client = cert_bootstrap._shared_fake_acm()
    domain = naming.wildcard_record_name(infra.dns_label, "launchpad.app")
    existing = client.request_certificate(
        DomainName=domain, ValidationMethod="DNS",
        Tags=[{"Key": cert_bootstrap.CERT_TAG_KEY, "Value": cert_bootstrap.CERT_TAG_VALUE}],
    )["CertificateArn"]

    _dev_ensure(infra)

    cert = InfrastructureCertificate.objects.get(infrastructure=infra)
    assert cert.cert_arn == existing


def test_reuse_skips_a_tagged_certificate_that_is_not_amazon_issued(make_infra):
    """An IMPORTED or PRIVATE certificate happening to carry the ManagedBy=launchpad tag
    (Launchpad never creates either kind) must never be adopted."""
    infra = make_infra()
    from api.services.platform_dns import naming

    client = cert_bootstrap._shared_fake_acm()
    domain = naming.wildcard_record_name(infra.dns_label, "launchpad.app")
    imported_arn = client.request_certificate(
        DomainName=domain, ValidationMethod="DNS",
        Tags=[{"Key": cert_bootstrap.CERT_TAG_KEY, "Value": cert_bootstrap.CERT_TAG_VALUE}],
    )["CertificateArn"]
    client._certs[imported_arn]["type"] = "IMPORTED"
    original_list = client.list_certificates

    def _list_with_type(*args, **kwargs):
        response = original_list(*args, **kwargs)
        for summary in response["CertificateSummaryList"]:
            summary["Type"] = client._certs[summary["CertificateArn"]].get("type", "AMAZON_ISSUED")
        return response

    with patch.object(client, "list_certificates", side_effect=_list_with_type):
        _dev_ensure(infra)

    cert = InfrastructureCertificate.objects.get(infrastructure=infra)
    assert cert.cert_arn != imported_arn


def test_reuse_follows_next_token_pagination(make_infra):
    infra = make_infra()
    from api.services.platform_dns import naming

    client = cert_bootstrap._shared_fake_acm()
    domain = naming.wildcard_record_name(infra.dns_label, "launchpad.app")
    existing = client.request_certificate(
        DomainName=domain, ValidationMethod="DNS",
        Tags=[{"Key": cert_bootstrap.CERT_TAG_KEY, "Value": cert_bootstrap.CERT_TAG_VALUE}],
    )["CertificateArn"]

    real_list = client.list_certificates
    call_count = {"n": 0}

    def _paginated(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # First page: empty, but says there's more.
            return {"CertificateSummaryList": [], "NextToken": "page-2"}
        return real_list(*args, **kwargs)

    with patch.object(client, "list_certificates", side_effect=_paginated):
        _dev_ensure(infra)

    assert call_count["n"] == 2
    cert = InfrastructureCertificate.objects.get(infrastructure=infra)
    assert cert.cert_arn == existing


# ---- R2: IdempotencyToken is bound to this attempt, not just infra_id ----

def test_idempotency_token_changes_between_a_failed_attempt_and_its_retry(make_infra):
    """A constant sha256(infra_id) token would hand a post-timeout retry the just-deleted
    certificate's ARN back (ACM's IdempotencyToken dedup window is ~1h)."""
    infra = make_infra()
    tokens = []
    client = cert_bootstrap._shared_fake_acm()
    original_request = client.request_certificate

    def _capture(*args, **kwargs):
        tokens.append(kwargs.get("IdempotencyToken"))
        return original_request(*args, **kwargs)

    with patch.object(client, "request_certificate", side_effect=_capture):
        _dev_ensure(infra)  # first attempt
        InfrastructureCertificate.objects.filter(infrastructure=infra).update(
            tls_status=InfrastructureCertificate.TLS_FAILED,
        )
        _dev_ensure(infra)  # retry

    assert len(tokens) == 2
    assert tokens[0] != tokens[1]


def test_idempotency_token_is_a_pure_function_of_infra_id_and_requested_at():
    from datetime import datetime
    from datetime import timezone as dt_timezone

    t1 = datetime(2026, 1, 1, tzinfo=dt_timezone.utc)
    t2 = datetime(2026, 1, 1, 0, 0, 1, tzinfo=dt_timezone.utc)
    a = cert_bootstrap._idempotency_token("infra-1", t1)
    b = cert_bootstrap._idempotency_token("infra-1", t1)
    c = cert_bootstrap._idempotency_token("infra-1", t2)
    assert a == b
    assert a != c
    assert len(a) <= 32


# ---- shape validation on whatever ACM hands back ----

def test_wrong_domain_name_from_acm_is_rejected(make_infra):
    infra = make_infra()

    class LyingClient(cert_bootstrap.FakeAcmClient):
        def describe_certificate(self, CertificateArn):
            response = super().describe_certificate(CertificateArn)
            response["Certificate"]["DomainName"] = "evil.example.com"
            return response

    with patch.object(cert_bootstrap, "_acm_client", return_value=LyingClient()):
        ensure_certificate(infra, credentials={}, region="us-east-1", infra_is_mock=True, dev_mode=True)

    cert = InfrastructureCertificate.objects.get(infrastructure=infra)
    # ensure_certificate never raises out; a CertificateShapeError deletes the bad
    # certificate immediately and marks FAILED rather than leaving it PENDING for 30
    # minutes only to fail anyway (R1).
    assert cert.tls_status == InfrastructureCertificate.TLS_FAILED
    assert cert.cert_arn is None


def test_bad_validation_value_shape_is_rejected(make_infra):
    infra = make_infra()

    class BadValueClient(cert_bootstrap.FakeAcmClient):
        def describe_certificate(self, CertificateArn):
            response = super().describe_certificate(CertificateArn)
            response["Certificate"]["DomainValidationOptions"][0]["ResourceRecord"]["Value"] = "not-a-real-value"
            return response

    client = BadValueClient()
    with patch.object(cert_bootstrap, "_acm_client", return_value=client):
        # _ensure_certificate no longer propagates CertificateShapeError — it handles it
        # internally (delete + FAILED) so a caller one level up (ensure_certificate) never
        # needs its own try/except for this specific case.
        cert_bootstrap._ensure_certificate(
            infra, credentials={}, region="us-east-1", infra_is_mock=True, dev_mode=True,
        )

    cert = InfrastructureCertificate.objects.get(infrastructure=infra)
    assert cert.tls_status == InfrastructureCertificate.TLS_FAILED
    assert cert.cert_arn is None
    # The certificate was actually deleted from ACM, not just forgotten in our DB.
    assert client.list_certificates()["CertificateSummaryList"] == []


# ---- timeout on the DescribeCertificate poll ----

def test_resource_record_poll_timeout_leaves_cert_arn_set_for_the_periodic_recheck(make_infra, monkeypatch):
    """ensure_certificate's own bounded poll only covers the initial ResourceRecord
    appearing (seconds in practice); it is not the ISSUED timeout (run_worker.py's
    periodic re-check owns that, per MIN policy / ISSUED_CHECK_TIMEOUT). R1: cert_arn must
    already be persisted by the time this poll could time out, so the periodic re-check
    (which excludes null-ARN rows) can still find and eventually time out this row instead
    of it being orphaned forever."""
    infra = make_infra()
    monkeypatch.setattr(cert_bootstrap, "_RESOURCE_RECORD_POLL_ATTEMPTS", 1)
    monkeypatch.setattr(cert_bootstrap, "_RESOURCE_RECORD_POLL_INTERVAL_SECONDS", 0)

    class NeverReadyClient(cert_bootstrap.FakeAcmClient):
        def describe_certificate(self, CertificateArn):
            response = super().describe_certificate(CertificateArn)
            response["Certificate"]["DomainValidationOptions"][0]["ResourceRecord"] = None
            return response

    with patch.object(cert_bootstrap, "_acm_client", return_value=NeverReadyClient()):
        ensure_certificate(infra, credentials={}, region="us-east-1", infra_is_mock=True, dev_mode=True)

    cert = InfrastructureCertificate.objects.get(infrastructure=infra)
    assert cert.tls_status == InfrastructureCertificate.TLS_PENDING
    assert cert.tls_requested_at is not None
    assert cert.cert_arn is not None


# ---- mock/real gate mirrors route53_client exactly ----

def test_acm_client_refuses_mock_infra_outside_dev_mode():
    from api.services.platform_dns.route53_client import MockRealMismatch

    with pytest.raises(MockRealMismatch):
        cert_bootstrap._acm_client(infra_is_mock=True, dev_mode=False, credentials={}, region="us-east-1")


def test_acm_client_refuses_real_infra_inside_dev_mode():
    from api.services.platform_dns.route53_client import MockRealMismatch

    with pytest.raises(MockRealMismatch):
        cert_bootstrap._acm_client(infra_is_mock=False, dev_mode=True, credentials={}, region="us-east-1")


# ---- never fails the caller ----

def test_ensure_certificate_never_raises_even_on_internal_error(make_infra):
    infra = make_infra()
    with patch.object(cert_bootstrap, "_acm_client", side_effect=RuntimeError("boom")):
        ensure_certificate(infra, credentials={}, region="us-east-1", infra_is_mock=True, dev_mode=True)
    # No exception propagated (would have failed the test above); nothing persisted since
    # the failure is before any row is created.
    assert not InfrastructureCertificate.objects.filter(infrastructure=infra).exists()


# ---- R4: maybe_reenqueue_after_policy_refresh ----

def test_reenqueue_after_policy_refresh_skips_a_torn_down_infra(make_infra):
    from api.models.environment import Environment
    from api.models.infrastructure import Infrastructure

    infra = make_infra(policy_version=cert_bootstrap.MIN_POLICY_VERSION_FOR_TLS)
    Environment.objects.create(infrastructure=infra, status="ACTIVE")
    Infrastructure.objects.filter(id=infra.id).update(dns_teardown_requested_at=timezone.now())

    with patch("api.services.infra_queue.InfraQueue.enqueue_provision") as enqueue:
        cert_bootstrap.maybe_reenqueue_after_policy_refresh(infra.id)

    enqueue.assert_not_called()


def test_reenqueue_after_policy_refresh_is_a_noop_for_a_nonexistent_infra(db):
    with patch("api.services.infra_queue.InfraQueue.enqueue_provision") as enqueue:
        cert_bootstrap.maybe_reenqueue_after_policy_refresh(uuid.uuid4())

    enqueue.assert_not_called()
