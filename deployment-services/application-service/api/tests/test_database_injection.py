"""ECS `secrets` builder for attached databases, and attach-time validation in
update_application: same-infra ACTIVE only, injected names win over application.envs."""
import uuid

import pytest


@pytest.fixture
def make_infra_env(schema_db):
    from api.models.environment import Environment
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    def _make():
        user = User.objects.create(
            id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="t",
        )
        infra = Infrastructure.objects.create(
            id=uuid.uuid4(), user=user, name=f"infra-{uuid.uuid4()}", cloud_provider="aws",
            max_cpu=1024, max_memory=512,
        )
        env = Environment.objects.create(id=uuid.uuid4(), infrastructure=infra)
        return user, infra, env
    return _make


@pytest.fixture
def make_db_row(schema_db):
    from api.models.database import Database

    def _make(env, *, name="primary-db", engine="postgres", status="ACTIVE",
              host="db.example.com", port=5432,
              secret_arn="arn:aws:secretsmanager:us-east-1:123456789012:secret:x"):
        return Database.objects.create(
            id=uuid.uuid4(), environment=env, name=name, engine=engine, status=status,
            host=host, port=port, secret_arn=secret_arn,
        )
    return _make


@pytest.fixture
def make_app(schema_db):
    from api.models.application import Application

    def _make(user, infra, *, attached_database_ids=None, envs=None):
        return Application.objects.create(
            id=uuid.uuid4(), user=user, infrastructure=infra, name="my-app",
            project_remote_url="https://github.com/x/y", project_branch="main",
            project_commit_hash="", attached_database_ids=attached_database_ids or [],
            envs=envs or {},
        )
    return _make


@pytest.fixture
def deployment_service():
    from api.services.application_deployment_service import ApplicationDeploymentService
    return ApplicationDeploymentService()


# ── ECS secrets/env builder ─────────────────────────────────────────────────────

@pytest.mark.django_db
def test_postgres_injection_builds_secrets_and_plain_env(deployment_service, make_infra_env, make_db_row, make_app):
    user, infra, env = make_infra_env()
    db = make_db_row(env, name="primary-db", engine="postgres")
    app = make_app(user, infra, attached_database_ids=[str(db.id)])

    plain_env, secrets = deployment_service._build_database_injections(app)

    assert plain_env["PRIMARY_DB_HOST"] == "db.example.com"
    assert plain_env["PRIMARY_DB_PORT"] == "5432"
    assert plain_env["PRIMARY_DB_DB"] == "primary-db"
    names = {s["name"] for s in secrets}
    assert names == {"PRIMARY_DB_USERNAME", "PRIMARY_DB_PASSWORD"}
    for s in secrets:
        assert s["valueFrom"].startswith(db.secret_arn + ":")


@pytest.mark.django_db
def test_redis_injection_uses_auth_token_and_tls(deployment_service, make_infra_env, make_db_row, make_app):
    user, infra, env = make_infra_env()
    db = make_db_row(env, name="cache-1", engine="redis", port=6379)
    app = make_app(user, infra, attached_database_ids=[str(db.id)])

    plain_env, secrets = deployment_service._build_database_injections(app)

    assert plain_env["CACHE_1_TLS"] == "true"
    assert "CACHE_1_DB" not in plain_env
    names = {s["name"] for s in secrets}
    assert names == {"CACHE_1_AUTH_TOKEN"}


@pytest.mark.django_db
def test_non_active_database_is_not_injected(deployment_service, make_infra_env, make_db_row, make_app):
    user, infra, env = make_infra_env()
    db = make_db_row(env, status="PROVISIONING")
    app = make_app(user, infra, attached_database_ids=[str(db.id)])

    plain_env, secrets = deployment_service._build_database_injections(app)
    assert plain_env == {}
    assert secrets == []


@pytest.mark.django_db
def test_injected_names_are_computed_from_database_name_prefix(deployment_service, make_infra_env, make_db_row, make_app):
    user, infra, env = make_infra_env()
    db = make_db_row(env, name="my-app-db")
    app = make_app(user, infra, attached_database_ids=[str(db.id)])
    plain_env, _ = deployment_service._build_database_injections(app)
    assert "MY_APP_DB_HOST" in plain_env


# ── attach validation (application_service.update_application) ─────────────────

@pytest.mark.django_db
def test_attach_rejects_database_from_another_infra(make_infra_env, make_db_row, make_app):
    from api.services.application_service import ApplicationService

    user, infra, _env = make_infra_env()
    _other_user, _other_infra, other_env = make_infra_env()
    foreign_db = make_db_row(other_env)
    app = make_app(user, infra)

    service = ApplicationService.__new__(ApplicationService)
    from api.repositories.application import ApplicationRepository
    from api.repositories.infrastructure import InfrastructureRepository
    service.app_repo = ApplicationRepository()
    service.infra_repo = InfrastructureRepository()

    with pytest.raises(ValueError, match="Unknown or unavailable"):
        service.update_application(user.id, str(app.id), {"attached_database_ids": [str(foreign_db.id)]})


@pytest.mark.django_db
def test_attach_rejects_deleting_database(make_infra_env, make_db_row, make_app):
    from api.services.application_service import ApplicationService

    user, infra, env = make_infra_env()
    db = make_db_row(env, status="DELETING")
    app = make_app(user, infra)

    service = ApplicationService.__new__(ApplicationService)
    from api.repositories.application import ApplicationRepository
    from api.repositories.infrastructure import InfrastructureRepository
    service.app_repo = ApplicationRepository()
    service.infra_repo = InfrastructureRepository()

    with pytest.raises(ValueError, match="Unknown or unavailable"):
        service.update_application(user.id, str(app.id), {"attached_database_ids": [str(db.id)]})


@pytest.mark.django_db
def test_attach_accepts_active_database_in_same_infra(make_infra_env, make_db_row, make_app):
    from api.services.application_service import ApplicationService

    user, infra, env = make_infra_env()
    db = make_db_row(env, status="ACTIVE")
    app = make_app(user, infra)

    service = ApplicationService.__new__(ApplicationService)
    from api.repositories.application import ApplicationRepository
    from api.repositories.infrastructure import InfrastructureRepository
    service.app_repo = ApplicationRepository()
    service.infra_repo = InfrastructureRepository()

    updated = service.update_application(user.id, str(app.id), {"attached_database_ids": [str(db.id)]})
    assert updated.attached_database_ids == [str(db.id)]
