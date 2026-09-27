"""CustomDomainService: owner-only claim/verify/delete orchestration, the cross-service
application_id check and attach/detach calls to application-service, the EKS refusal, the
PENDING-claim cap, and idempotent teardown — see plan/F1b-tls-activation.md's Part 3b
design section. Exercised against the real custom_domain_cert/custom_domain_dns modules
with infra.is_mock=True + a forced dev_mode, so the certificate/DNS state machine is
genuinely run, not stubbed out — only the cross-service HTTP calls and AssumeRole are
mocked."""
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from api.services import cert_bootstrap, custom_domain_dns
from api.services.custom_domain_service import (
    CustomDomainConflictError,
    CustomDomainService,
)


@pytest.fixture(autouse=True)
def _reset_fakes():
    cert_bootstrap.reset_fake_acm_for_tests()
    custom_domain_dns.reset_fake_resolver_for_tests()
    yield
    cert_bootstrap.reset_fake_acm_for_tests()
    custom_domain_dns.reset_fake_resolver_for_tests()


@pytest.fixture(autouse=True)
def _mock_dev_mode():
    with patch("api.services.custom_domain_service.is_dev_mode", return_value=True), \
         patch("api.services.custom_domain_service.assume_role_credentials_only", return_value={}):
        yield


def _response(status_code, json_body=None, text=""):
    return SimpleNamespace(status_code=status_code, json=lambda: json_body or {}, text=text)


@pytest.fixture
def make_user(db):
    from api.models.user import User

    def _make():
        return User.objects.create(
            id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="t", role="super_admin",
        )

    return _make


@pytest.fixture
def make_infra(db, make_user):
    from api.models.infrastructure import Infrastructure

    def _make(user=None, compute_type="ecs_fargate"):
        return Infrastructure.objects.create(
            user=user or make_user(), name=f"infra-{uuid.uuid4()}", cloud_provider="aws",
            max_cpu=1024, max_memory=512, code="123456789012", is_mock=True,
            compute_type=compute_type,
        )

    return _make


def _ok_app_lookup(infra_id):
    return _response(200, {"infrastructure_id": str(infra_id)})


def _claim(service, user, infra, app_id=None):
    with patch("shared.resilience.http_client.ResilientHttpClient.get", return_value=_ok_app_lookup(infra.id)):
        return service.claim_domain(user.id, infra.id, app_id or uuid.uuid4(), "app.example.com")


def _publish_txt(domain, token):
    custom_domain_dns._shared_fake_resolver().set_txt(domain.hostname, token)


def test_claim_happy_path_returns_domain_token_and_validation_record(make_infra):
    infra = make_infra()
    service = CustomDomainService()

    domain, token, record = _claim(service, infra.user, infra)

    assert domain.status == "PENDING"
    assert domain.cert_arn
    assert token
    assert record["type"] == "CNAME"


def test_claim_refuses_eks_infra(make_infra):
    infra = make_infra(compute_type="eks")
    service = CustomDomainService()

    with pytest.raises(ValueError, match="ECS Fargate"):
        _claim(service, infra.user, infra)


def test_claim_refuses_invited_non_owner_member(make_infra, make_user):
    """infra-service has no per-infra role gradation of its own (that lives only in
    application-service's copy) — an invited member, regardless of what role they'd carry
    there, is refused write access here the same way DatabaseService._require_owner
    refuses one for database management. `invited_users` membership makes the infra
    resolvable at all (get_by_id) but claim/verify/delete still demand ownership."""
    infra = make_infra()
    other_user = make_user()
    infra.invited_users.add(other_user)
    service = CustomDomainService()

    with pytest.raises(PermissionError):
        _claim(service, other_user, infra)


def test_claim_raises_lookup_error_for_unowned_infra_id(make_user):
    other_user = make_user()
    service = CustomDomainService()

    with pytest.raises(LookupError):
        service.claim_domain(other_user.id, uuid.uuid4(), uuid.uuid4(), "app.example.com")


