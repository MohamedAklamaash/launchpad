"""CustomDomain ownership state machine: suffix rejection against punycode/mixed-case
spoofing, hostname syntax hardening, claim-then-validate exclusivity, ownership tokens,
and cross-tenant hostname races."""
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone


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

    def _make(user=None):
        return Infrastructure.objects.create(
            user=user or make_user(), name=f"infra-{uuid.uuid4()}", cloud_provider="aws",
            max_cpu=1024, max_memory=512, code="123456789012",
        )

    return _make


def reserve(hostname, infra, application_id=None):
    from api.models.custom_domain import CustomDomain

    domain, _token = CustomDomain.reserve(hostname, infra, application_id or uuid.uuid4())
    return domain


def test_normalize_hostname_lowercases_and_punycode_encodes():
    from api.models.custom_domain import normalize_hostname

    assert normalize_hostname("XN--nxasmq6b.Example.COM") == "xn--nxasmq6b.example.com"


def test_normalize_hostname_strips_trailing_dot():
    from api.models.custom_domain import normalize_hostname

    assert normalize_hostname("App.Example.com.") == "app.example.com"


def test_normalize_hostname_rejects_empty_label():
    from api.models.custom_domain import InvalidHostnameError, normalize_hostname

    with pytest.raises(InvalidHostnameError):
        normalize_hostname("app..example.com")


@pytest.mark.parametrize("raw", [
    "not_ldh_.example.com",  # underscore
    "-leadinghyphen.example.com",
    "trailinghyphen-.example.com",
    "onelabel",
    "*.example.com",
    "a" * 64 + ".example.com",  # label too long
    ".".join(["a"] * 130),  # total too long
    "1.2.3.4",
    "[::1].example.com",
])
def test_validate_hostname_syntax_rejects(raw):
    from api.models.custom_domain import (
        InvalidHostnameError,
        normalize_hostname,
        validate_hostname_syntax,
    )

    try:
        normalized = normalize_hostname(raw)
    except InvalidHostnameError:
        return  # rejected at normalization already — syntax check never even runs
    with pytest.raises(InvalidHostnameError):
        validate_hostname_syntax(normalized)


def test_validate_hostname_syntax_rejects_ipv4_literal():
    from api.models.custom_domain import InvalidHostnameError, validate_hostname_syntax

    with pytest.raises(InvalidHostnameError):
        validate_hostname_syntax("1.2.3.4")


@pytest.mark.parametrize("raw", [
    "app.example.com",
    "a-b.example.com",
    "xn--nxasmq6b.example.com",
    "a.b.c.example.co",
])
def test_validate_hostname_syntax_accepts(raw):
    from api.models.custom_domain import normalize_hostname, validate_hostname_syntax

    validate_hostname_syntax(normalize_hostname(raw))


def test_reserve_rejects_reserved_suffix_raw(make_infra):
    from api.models.custom_domain import CustomDomain, ReservedSuffixError

    with pytest.raises(ReservedSuffixError):
        CustomDomain.reserve("evil.launchpad.app", make_infra(), uuid.uuid4())


def test_reserve_rejects_reserved_suffix_apex(make_infra):
    from api.models.custom_domain import CustomDomain, ReservedSuffixError

    with pytest.raises(ReservedSuffixError):
        CustomDomain.reserve("launchpad.app", make_infra(), uuid.uuid4())


def test_reserve_rejects_reserved_suffix_fullwidth_homoglyph(make_infra):
    """Fullwidth Latin lookalikes (U+FF01-FF5E) aren't ASCII, so a naive raw endswith()
    check never matches — IDNA's UTS-46 compatibility mapping folds them down to plain
    ASCII 'launchpad.app', which is exactly what a naive check would miss."""
    from api.models.custom_domain import CustomDomain, ReservedSuffixError

    with pytest.raises(ReservedSuffixError):
        CustomDomain.reserve("evil.ｌａｕｎｃｈｐａｄ.ａｐｐ", make_infra(), uuid.uuid4())


def test_reserve_rejects_reserved_suffix_mixed_case(make_infra):
    from api.models.custom_domain import CustomDomain, ReservedSuffixError

    with pytest.raises(ReservedSuffixError):
        CustomDomain.reserve("Evil.LaunchPad.App", make_infra(), uuid.uuid4())


def test_reserve_rejects_invalid_syntax(make_infra):
    from api.models.custom_domain import CustomDomain, InvalidHostnameError

    with pytest.raises(InvalidHostnameError):
        CustomDomain.reserve("not_ldh_.example.com", make_infra(), uuid.uuid4())


def test_reserve_allows_unrelated_hostname(make_infra):
    domain = reserve("app.example.com", make_infra())

    assert domain.status == "PENDING"
    assert domain.hostname == "app.example.com"


def test_reserve_returns_plaintext_token_once_and_stores_only_hash(make_infra):
    from api.models.custom_domain import CustomDomain

    domain, token = CustomDomain.reserve("app.example.com", make_infra(), uuid.uuid4())

    assert token
    assert domain.ownership_token_hash
    assert domain.ownership_token_hash != token
    assert domain.check_ownership_token(token) is True
    assert domain.check_ownership_token("wrong-token") is False


