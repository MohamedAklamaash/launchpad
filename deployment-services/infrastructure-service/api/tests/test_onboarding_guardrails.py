import sys
import types
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate


@pytest.fixture(autouse=True)
def _stub_infra_queue():
    previous = sys.modules.get("api.services.infra_queue")
    fake = types.ModuleType("api.services.infra_queue")
    fake.InfraQueue = MagicMock()
    sys.modules["api.services.infra_queue"] = fake
    try:
        yield fake.InfraQueue
    finally:
        if previous is not None:
            sys.modules["api.services.infra_queue"] = previous
        else:
            sys.modules.pop("api.services.infra_queue", None)


@pytest.fixture
def make_user(db):
    from api.models.user import User

    def _make():
        return User.objects.create(
            id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="t", role="super_admin"
        )

    return _make


@pytest.fixture
def make_infra(db, make_user):
    from api.models.infrastructure import Infrastructure
    from api.models.environment import Environment

    def _make(*, is_cloud_authenticated=False, code="123456789012"):
        infra = Infrastructure.objects.create(
            user=make_user(), name=f"infra-{uuid.uuid4()}", cloud_provider="aws",
            max_cpu=1024, max_memory=512, code=code,
            is_cloud_authenticated=is_cloud_authenticated,
        )
        Environment.objects.create(infrastructure=infra, status="PENDING")
        return infra

    return _make


def _reprovision(infra):
    from api.views.infrastructure import infrastructure_reprovision

    req = APIRequestFactory().post(f"/api/v1/infrastructures/{infra.id}/reprovision/")
    force_authenticate(req, user=SimpleNamespace(id=str(infra.user_id), is_authenticated=True))
    return infrastructure_reprovision(req, infra_id=str(infra.id))


# ---- reprovision onboarding gate ----

def test_reprovision_blocked_when_not_onboarded(make_infra, _stub_infra_queue):
    infra = make_infra(is_cloud_authenticated=False)
    resp = _reprovision(infra)
    assert resp.status_code == 409
    _stub_infra_queue.enqueue_provision.assert_not_called()


def test_reprovision_allowed_when_onboarded(make_infra, _stub_infra_queue):
    infra = make_infra(is_cloud_authenticated=True)
    resp = _reprovision(infra)
    assert resp.status_code == 202
    _stub_infra_queue.enqueue_provision.assert_called_once()


# ---- account id validation ----

@pytest.mark.parametrize("bad", ["abc", "123", "1234567890123", "111-111-1111", " 12345678901"])
def test_create_rejects_malformed_account_id(make_user, bad):
    from api.services.infrastructure import InfrastructureService

    user = make_user()
    with pytest.raises(ValueError):
        InfrastructureService().create_infrastructure(
            user_id=user.id,
            infra_data={"name": "n", "cloud_provider": "AWS", "max_cpu": 1, "max_memory": 1, "code": bad},
        )


def test_create_accepts_and_normalizes_12_digit(make_user):
    from api.services.infrastructure import InfrastructureService

    user = make_user()
    result = InfrastructureService().create_infrastructure(
        user_id=user.id,
        infra_data={"name": "n", "cloud_provider": "AWS", "max_cpu": 1, "max_memory": 1, "code": " 123456789012 "},
    )
    assert result["code"] == "123456789012"


# ---- code echoed in the response ----

def test_response_includes_code(make_infra):
    from api.serializers.infrastructure import InfrastructureSerializer

    infra = make_infra(code="123456789012")
    data = InfrastructureSerializer.serialize_instance(infra)
    assert data["code"] == "123456789012"


# ---- code correctable only before onboarding ----

def test_code_correctable_before_onboarding(make_infra):
    from api.services.infrastructure import InfrastructureService

    infra = make_infra(is_cloud_authenticated=False, code="111111111111")
    updated = InfrastructureService().update_infrastructure_config(
        user_id=infra.user_id, infra_id=infra.id, update_data={"code": "222222222222"}
    )
    assert updated["code"] == "222222222222"


def test_code_locked_after_onboarding(make_infra):
    from api.services.infrastructure import InfrastructureService

    infra = make_infra(is_cloud_authenticated=True, code="111111111111")
    with pytest.raises(ValueError):
        InfrastructureService().update_infrastructure_config(
            user_id=infra.user_id, infra_id=infra.id, update_data={"code": "222222222222"}
        )


def test_update_rejects_malformed_code_before_onboarding(make_infra):
    from api.services.infrastructure import InfrastructureService

    infra = make_infra(is_cloud_authenticated=False)
    with pytest.raises(ValueError):
        InfrastructureService().update_infrastructure_config(
            user_id=infra.user_id, infra_id=infra.id, update_data={"code": "not-an-account"}
        )