def test_claim_raises_lookup_error_when_application_not_found(make_infra):
    infra = make_infra()
    service = CustomDomainService()

    with patch("shared.resilience.http_client.ResilientHttpClient.get", return_value=_response(404)), \
         pytest.raises(LookupError):
        service.claim_domain(infra.user.id, infra.id, uuid.uuid4(), "app.example.com")


def test_claim_raises_lookup_error_when_application_belongs_to_different_infra(make_infra):
    infra = make_infra()
    service = CustomDomainService()

    with patch("shared.resilience.http_client.ResilientHttpClient.get", return_value=_ok_app_lookup(uuid.uuid4())), \
         pytest.raises(LookupError):
        service.claim_domain(infra.user.id, infra.id, uuid.uuid4(), "app.example.com")


def test_claim_rejects_reserved_suffix_as_value_error(make_infra, settings):
    settings.RESERVED_DOMAIN_SUFFIX = "launchpad.app"
    infra = make_infra()
    service = CustomDomainService()

    with patch("shared.resilience.http_client.ResilientHttpClient.get", return_value=_ok_app_lookup(infra.id)), \
         pytest.raises(ValueError):
        service.claim_domain(infra.user.id, infra.id, uuid.uuid4(), "evil.launchpad.app")


def test_claim_enforces_pending_cap_per_infra(make_infra, settings):
    settings.MAX_PENDING_CUSTOM_DOMAINS_PER_INFRA = 1
    infra = make_infra()
    service = CustomDomainService()

    _claim(service, infra.user, infra)

    with patch("shared.resilience.http_client.ResilientHttpClient.get", return_value=_ok_app_lookup(infra.id)), \
         pytest.raises(CustomDomainConflictError):
        service.claim_domain(infra.user.id, infra.id, uuid.uuid4(), "second.example.com")


def test_verify_happy_path_validates_and_attaches(make_infra):
    infra = make_infra()
    service = CustomDomainService()
    domain, token, _record = _claim(service, infra.user, infra)
    _publish_txt(domain, token)

    with patch("api.services.custom_domain_service.CustomDomainService._attach", return_value={}) as attach:
        validated = service.verify_domain(infra.user.id, infra.id, domain.id)

    assert validated.status == "VALIDATED"
    attach.assert_called_once()


def test_verify_fails_without_matching_txt(make_infra):
    infra = make_infra()
    service = CustomDomainService()
    domain, _token, _record = _claim(service, infra.user, infra)

    with pytest.raises(ValueError, match="TXT record"):
        service.verify_domain(infra.user.id, infra.id, domain.id)

    domain.refresh_from_db()
    assert domain.status == "PENDING"


def test_verify_fails_when_certificate_not_issued(make_infra):
    infra = make_infra()
    service = CustomDomainService()
    domain, token, _record = _claim(service, infra.user, infra)
    _publish_txt(domain, token)

    with patch("api.services.custom_domain_cert.certificate_status", return_value="PENDING_VALIDATION"), \
         pytest.raises(ValueError, match="not yet issued"):
        service.verify_domain(infra.user.id, infra.id, domain.id)


def test_verify_fails_on_expired_claim(make_infra):
    from datetime import timedelta

    from django.utils import timezone

    infra = make_infra()
    service = CustomDomainService()
    domain, token, _record = _claim(service, infra.user, infra)
    _publish_txt(domain, token)
    domain.expires_at = timezone.now() - timedelta(seconds=1)
    domain.save(update_fields=["expires_at"])

    with pytest.raises(ValueError, match="expired"):
        service.verify_domain(infra.user.id, infra.id, domain.id)


def test_verify_conflict_detaches_on_lost_race(make_infra):
    infra = make_infra()
    service = CustomDomainService()
    domain, token, _record = _claim(service, infra.user, infra)
    _publish_txt(domain, token)

    from api.models.custom_domain import CustomDomain
    CustomDomain.objects.filter(hostname=domain.hostname).exclude(pk=domain.pk).delete()
    CustomDomain.objects.create(
        hostname=domain.hostname, infrastructure=make_infra(), application_id=uuid.uuid4(),
        status="VALIDATED", expires_at=domain.expires_at,
    )

    with patch("api.services.custom_domain_service.CustomDomainService._attach", return_value={}), \
         patch("api.services.custom_domain_service.CustomDomainService._detach") as detach, \
         pytest.raises(CustomDomainConflictError):
        service.verify_domain(infra.user.id, infra.id, domain.id)

    detach.assert_called_once_with(domain.hostname)
    domain.refresh_from_db()
    assert domain.status == "PENDING"


