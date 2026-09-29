"""The customer's create_aws_role.sh trust policy must name the real Launchpad
platform AWS account/user as principal. The script used to fall back to a stale
hardcoded default when the dashboard didn't inject one, producing
`MalformedPolicyDocument: Invalid principal`. These tests cover the fix: the
platform account id / user are derived once from LAUNCHPAD_PLATFORM_PRINCIPAL_ARN
and surfaced on every infrastructure response so the dashboard can inject them.
"""

import uuid

import pytest
from api.cloud_providers.aws.platform_principal import platform_account_and_user
from api.views.infrastructure import infrastructure_detail, infrastructure_list_create
from rest_framework.test import APIRequestFactory, force_authenticate

# test_settings.py pins LAUNCHPAD_PLATFORM_PRINCIPAL_ARN to this value.
EXPECTED_ACCOUNT_ID = "210987654321"
EXPECTED_USER = "launchpad-platform"


def test_platform_account_and_user_parses_the_configured_arn():
    account_id, user = platform_account_and_user()
    assert account_id == EXPECTED_ACCOUNT_ID
    assert user == EXPECTED_USER


def test_platform_account_and_user_rejects_a_malformed_arn(settings):
    settings.LAUNCHPAD_PLATFORM_PRINCIPAL_ARN = "arn:aws:iam::210987654321:role/not-a-user"
    with pytest.raises(ValueError, match="LAUNCHPAD_PLATFORM_PRINCIPAL_ARN"):
        platform_account_and_user()


@pytest.fixture
def owner(db):
    from api.models.user import User

    return User.objects.create(
        id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="t", role="super_admin",
    )


@pytest.fixture
def make_infra(db, owner):
    from api.models.infrastructure import Infrastructure

    def _make(**kwargs):
        return Infrastructure.objects.create(
            user=owner, name=f"infra-{uuid.uuid4()}", cloud_provider="aws",
            max_cpu=1024, max_memory=512, code="123456789012", metadata={},
            **kwargs,
        )

    return _make


def test_serializer_exposes_the_platform_principal(make_infra):
    from api.serializers.infrastructure import InfrastructureSerializer

    data = InfrastructureSerializer.serialize_instance(make_infra())
    assert data["platform_account_id"] == EXPECTED_ACCOUNT_ID
    assert data["platform_user"] == EXPECTED_USER


@pytest.mark.django_db
def test_create_response_includes_the_platform_principal(owner):
    body = {
        "name": "prod-infra", "cloud_provider": "AWS", "code": "123456789012",
        "max_cpu": 4, "max_memory": 8,
    }
    request = APIRequestFactory().post("/api/v1/infrastructures/", body, format="json")
    force_authenticate(request, user=owner)
    response = infrastructure_list_create(request)

    assert response.status_code == 201
    assert response.data["platform_account_id"] == EXPECTED_ACCOUNT_ID
    assert response.data["platform_user"] == EXPECTED_USER


def test_detail_response_includes_the_platform_principal(make_infra, owner):
    infra = make_infra()
    request = APIRequestFactory().get(f"/api/v1/infrastructures/{infra.id}/")
    force_authenticate(request, user=owner)
    response = infrastructure_detail(request, infra_id=str(infra.id))

    assert response.status_code == 200
    assert response.data["platform_account_id"] == EXPECTED_ACCOUNT_ID
    assert response.data["platform_user"] == EXPECTED_USER
