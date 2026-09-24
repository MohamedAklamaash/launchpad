"""Managed-database CRUD API: validation, cross-tenant isolation, precheck denial,
and the no-credential-shaped-field invariant on serialized rows."""
import uuid
from unittest.mock import MagicMock

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate


@pytest.fixture(autouse=True)
def _stub_infra_queue(monkeypatch):
    """database_service/infrastructure import InfraQueue at module load time, so
    patching sys.modules after that first import is a no-op — patch the
    already-bound names directly instead. The real InfraQueue opens Redis
    connection pools, which isn't available in the test environment."""
    fake = MagicMock()
    monkeypatch.setattr("api.services.database_service.InfraQueue", fake)
    monkeypatch.setattr("api.services.infrastructure.InfraQueue", fake)
    return fake


@pytest.fixture(autouse=True)
def _skip_iam_precheck(monkeypatch):
    """Most tests aren't about the IAM precheck itself — short-circuit it so create
    doesn't need a live/mocked boto3 IAM client."""
    monkeypatch.setattr("api.services.database_service.precheck_database_create", lambda infra, engine: None)


@pytest.fixture
def factory():
    return APIRequestFactory()


@pytest.fixture
def make_user(db):
    from api.models.user import User

    def _make():
        return User.objects.create(
            id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="t", role="super_admin",
        )
    return _make


@pytest.fixture
def make_infra_env(db, make_user):
    from api.models.environment import Environment
    from api.models.infrastructure import Infrastructure

    def _make(*, owner=None, env_status="ACTIVE"):
        owner = owner or make_user()
        infra = Infrastructure.objects.create(
            user=owner, name=f"infra-{uuid.uuid4()}", cloud_provider="aws",
            max_cpu=1024, max_memory=512, code="123456789012", metadata={},
        )
        env = Environment.objects.create(
            infrastructure=infra, status=env_status, vpc_id="vpc-abc123",
        )
        return owner, infra, env
    return _make


def _create(factory, view, user, infra_id, **payload):
    request = factory.post(f"/api/v1/infrastructures/{infra_id}/databases/", payload, format="json")
    force_authenticate(request, user=user)
    return view(request, infra_id=infra_id)


def _list(factory, view, user, infra_id):
    request = factory.get(f"/api/v1/infrastructures/{infra_id}/databases/")
    force_authenticate(request, user=user)
    return view(request, infra_id=infra_id)


def _get(factory, view, user, infra_id, database_id):
    request = factory.get(f"/api/v1/infrastructures/{infra_id}/databases/{database_id}/")
    force_authenticate(request, user=user)
    return view(request, infra_id=infra_id, database_id=database_id)


def _delete(factory, view, user, infra_id, database_id, confirm_name=""):
    request = factory.delete(
        f"/api/v1/infrastructures/{infra_id}/databases/{database_id}/",
        {"confirm_name": confirm_name}, format="json",
    )
    force_authenticate(request, user=user)
    return view(request, infra_id=infra_id, database_id=database_id)


VALID_CREATE = {
    "name": "primary-db",
    "engine": "postgres",
    "engine_version": "16.6",
    "instance_class": "db.t3.micro",
    "allocated_storage": 20,
}


# ── create validation ───────────────────────────────────────────────────────────

def test_create_requires_active_environment(factory, make_infra_env):
    from api.views.database import database_list_create

    owner, infra, _env = make_infra_env(env_status="PROVISIONING")
    resp = _create(factory, database_list_create, owner, str(infra.id), **VALID_CREATE)
    assert resp.status_code == 400
    assert "ACTIVE" in resp.data["error"]


def test_create_rejects_invalid_name(factory, make_infra_env):
    from api.views.database import database_list_create

    owner, infra, _env = make_infra_env()
    bad = {**VALID_CREATE, "name": "Not_Valid!"}
    resp = _create(factory, database_list_create, owner, str(infra.id), **bad)
    assert resp.status_code == 400


def test_create_rejects_unsupported_engine_version(factory, make_infra_env):
    from api.views.database import database_list_create

    owner, infra, _env = make_infra_env()
    bad = {**VALID_CREATE, "engine_version": "9.9.9"}
    resp = _create(factory, database_list_create, owner, str(infra.id), **bad)
    assert resp.status_code == 400