def test_delete_pending_domain_deletes_row_and_cert(make_infra):
    infra = make_infra()
    service = CustomDomainService()
    domain, _token, _record = _claim(service, infra.user, infra)
    cert_arn = domain.cert_arn

    service.delete_domain(infra.user.id, infra.id, domain.id)

    from api.models.custom_domain import CustomDomain
    assert not CustomDomain.objects.filter(id=domain.id).exists()
    client = cert_bootstrap._shared_fake_acm()
    assert cert_arn not in client._certs


def test_delete_validated_domain_detaches_and_disables(make_infra):
    infra = make_infra()
    service = CustomDomainService()
    domain, token, _record = _claim(service, infra.user, infra)
    _publish_txt(domain, token)
    with patch("api.services.custom_domain_service.CustomDomainService._attach", return_value={}):
        service.verify_domain(infra.user.id, infra.id, domain.id)

    with patch("api.services.custom_domain_service.CustomDomainService._detach") as detach:
        result = service.delete_domain(infra.user.id, infra.id, domain.id)

    assert result.status == "DISABLED"
    detach.assert_called_once_with(domain.hostname)


def test_delete_is_idempotent_on_already_disabled(make_infra):
    infra = make_infra()
    service = CustomDomainService()
    domain, token, _record = _claim(service, infra.user, infra)
    _publish_txt(domain, token)
    with patch("api.services.custom_domain_service.CustomDomainService._attach", return_value={}):
        service.verify_domain(infra.user.id, infra.id, domain.id)
    with patch("api.services.custom_domain_service.CustomDomainService._detach"):
        service.delete_domain(infra.user.id, infra.id, domain.id)

    # Second delete on an already-DISABLED row must not raise or re-attempt AWS calls.
    service.delete_domain(infra.user.id, infra.id, domain.id)


def test_teardown_for_infrastructure_continues_past_a_failure(make_infra):
    infra = make_infra()
    service = CustomDomainService()
    d1, _t1, _r1 = _claim(service, infra.user, infra)
    with patch("shared.resilience.http_client.ResilientHttpClient.get", return_value=_ok_app_lookup(infra.id)):
        d2, _t2, _r2 = service.claim_domain(infra.user.id, infra.id, uuid.uuid4(), "second.example.com")

    with patch("api.services.custom_domain_service.custom_domain_cert.delete_certificate",
               side_effect=[RuntimeError("boom"), None]):
        service.teardown_for_infrastructure(infra)

    from api.models.custom_domain import CustomDomain
    # One domain's teardown raised and was swallowed (order between d1/d2 is not
    # guaranteed); the other's still completed — teardown must not abort the whole sweep.
    remaining = set(CustomDomain.objects.filter(id__in=[d1.id, d2.id]).values_list("id", flat=True))
    assert len(remaining) == 1
    assert remaining <= {d1.id, d2.id}


def test_disable_for_application_only_touches_matching_domains(make_infra):
    infra = make_infra()
    service = CustomDomainService()
    app_id = uuid.uuid4()
    other_app_id = uuid.uuid4()
    domain, _token, _record = _claim(service, infra.user, infra, app_id=app_id)
    with patch("shared.resilience.http_client.ResilientHttpClient.get", return_value=_ok_app_lookup(infra.id)):
        other_domain = service.claim_domain(infra.user.id, infra.id, other_app_id, "other.example.com")[0]

    service.disable_for_application(infra.id, app_id)

    from api.models.custom_domain import CustomDomain
    assert not CustomDomain.objects.filter(id=domain.id).exists()  # PENDING -> hard deleted
    other_domain.refresh_from_db()
    assert other_domain.status == "PENDING"