def test_reserve_binds_application_id(make_infra):
    from api.models.custom_domain import CustomDomain

    app_id = uuid.uuid4()
    domain, _ = CustomDomain.reserve("app.example.com", make_infra(), app_id)

    assert domain.application_id == app_id


def test_multiple_pending_claims_same_hostname_coexist(make_infra):
    first = reserve("shared.example.com", make_infra())
    second = reserve("shared.example.com", make_infra())

    assert first.status == second.status == "PENDING"
    assert first.pk != second.pk


def test_only_first_validation_wins_second_fails(make_infra):
    from api.models.custom_domain import HostnameAlreadyValidatedError

    first = reserve("shared.example.com", make_infra())
    second = reserve("shared.example.com", make_infra())

    first.mark_validated()
    assert first.status == "VALIDATED"

    with pytest.raises(HostnameAlreadyValidatedError):
        second.mark_validated()

    second.refresh_from_db()
    assert second.status == "PENDING"


def test_cross_tenant_hostname_claim_concurrent_validation(make_infra):
    """Two different users' infrastructures both claim the same hostname; only one can
    ever validate. Under the sqlite test backend select_for_update() is a no-op, so this
    proves the DB-level partial UniqueConstraint holds — not the row lock itself, which is
    only meaningfully exercised under Postgres."""
    from api.models.custom_domain import CustomDomain, HostnameAlreadyValidatedError

    tenant_a_domain = reserve("contested.example.com", make_infra())
    tenant_b_domain = reserve("contested.example.com", make_infra())

    tenant_a_domain.mark_validated()

    with pytest.raises(HostnameAlreadyValidatedError):
        tenant_b_domain.mark_validated()

    assert CustomDomain.objects.filter(hostname="contested.example.com", status="VALIDATED").count() == 1


def test_pending_claim_expires_after_72h(make_infra):
    domain = reserve("stale.example.com", make_infra())
    assert not domain.is_expired

    domain.expires_at = timezone.now() - timedelta(seconds=1)
    domain.save(update_fields=["expires_at"])

    assert domain.is_expired


def test_mark_validated_rejects_expired_claim(make_infra):
    from api.models.custom_domain import IllegalStatusTransitionError

    domain = reserve("stale.example.com", make_infra())
    domain.expires_at = timezone.now() - timedelta(seconds=1)
    domain.save(update_fields=["expires_at"])

    with pytest.raises(IllegalStatusTransitionError):
        domain.mark_validated()


def test_mark_validated_rechecks_locked_row_not_stale_in_memory_object(make_infra):
    """H4: a caller holding a stale in-memory copy (already validated, or expired, by the
    time this call's lock is acquired) must not blindly overwrite the row — the re-check
    has to run on the row select_for_update() returns, not on `self`."""
    from api.models.custom_domain import CustomDomain, IllegalStatusTransitionError

    domain = reserve("stale.example.com", make_infra())
    stale_copy = CustomDomain.objects.get(pk=domain.pk)

    # The row expires after `stale_copy` was read into memory.
    CustomDomain.objects.filter(pk=domain.pk).update(
        expires_at=timezone.now() - timedelta(seconds=1),
    )

    with pytest.raises(IllegalStatusTransitionError):
        stale_copy.mark_validated()

    domain.refresh_from_db()
    assert domain.status == "PENDING"


def test_mark_validated_resets_failure_count(make_infra):
    domain = reserve("app.example.com", make_infra())
    domain.verification_failure_count = 2
    domain.save(update_fields=["verification_failure_count"])

    domain.mark_validated()

    assert domain.verification_failure_count == 0


def test_mark_verification_failed_transitions_validated_to_disabled(make_infra):
    domain = reserve("app.example.com", make_infra())
    domain.mark_validated()

    domain.mark_verification_failed()

    assert domain.status == "DISABLED"


def test_mark_verification_failed_rejects_non_validated_row(make_infra):
    from api.models.custom_domain import IllegalStatusTransitionError

    domain = reserve("app.example.com", make_infra())

    with pytest.raises(IllegalStatusTransitionError):
        domain.mark_verification_failed()


def test_record_verification_failure_disables_after_three_consecutive(make_infra):
    from api.models.custom_domain import MAX_CONSECUTIVE_VERIFICATION_FAILURES

    domain = reserve("app.example.com", make_infra())
    domain.mark_validated()

    should_disable = False
    for _ in range(MAX_CONSECUTIVE_VERIFICATION_FAILURES):
        should_disable = domain.record_verification_failure()

    assert should_disable is True
    assert domain.verification_failure_count == MAX_CONSECUTIVE_VERIFICATION_FAILURES


def test_record_verification_success_resets_counter(make_infra):
    domain = reserve("app.example.com", make_infra())
    domain.mark_validated()
    domain.record_verification_failure()
    domain.record_verification_failure()

    domain.record_verification_success()

    domain.refresh_from_db()
    assert domain.verification_failure_count == 0