def test_create_rejects_unsupported_instance_class(factory, make_infra_env):
    from api.views.database import database_list_create

    owner, infra, _env = make_infra_env()
    bad = {**VALID_CREATE, "instance_class": "db.m9.giant"}
    resp = _create(factory, database_list_create, owner, str(infra.id), **bad)
    assert resp.status_code == 400


def test_create_rejects_storage_out_of_bounds(factory, make_infra_env):
    from api.views.database import database_list_create

    owner, infra, _env = make_infra_env()
    bad = {**VALID_CREATE, "allocated_storage": 5}
    resp = _create(factory, database_list_create, owner, str(infra.id), **bad)
    assert resp.status_code == 400


def test_create_redis_does_not_require_allocated_storage(factory, make_infra_env):
    from api.views.database import database_list_create

    owner, infra, _env = make_infra_env()
    payload = {
        "name": "cache-1", "engine": "redis", "engine_version": "7.1", "instance_class": "cache.t3.micro",
    }
    resp = _create(factory, database_list_create, owner, str(infra.id), **payload)
    assert resp.status_code == 202
    assert resp.data["allocated_storage"] is None


def test_create_rejects_duplicate_name(factory, make_infra_env):
    from api.views.database import database_list_create

    owner, infra, _env = make_infra_env()
    _create(factory, database_list_create, owner, str(infra.id), **VALID_CREATE)
    resp = _create(factory, database_list_create, owner, str(infra.id), **VALID_CREATE)
    assert resp.status_code == 400


def test_create_enforces_quota(factory, make_infra_env, settings):
    from api.views.database import database_list_create

    owner, infra, _env = make_infra_env()
    settings.MAX_DATABASES_PER_INFRA = 1
    ok = _create(factory, database_list_create, owner, str(infra.id), **VALID_CREATE)
    assert ok.status_code == 202
    over = _create(factory, database_list_create, owner, str(infra.id), **{**VALID_CREATE, "name": "second-db"})
    assert over.status_code == 400
    assert "quota" in over.data["error"].lower()


def test_create_success_enqueues_provision_on_commit(factory, make_infra_env, django_capture_on_commit_callbacks, _stub_infra_queue):
    from api.views.database import database_list_create

    owner, infra, _env = make_infra_env()
    with django_capture_on_commit_callbacks(execute=True):
        resp = _create(factory, database_list_create, owner, str(infra.id), **VALID_CREATE)
    assert resp.status_code == 202
    assert resp.data["status"] == "PENDING"
    _stub_infra_queue.enqueue_provision.assert_called_once_with(str(infra.id))


def test_create_returns_422_on_policy_refresh_required(factory, make_infra_env, monkeypatch):
    from api.cloud_providers.aws.iam_precheck import PolicyRefreshRequired
    from api.views.database import database_list_create

    owner, infra, _env = make_infra_env()

    def _deny(infra_arg, engine):
        raise PolicyRefreshRequired(["rds:CreateDBInstance"])

    monkeypatch.setattr("api.services.database_service.precheck_database_create", _deny)
    resp = _create(factory, database_list_create, owner, str(infra.id), **VALID_CREATE)
    assert resp.status_code == 422
    assert resp.data["code"] == "policy_refresh_required"
    assert "rds:CreateDBInstance" in resp.data["denied_actions"]


# ── tenant isolation ─────────────────────────────────────────────────────────────

def test_create_is_owner_only(factory, make_infra_env, make_user):
    from api.views.database import database_list_create

    _owner, infra, _env = make_infra_env()
    other = make_user()
    infra.invited_users.add(other)
    resp = _create(factory, database_list_create, other, str(infra.id), **VALID_CREATE)
    assert resp.status_code == 403


def test_invited_member_can_list_and_view(factory, make_infra_env, make_user):
    from api.views.database import database_detail, database_list_create

    owner, infra, _env = make_infra_env()
    create_resp = _create(factory, database_list_create, owner, str(infra.id), **VALID_CREATE)
    db_id = create_resp.data["id"]

    other = make_user()
    infra.invited_users.add(other)

    list_resp = _list(factory, database_list_create, other, str(infra.id))
    assert list_resp.status_code == 200
    assert len(list_resp.data) == 1

    get_resp = _get(factory, database_detail, other, str(infra.id), db_id)
    assert get_resp.status_code == 200


