"""Per-customer-domain ACM certificate lifecycle: request/poll/describe/delete against the
FakeAcmClient double shared with cert_bootstrap.py (dev_mode + infra_is_mock=True), plus
the certificate-shape guard against a mismatched DomainName."""
from unittest.mock import patch

import pytest
from api.services import cert_bootstrap, custom_domain_cert
from django.utils import timezone


@pytest.fixture(autouse=True)
def _reset_fake_acm():
    cert_bootstrap.reset_fake_acm_for_tests()
    yield
    cert_bootstrap.reset_fake_acm_for_tests()


_CREDS = {}
_REGION = "us-east-1"


def _mock_kwargs():
    return {"credentials": _CREDS, "region": _REGION, "infra_is_mock": True, "dev_mode": True}


def test_request_certificate_returns_arn_tagged_managed_by_launchpad():
    arn = custom_domain_cert.request_certificate(
        "app.example.com", "domain-1", timezone.now(), **_mock_kwargs(),
    )

    assert arn.startswith("arn:aws:acm:")
    client = cert_bootstrap._shared_fake_acm()
    tags = client.list_tags_for_certificate(CertificateArn=arn)["Tags"]
    assert {"Key": "ManagedBy", "Value": "launchpad"} in tags


def test_request_certificate_is_idempotent_per_domain_and_requested_at():
    now = timezone.now()
    arn1 = custom_domain_cert.request_certificate("app.example.com", "domain-1", now, **_mock_kwargs())
    arn2 = custom_domain_cert.request_certificate("app.example.com", "domain-1", now, **_mock_kwargs())

    assert arn1 == arn2


def test_poll_for_validation_record_returns_cname_record():
    arn = custom_domain_cert.request_certificate(
        "app.example.com", "domain-1", timezone.now(), **_mock_kwargs(),
    )

    record = custom_domain_cert.poll_for_validation_record(arn, "app.example.com", **_mock_kwargs())

    assert record["type"] == "CNAME"
    assert record["name"]
    assert record["value"]


def test_describe_validation_record_single_shot_backfill():
    arn = custom_domain_cert.request_certificate(
        "app.example.com", "domain-1", timezone.now(), **_mock_kwargs(),
    )

    record = custom_domain_cert.describe_validation_record(arn, "app.example.com", **_mock_kwargs())

    assert record["type"] == "CNAME"


def test_certificate_status_progresses_to_issued_after_enough_describes():
    arn = custom_domain_cert.request_certificate(
        "app.example.com", "domain-1", timezone.now(), **_mock_kwargs(),
    )

    statuses = [custom_domain_cert.certificate_status(arn, **_mock_kwargs()) for _ in range(3)]

    assert statuses[0] == "PENDING_VALIDATION"
    assert statuses[-1] == "ISSUED"


def test_delete_certificate_removes_it():
    arn = custom_domain_cert.request_certificate(
        "app.example.com", "domain-1", timezone.now(), **_mock_kwargs(),
    )

    custom_domain_cert.delete_certificate(arn, **_mock_kwargs())

    client = cert_bootstrap._shared_fake_acm()
    assert arn not in client._certs


def test_delete_certificate_is_best_effort_on_unknown_arn():
    # Must not raise even though this ARN was never issued.
    custom_domain_cert.delete_certificate("arn:aws:acm:mock:000:certificate/does-not-exist", **_mock_kwargs())


def test_extract_validation_record_raises_on_domain_mismatch():
    certificate = {
        "CertificateArn": "arn:x",
        "DomainName": "other.example.com",
        "DomainValidationOptions": [],
    }
    with pytest.raises(custom_domain_cert.CertificateShapeError):
        custom_domain_cert._extract_validation_record(certificate, "app.example.com")


def test_poll_for_validation_record_returns_none_when_never_populated():
    class NeverReadyClient(cert_bootstrap.FakeAcmClient):
        def describe_certificate(self, CertificateArn):
            response = super().describe_certificate(CertificateArn)
            response["Certificate"]["DomainValidationOptions"] = []
            return response

    client = NeverReadyClient()
    arn = client.request_certificate(DomainName="app.example.com", ValidationMethod="DNS")["CertificateArn"]

    with patch.object(custom_domain_cert, "_acm_client", return_value=client):
        record = custom_domain_cert.poll_for_validation_record(
            arn, "app.example.com", credentials=_CREDS, region=_REGION, infra_is_mock=True, dev_mode=True,
        )

    assert record is None