def test_sweep_expired_claims_deletes_expired_pending_and_cert(make_infra):
    from datetime import timedelta

    from django.utils import timezone

    infra = make_infra()
    service = CustomDomainService()
    domain, _token, _record = _claim(service, infra.user, infra)
    cert_arn = domain.cert_arn
    domain.expires_at = timezone.now() - timedelta(seconds=1)
    domain.save(update_fields=["expires_at"])

    service.sweep_expired_claims()

    from api.models.custom_domain import CustomDomain
    assert not CustomDomain.objects.filter(id=domain.id).exists()
    assert cert_arn not in cert_bootstrap._shared_fake_acm()._certs


def test_sweep_expired_claims_leaves_non_expired_pending_alone(make_infra):
    infra = make_infra()
    service = CustomDomainService()
    domain, _token, _record = _claim(service, infra.user, infra)

    service.sweep_expired_claims()

    from api.models.custom_domain import CustomDomain
    assert CustomDomain.objects.filter(id=domain.id, status="PENDING").exists()


def test_sweep_expired_claims_continues_past_a_failure(make_infra):
    from datetime import timedelta

    from django.utils import timezone

    infra = make_infra()
    service = CustomDomainService()
    d1, _t1, _r1 = _claim(service, infra.user, infra)
    with patch("shared.resilience.http_client.ResilientHttpClient.get", return_value=_ok_app_lookup(infra.id)):
        d2, _t2, _r2 = service.claim_domain(infra.user.id, infra.id, uuid.uuid4(), "second.example.com")
    from api.models.custom_domain import CustomDomain
    CustomDomain.objects.filter(id__in=[d1.id, d2.id]).update(expires_at=timezone.now() - timedelta(seconds=1))

    with patch("api.services.custom_domain_service.custom_domain_cert.delete_certificate",
               side_effect=[RuntimeError("boom"), None]):
        service.sweep_expired_claims()

    remaining = set(CustomDomain.objects.filter(id__in=[d1.id, d2.id]).values_list("id", flat=True))
    assert len(remaining) == 1


def test_revalidate_resets_failure_count_on_success(make_infra):
    infra = make_infra()
    service = CustomDomainService()
    domain, token, _record = _claim(service, infra.user, infra)
    _publish_txt(domain, token)
    with patch("api.services.custom_domain_service.CustomDomainService._attach", return_value={}):
        service.verify_domain(infra.user.id, infra.id, domain.id)
    domain.verification_failure_count = 2
    domain.save(update_fields=["verification_failure_count"])

    service.revalidate_validated_domains()

    domain.refresh_from_db()
    assert domain.verification_failure_count == 0
    assert domain.status == "VALIDATED"


def test_revalidate_disables_after_three_consecutive_failures(make_infra):
    infra = make_infra()
    service = CustomDomainService()
    domain, token, _record = _claim(service, infra.user, infra)
    _publish_txt(domain, token)
    with patch("api.services.custom_domain_service.CustomDomainService._attach", return_value={}):
        service.verify_domain(infra.user.id, infra.id, domain.id)
    custom_domain_dns._shared_fake_resolver().clear_txt(domain.hostname)

    with patch("api.services.custom_domain_service.CustomDomainService._detach") as detach:
        for _ in range(3):
            service.revalidate_validated_domains()

    domain.refresh_from_db()
    assert domain.status == "DISABLED"
    detach.assert_called_once_with(domain.hostname)


def test_revalidate_does_not_disable_after_one_or_two_failures(make_infra):
    infra = make_infra()
    service = CustomDomainService()
    domain, token, _record = _claim(service, infra.user, infra)
    _publish_txt(domain, token)
    with patch("api.services.custom_domain_service.CustomDomainService._attach", return_value={}):
        service.verify_domain(infra.user.id, infra.id, domain.id)
    custom_domain_dns._shared_fake_resolver().clear_txt(domain.hostname)

    service.revalidate_validated_domains()
    service.revalidate_validated_domains()

    domain.refresh_from_db()
    assert domain.status == "VALIDATED"
    assert domain.verification_failure_count == 2