def test_cross_tenant_get_returns_404(factory, make_infra_env, make_user):
    from api.views.database import database_detail, database_list_create

    owner, infra, _env = make_infra_env()
    create_resp = _create(factory, database_list_create, owner, str(infra.id), **VALID_CREATE)
    db_id = create_resp.data["id"]

    stranger = make_user()  # not owner, not invited
    resp = _get(factory, database_detail, stranger, str(infra.id), db_id)
    assert resp.status_code == 404


def test_cross_tenant_delete_returns_404(factory, make_infra_env, make_user):
    from api.views.database import database_detail, database_list_create

    owner, infra, _env = make_infra_env()
    create_resp = _create(factory, database_list_create, owner, str(infra.id), **VALID_CREATE)
    db_id = create_resp.data["id"]

    stranger = make_user()
    resp = _delete(factory, database_detail, stranger, str(infra.id), db_id, confirm_name="primary-db")
    assert resp.status_code == 404


# ── delete ────────────────────────────────────────────────────────────────────

def test_delete_requires_matching_confirm_name(factory, make_infra_env):
    from api.views.database import database_detail, database_list_create

    owner, infra, _env = make_infra_env()
    create_resp = _create(factory, database_list_create, owner, str(infra.id), **VALID_CREATE)
    db_id = create_resp.data["id"]

    resp = _delete(factory, database_detail, owner, str(infra.id), db_id, confirm_name="wrong-name")
    assert resp.status_code == 400


def test_delete_from_error_succeeds(factory, make_infra_env):
    from api.models.database import Database
    from api.views.database import database_detail, database_list_create

    owner, infra, _env = make_infra_env()
    create_resp = _create(factory, database_list_create, owner, str(infra.id), **VALID_CREATE)
    db_id = create_resp.data["id"]
    Database.objects.filter(id=db_id).update(status="ERROR")

    resp = _delete(factory, database_detail, owner, str(infra.id), db_id, confirm_name="primary-db")
    assert resp.status_code == 202
    assert resp.data["status"] == "DELETING"


def test_delete_already_in_progress_rejected(factory, make_infra_env):
    from api.models.database import Database
    from api.views.database import database_detail, database_list_create

    owner, infra, _env = make_infra_env()
    create_resp = _create(factory, database_list_create, owner, str(infra.id), **VALID_CREATE)
    db_id = create_resp.data["id"]
    Database.objects.filter(id=db_id).update(status="DELETING")

    resp = _delete(factory, database_detail, owner, str(infra.id), db_id, confirm_name="primary-db")
    assert resp.status_code == 400


# ── credential-shape invariant (goal 2 of the plan) ─────────────────────────────

_CREDENTIAL_KEY_MARKERS = ("password", "secret_value", "access_key", "session_token", "auth_token")


def test_serialized_database_has_no_credential_shaped_field(factory, make_infra_env):
    from api.views.database import database_list_create

    owner, infra, _env = make_infra_env()
    resp = _create(factory, database_list_create, owner, str(infra.id), **VALID_CREATE)
    assert resp.status_code == 202
    for key in resp.data:
        assert not any(marker in key.lower() for marker in _CREDENTIAL_KEY_MARKERS), key
    # secret_arn is the one allowed pointer field, and it must be an ARN shape or null.
    assert resp.data["secret_arn"] is None or str(resp.data["secret_arn"]).startswith("arn:")


def test_destroy_infra_refused_with_live_database(factory, make_infra_env, django_capture_on_commit_callbacks):
    from api.services.infrastructure import InfrastructureService
    from api.views.database import database_list_create

    owner, infra, _env = make_infra_env()
    with django_capture_on_commit_callbacks(execute=True):
        _create(factory, database_list_create, owner, str(infra.id), **VALID_CREATE)

    with pytest.raises(ValueError, match="database"):
        InfrastructureService().delete_infrastructure(user_id=owner.id, infra_id=infra.id)


def test_create_rejects_eks_infrastructure(factory, make_infra_env):
    """Managed databases are ECS-only: the EKS config builder emits no database module
    and EKS apps have no path to a Secrets Manager credential, so a database row on an
    EKS infra could never reconcile. Refuse at create rather than stranding the row."""
    from api.views.database import database_list_create

    owner, infra, _env = make_infra_env()
    infra.compute_type = "eks"
    infra.save(update_fields=["compute_type"])

    resp = _create(factory, database_list_create, owner, str(infra.id), **VALID_CREATE)
    assert resp.status_code == 400
    assert "ecs_fargate" in resp.data["error"]
