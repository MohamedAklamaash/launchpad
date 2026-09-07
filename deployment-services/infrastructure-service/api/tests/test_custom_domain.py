"""CustomDomain ownership state machine: suffix rejection against punycode/mixed-case
spoofing, claim-then-validate exclusivity, and cross-tenant hostname races."""
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


def test_normalize_hostname_lowercases_and_decodes_punycode():
    import idna
    from api.models.custom_domain import normalize_hostname

    decoded_label = idna.decode("xn--nxasmq6b").lower()
    assert normalize_hostname("XN--nxasmq6b.Example.COM") == f"{decoded_label}.example.com"


def test_normalize_hostname_strips_trailing_dot():
    from api.models.custom_domain import normalize_hostname

    assert normalize_hostname("App.Example.com.") == "app.example.com"


def test_reserve_rejects_reserved_suffix_raw(make_infra):
    from api.models.custom_domain import CustomDomain, ReservedSuffixError

    with pytest.raises(ReservedSuffixError):
        CustomDomain.reserve("evil.launchpad.app", make_infra())


def test_reserve_rejects_reserved_suffix_apex(make_infra):
    from api.models.custom_domain import CustomDomain, ReservedSuffixError

    with pytest.raises(ReservedSuffixError):
        CustomDomain.reserve("launchpad.app", make_infra())


def test_reserve_rejects_reserved_suffix_fullwidth_homoglyph(make_infra):
    """Fullwidth Latin lookalikes (U+FF01-FF5E) aren't ASCII, so a naive raw endswith()
    check never matches — IDNA's UTS-46 compatibility mapping folds them down to plain
    ASCII 'launchpad.app', which is exactly what a naive check would miss."""
    from api.models.custom_domain import CustomDomain, ReservedSuffixError

    with pytest.raises(ReservedSuffixError):
        CustomDomain.reserve("evil.ｌａｕｎｃｈｐａｄ.ａｐｐ", make_infra())


def test_reserve_rejects_reserved_suffix_mixed_case(make_infra):
    from api.models.custom_domain import CustomDomain, ReservedSuffixError

    with pytest.raises(ReservedSuffixError):
        CustomDomain.reserve("Evil.LaunchPad.App", make_infra())


def test_reserve_allows_unrelated_hostname(make_infra):
    from api.models.custom_domain import CustomDomain

    domain = CustomDomain.reserve("app.example.com", make_infra())

    assert domain.status == "PENDING"
    assert domain.hostname == "app.example.com"


def test_multiple_pending_claims_same_hostname_coexist(make_infra):
    from api.models.custom_domain import CustomDomain

    first = CustomDomain.reserve("shared.example.com", make_infra())
    second = CustomDomain.reserve("shared.example.com", make_infra())

    assert first.status == second.status == "PENDING"
    assert first.pk != second.pk


def test_only_first_validation_wins_second_fails(make_infra):
    from api.models.custom_domain import CustomDomain, HostnameAlreadyValidatedError

    first = CustomDomain.reserve("shared.example.com", make_infra())
    second = CustomDomain.reserve("shared.example.com", make_infra())

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

    tenant_a_domain = CustomDomain.reserve("contested.example.com", make_infra())
    tenant_b_domain = CustomDomain.reserve("contested.example.com", make_infra())

    tenant_a_domain.mark_validated()

    with pytest.raises(HostnameAlreadyValidatedError):
        tenant_b_domain.mark_validated()

    assert CustomDomain.objects.filter(hostname="contested.example.com", status="VALIDATED").count() == 1


def test_pending_claim_expires_after_72h(make_infra):
    from api.models.custom_domain import CustomDomain

    domain = CustomDomain.reserve("stale.example.com", make_infra())
    assert not domain.is_expired

    domain.expires_at = timezone.now() - timedelta(seconds=1)
    domain.save(update_fields=["expires_at"])

    assert domain.is_expired


def test_mark_validated_rejects_expired_claim(make_infra):
    from api.models.custom_domain import CustomDomain, IllegalStatusTransitionError

    domain = CustomDomain.reserve("stale.example.com", make_infra())
    domain.expires_at = timezone.now() - timedelta(seconds=1)
    domain.save(update_fields=["expires_at"])

    with pytest.raises(IllegalStatusTransitionError):
        domain.mark_validated()


def test_mark_verification_failed_transitions_validated_to_disabled(make_infra):
    from api.models.custom_domain import CustomDomain

    domain = CustomDomain.reserve("app.example.com", make_infra())
    domain.mark_validated()

    domain.mark_verification_failed()

    assert domain.status == "DISABLED"


def test_mark_verification_failed_rejects_non_validated_row(make_infra):
    from api.models.custom_domain import CustomDomain, IllegalStatusTransitionError

    domain = CustomDomain.reserve("app.example.com", make_infra())

    with pytest.raises(IllegalStatusTransitionError):
        domain.mark_verification_failed()
