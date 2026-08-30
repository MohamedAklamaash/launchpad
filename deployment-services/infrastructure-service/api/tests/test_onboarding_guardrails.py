import sys
import types
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

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
    from api.models.environment import Environment
    from api.models.infrastructure import Infrastructure

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

@pytest.mark.parametrize("bad", ["abc", "123", "1234567890123", "111-111-1111", " 12345678901", "١٢٣٤٥٦٧٨٩٠١٢"])
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


def test_update_rejects_empty_code(make_infra):
    from api.services.infrastructure import InfrastructureService

    infra = make_infra(is_cloud_authenticated=False)
    with pytest.raises(ValueError):
        InfrastructureService().update_infrastructure_config(
            user_id=infra.user_id, infra_id=infra.id, update_data={"code": ""}
        )


def test_non_owner_cannot_change_code(make_infra, make_user):
    from api.services.infrastructure import InfrastructureService

    infra = make_infra(is_cloud_authenticated=False)
    outsider = make_user()
    infra.invited_users.add(outsider)
    with pytest.raises(PermissionError):
        InfrastructureService().update_infrastructure_config(
            user_id=outsider.id, infra_id=infra.id, update_data={"code": "222222222222"}
        )


# ---- terraform-var metadata validated at creation ----

def _create(user, metadata):
    from api.services.infrastructure import InfrastructureService

    return InfrastructureService().create_infrastructure(
        user_id=user.id,
        infra_data={
            "name": f"n-{uuid.uuid4()}", "cloud_provider": "AWS", "max_cpu": 1, "max_memory": 1,
            "code": "123456789012", "metadata": metadata,
        },
    )


@pytest.mark.parametrize("cidr", ["10.0.0.0/16", "172.31.0.0/20", "192.168.0.0/24"])
def test_create_accepts_well_formed_vpc_cidr(make_user, cidr):
    result = _create(make_user(), {"vpc_cidr": cidr, "aws_region": "us-east-1"})
    assert result["metadata"]["vpc_cidr"] == cidr


@pytest.mark.parametrize("region", ["us-east-1", "ap-southeast-2", "us-gov-west-1"])
def test_create_accepts_well_formed_aws_region(make_user, region):
    result = _create(make_user(), {"aws_region": region})
    assert result["metadata"]["aws_region"] == region


@pytest.mark.parametrize("cidr", [
    '10.0.0.0/16"\n}\nresource "null_resource" "x" {',
    "not-a-cidr",
    "10.0.0.1/16",  # host bits set — strict=True rejects
    "10.0.0.0/16 ",
    "",
])
def test_create_rejects_malformed_vpc_cidr(make_user, cidr):
    with pytest.raises(ValueError):
        _create(make_user(), {"vpc_cidr": cidr})


@pytest.mark.parametrize("region", [
    'us-west-2"; malicious {',
    "us-west-2\nprovider",
    "US-EAST-1",
    "useast1",
    "us-east-1x",
    "",
])
def test_create_rejects_malformed_aws_region(make_user, region):
    with pytest.raises(ValueError):
        _create(make_user(), {"aws_region": region})


def test_create_allows_metadata_without_terraform_vars(make_user):
    result = _create(make_user(), {"note": "no tf vars here"})
    assert result["metadata"]["note"] == "no tf vars here"


def test_rejected_metadata_leaves_no_infrastructure_row(make_user):
    from api.models.infrastructure import Infrastructure

    user = make_user()
    with pytest.raises(ValueError):
        _create(user, {"vpc_cidr": "not-a-cidr"})
    assert not Infrastructure.objects.filter(user_id=user.id).exists()


# ---- delete routes live AWS state through the async destroy ----

def _delete(infra, status):
    from api.models.environment import Environment
    from api.services.infrastructure import InfrastructureService

    Environment.objects.filter(infrastructure_id=infra.id).update(status=status)
    with patch("api.services.infrastructure.InfraQueue") as Q, \
            patch("api.messaging.producer.producer.infra_producer"):
        Q.enqueue_destroy.return_value = True
        result = InfrastructureService().delete_infrastructure(infra.user_id, infra.id)
    return result, Q


def test_delete_active_enqueues_destroy_and_keeps_rows(make_infra):
    from api.models.environment import Environment
    from api.models.infrastructure import Infrastructure

    infra = make_infra(is_cloud_authenticated=True)
    result, Q = _delete(infra, "ACTIVE")

    assert result is True
    Q.enqueue_destroy.assert_called_once_with(str(infra.id))
    assert Infrastructure.objects.filter(id=infra.id).exists()
    env = Environment.objects.get(infrastructure_id=infra.id)
    assert env.status == "DESTROYING"


@pytest.mark.parametrize("onboarded,stamp_activated", [(True, False), (False, True)])
def test_delete_error_after_real_apply_enqueues_destroy(make_infra, onboarded, stamp_activated):
    """ERROR after onboarding or a prior activation must async-destroy, not orphan resources."""
    from api.models.environment import Environment
    from api.models.infrastructure import Infrastructure
    from django.utils import timezone

    infra = make_infra(is_cloud_authenticated=onboarded)
    if stamp_activated:
        Environment.objects.filter(infrastructure_id=infra.id).update(first_activated_at=timezone.now())
    result, Q = _delete(infra, "ERROR")

    assert result is True
    Q.enqueue_destroy.assert_called_once_with(str(infra.id))
    assert Infrastructure.objects.filter(id=infra.id).exists()
    env = Environment.objects.get(infrastructure_id=infra.id)
    assert env.status == "DESTROYING"


def test_delete_error_before_any_apply_deletes_immediately(make_infra):
    """A never-onboarded, never-activated ERROR row has nothing in AWS to destroy."""
    from api.models.environment import Environment
    from api.models.infrastructure import Infrastructure

    infra = make_infra(is_cloud_authenticated=False)
    result, Q = _delete(infra, "ERROR")

    assert result is True
    Q.enqueue_destroy.assert_not_called()
    assert not Infrastructure.objects.filter(id=infra.id).exists()
    assert not Environment.objects.filter(infrastructure_id=infra.id).exists()


@pytest.mark.parametrize("status", ["PENDING", "DESTROYED"])
def test_delete_removes_rows_immediately_without_destroy(make_infra, status):
    from api.models.environment import Environment
    from api.models.infrastructure import Infrastructure

    infra = make_infra()
    result, Q = _delete(infra, status)

    assert result is True
    Q.enqueue_destroy.assert_not_called()
    assert not Infrastructure.objects.filter(id=infra.id).exists()
    assert not Environment.objects.filter(infrastructure_id=infra.id).exists()


@pytest.mark.parametrize("status", ["PROVISIONING", "UPDATING"])
def test_delete_blocks_while_operation_in_flight(make_infra, status):
    infra = make_infra()
    with pytest.raises(ValueError):
        _delete(infra, status)
