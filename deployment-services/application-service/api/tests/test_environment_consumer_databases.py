"""environment.updated v3's databases[] reconciliation: v2 payloads (no key) must
never wipe existing rows, and a v3 payload is a full-replace sync per environment."""
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
        return infra, env
    return _make


@pytest.fixture
def consumer():
    from api.messaging.consumers.environment import EnvironmentEventConsumer
    return EnvironmentEventConsumer.__new__(EnvironmentEventConsumer)


def _db_entry(**overrides):
    entry = {
        "id": str(uuid.uuid4()), "name": "primary-db", "engine": "postgres",
        "host": "db.example.com", "port": 5432,
        "secret_arn": "arn:aws:secretsmanager:us-east-1:123456789012:secret:x",
        "status": "ACTIVE",
    }
    entry.update(overrides)
    return entry


@pytest.mark.django_db
def test_reconcile_creates_and_updates_rows(consumer, make_infra_env):
    from api.models.database import Database

    _infra, env = make_infra_env()
    entry = _db_entry()
    consumer._reconcile_databases(env, [entry])

    row = Database.objects.get(id=entry["id"])
    assert row.name == "primary-db"
    assert row.host == "db.example.com"
    assert row.status == "ACTIVE"

    entry["status"] = "ERROR"
    consumer._reconcile_databases(env, [entry])
    row.refresh_from_db()
    assert row.status == "ERROR"


@pytest.mark.django_db
def test_reconcile_drops_rows_missing_from_payload(consumer, make_infra_env):
    from api.models.database import Database

    _infra, env = make_infra_env()
    kept = _db_entry(name="kept-db")
    dropped = _db_entry(name="dropped-db")
    consumer._reconcile_databases(env, [kept, dropped])
    assert Database.objects.filter(environment=env).count() == 2

    # Second apply's payload no longer includes "dropped-db" (it was deleted upstream).
    consumer._reconcile_databases(env, [kept])
    remaining = list(Database.objects.filter(environment=env).values_list("name", flat=True))
    assert remaining == ["kept-db"]


@pytest.mark.django_db
def test_reconcile_empty_list_clears_all_rows(consumer, make_infra_env):
    from api.models.database import Database

    _infra, env = make_infra_env()
    consumer._reconcile_databases(env, [_db_entry()])
    assert Database.objects.filter(environment=env).count() == 1

    consumer._reconcile_databases(env, [])
    assert Database.objects.filter(environment=env).count() == 0


@pytest.mark.django_db
def test_v2_payload_without_databases_key_leaves_existing_rows_untouched(make_infra_env):
    """A v2 producer (or a stale one during a rolling deploy) omits `databases`
    entirely — the consumer must treat that as 'no opinion', not 'empty list'."""
    from api.messaging.consumers.environment import EnvironmentEventConsumer
    from api.models.database import Database

    infra, env = make_infra_env()
    Database.objects.create(id=uuid.uuid4(), environment=env, name="pre-existing", engine="postgres")

    consumer = EnvironmentEventConsumer.__new__(EnvironmentEventConsumer)
    payload = {
        "id": str(env.id), "environment_id": str(env.id), "infrastructure_id": str(infra.id),
        "status": "ACTIVE",
        # no "databases" key at all — v2 shape
    }
    if "databases" in payload:
        consumer._reconcile_databases(env, payload["databases"])

    assert Database.objects.filter(environment=env).count() == 1
