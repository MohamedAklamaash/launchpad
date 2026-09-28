"""force_disable_custom_domain (H6): operator override that tears a custom domain down
through the exact same DISABLING -> detach -> DISABLED path CustomDomainService.delete_domain
uses, without the owner-only check — see plan/H-hardening.md's H6 section."""
import uuid
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


@pytest.fixture
def make_user(db):
    from api.models.user import User

    def _make():
        return User.objects.create(id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="t")
    return _make


@pytest.fixture
def make_infra(db, make_user):
    from api.models.infrastructure import Infrastructure

    def _make():
        return Infrastructure.objects.create(
            user=make_user(), name=f"infra-{uuid.uuid4()}", cloud_provider="aws",
            max_cpu=1024, max_memory=512,
        )
    return _make


@pytest.fixture
def make_domain(make_infra):
    from api.models.custom_domain import CustomDomain

    def _make(infra=None, hostname="app.example.com"):
        infra = infra or make_infra()
        domain, _token = CustomDomain.reserve(hostname, infra, application_id=uuid.uuid4())
        return domain
    return _make


def _detach_ok():
    return patch("api.services.custom_domain_service.CustomDomainService._detach", return_value=True)


def test_without_confirm_reports_only_and_changes_nothing(make_domain):
    domain = make_domain()
    out = StringIO()
    call_command(
        "force_disable_custom_domain", f"--domain-id={domain.id}", "--reason=abuse report", stdout=out,
    )
    domain.refresh_from_db()
    assert domain.status == "PENDING"
    assert "dry-run" in out.getvalue()


def test_empty_reason_is_rejected(make_domain):
    domain = make_domain()
    with pytest.raises(CommandError):
        call_command("force_disable_custom_domain", f"--domain-id={domain.id}", "--reason=   ", "--confirm")


def test_unknown_domain_id_raises(db):
    with pytest.raises(CommandError):
        call_command("force_disable_custom_domain", f"--domain-id={uuid.uuid4()}", "--reason=x", "--confirm")


def test_malformed_domain_id_raises(db):
    with pytest.raises(CommandError):
        call_command("force_disable_custom_domain", "--domain-id=not-a-uuid", "--reason=x", "--confirm")


def test_force_disable_by_domain_id_reuses_the_teardown_path(make_domain):
    domain = make_domain()
    with _detach_ok():
        out = StringIO()
        call_command(
            "force_disable_custom_domain", f"--domain-id={domain.id}",
            "--reason=DMCA takedown", "--confirm", stdout=out,
        )
    domain.refresh_from_db()
    assert domain.status == "DISABLED"
    assert "DISABLED" in out.getvalue()


def test_force_disable_by_hostname(make_domain):
    domain = make_domain(hostname="Shady.Example.com")
    with _detach_ok():
        call_command(
            "force_disable_custom_domain", "--hostname=shady.example.com",
            "--reason=abuse", "--confirm",
        )
    domain.refresh_from_db()
    assert domain.status == "DISABLED"


def test_unknown_hostname_raises(db):
    with pytest.raises(CommandError):
        call_command("force_disable_custom_domain", "--hostname=nowhere.example.com", "--reason=x", "--confirm")


def test_ambiguous_hostname_requires_domain_id(make_infra):
    from api.models.custom_domain import CustomDomain

    CustomDomain.reserve("shared.example.com", make_infra(), application_id=uuid.uuid4())
    CustomDomain.reserve("shared.example.com", make_infra(), application_id=uuid.uuid4())

    with pytest.raises(CommandError):
        call_command("force_disable_custom_domain", "--hostname=shared.example.com", "--reason=x", "--confirm")


def test_already_disabled_is_a_noop(make_domain):
    domain = make_domain()
    with _detach_ok():
        call_command(
            "force_disable_custom_domain", f"--domain-id={domain.id}", "--reason=first", "--confirm",
        )
    out = StringIO()
    call_command(
        "force_disable_custom_domain", f"--domain-id={domain.id}", "--reason=second", "--confirm", stdout=out,
    )
    assert "already DISABLED" in out.getvalue()


def test_unconfirmed_detach_leaves_it_in_disabling_for_the_sweep(make_domain):
    domain = make_domain()
    with patch("api.services.custom_domain_service.CustomDomainService._detach", return_value=False):
        out = StringIO()
        call_command(
            "force_disable_custom_domain", f"--domain-id={domain.id}", "--reason=abuse", "--confirm", stdout=out,
        )
    domain.refresh_from_db()
    assert domain.status == "DISABLING"
    assert "DISABLING" in out.getvalue()


def test_audit_log_line_is_emitted(make_domain, caplog):
    domain = make_domain()
    with _detach_ok(), caplog.at_level("INFO", logger="audit.custom_domain"):
        call_command(
            "force_disable_custom_domain", f"--domain-id={domain.id}",
            "--reason=abuse report from support ticket #123", "--confirm",
        )
    matches = [r for r in caplog.records if r.name == "audit.custom_domain"]
    assert len(matches) == 1
    assert matches[0].domain_id == str(domain.id)
    assert matches[0].reason == "abuse report from support ticket #123"
    assert matches[0].resulting_status == "DISABLED"
    assert matches[0].outcome == "ok"


def test_teardown_failure_is_audited_and_leaves_the_domain_in_disabling(make_domain, caplog):
    """_teardown swallows a failed detach (logs, leaves DISABLING for the next sweep), but
    a non-AccessDenied certificate-delete failure re-raises — mark_disabling() already
    committed by that point, so this is real state that must be audited too, not silently
    lost to an uncaught traceback."""
    from botocore.exceptions import ClientError

    domain = make_domain()
    domain.cert_arn = "arn:aws:acm:us-east-1:123456789012:certificate/abc"
    domain.save(update_fields=["cert_arn"])

    error = ClientError({"Error": {"Code": "InternalFailure", "Message": "boom"}}, "DeleteCertificate")

    with _detach_ok(), \
         patch("api.services.custom_domain_service.is_dev_mode", return_value=True), \
         patch("api.services.custom_domain_service.assume_role_credentials_only", return_value={}), \
         patch("api.services.custom_domain_service.custom_domain_cert.delete_certificate", side_effect=error), \
         caplog.at_level("INFO", logger="audit.custom_domain"), \
         pytest.raises(CommandError):
        call_command(
            "force_disable_custom_domain", f"--domain-id={domain.id}",
            "--reason=abuse", "--confirm",
        )

    domain.refresh_from_db()
    assert domain.status == "DISABLING"

    matches = [r for r in caplog.records if r.name == "audit.custom_domain"]
    assert len(matches) == 1
    assert matches[0].outcome == "error"
    assert matches[0].resulting_status == "DISABLING"
    assert "InternalFailure" in matches[0].error or "ClientError" in matches[0].error
