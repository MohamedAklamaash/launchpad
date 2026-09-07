"""The dashboard reads this to decide which compute targets to offer, so it must track
EKS_ENABLED exactly — the flag is enforced at create and at provision dispatch, and an
option the server will reject should never be selectable."""
import pytest
from api.views.capabilities import list_capabilities
from rest_framework.test import APIRequestFactory, force_authenticate


def _compute_types(settings_obj, eks_enabled, user):
    settings_obj.EKS_ENABLED = eks_enabled
    request = APIRequestFactory().get("/api/v1/capabilities/")
    force_authenticate(request, user=user)
    response = list_capabilities(request)
    assert response.status_code == 200
    return {entry["value"]: entry["enabled"] for entry in response.data["compute_types"]}


@pytest.fixture
def user(db):
    import uuid

    from api.models.user import User

    return User.objects.create(
        id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="t", role="super_admin",
    )


def test_eks_is_offered_when_enabled(settings, user):
    assert _compute_types(settings, True, user) == {"ecs_fargate": True, "eks": True}


def test_eks_is_not_offered_when_disabled(settings, user):
    assert _compute_types(settings, False, user) == {"ecs_fargate": True, "eks": False}


def test_ecs_is_always_offered(settings, user):
    for flag in (True, False):
        assert _compute_types(settings, flag, user)["ecs_fargate"] is True


def test_capabilities_is_not_auth_exempt(settings):
    """A platform-configuration read must not be reachable anonymously the way the static
    AWS region list is."""
    exempt = list(getattr(settings, "INTERNAL_AUTH_EXEMPT_PATHS", []))
    exempt += list(getattr(settings, "INTERNAL_AUTH_EXEMPT_PREFIXES", []))
    assert not any("capabilities" in path for path in exempt)
