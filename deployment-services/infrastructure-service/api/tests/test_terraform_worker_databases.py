"""Per-engine terraform config generation, output reconciliation, and the
UPDATING/rollback-gate rules for the managed-database worker path."""
import uuid

import pytest
from api.services.terraform_worker import TerraformWorker
from shared.enums.orchestrator import ComputeType


@pytest.fixture
def make_infra_env(db):
    from api.models.environment import Environment
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    def _make(*, env_status="ACTIVE", first_activated_at=None):
        user = User.objects.create(
            id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="t", role="super_admin",
        )
        infra = Infrastructure.objects.create(
            user=user, name=f"infra-{uuid.uuid4()}", cloud_provider="aws",
            max_cpu=1024, max_memory=512, code="123456789012", metadata={},
        )
        from django.utils import timezone
        env = Environment.objects.create(
            infrastructure=infra, status=env_status, vpc_id="vpc-abc123",
            first_activated_at=first_activated_at or (timezone.now() if env_status == "ACTIVE" else None),
        )
        return infra, env
    return _make


@pytest.fixture
def make_db_row(db):
    from api.models.database import Database

    def _make(env, *, name="primary-db", engine="postgres", status="PENDING", allocated_storage=20):
        return Database.objects.create(
            environment=env, name=name, engine=engine, engine_version="16.6",
            instance_class="db.t3.micro", allocated_storage=allocated_storage, status=status,
        )
    return _make


# ── config generation ──────────────────────────────────────────────────────────

def test_generate_config_includes_one_module_block_per_live_engine(make_infra_env, make_db_row):
    infra, env = make_infra_env()
    make_db_row(env, name="pg-db", engine="postgres")
    make_db_row(env, name="cache-db", engine="redis")
    make_db_row(env, name="doc-db", engine="docdb")

    config = TerraformWorker._generate_config(
        {"aws_region": "us-east-1", "vpc_cidr": "10.0.0.0/16", "db_app_sg_id": "sg-app123"},
        str(infra.id), "bucket", "table", "us-east-1",
        ComputeType.ECS_FARGATE, "123456789012",
    )
    assert 'source                    = "./modules/rds"' in config
    assert 'source                    = "./modules/elasticache"' in config
    assert 'source                    = "./modules/docdb"' in config
    assert 'app_security_group_id       = "sg-app123"' in config


def test_generate_config_excludes_deleting_and_deleted_rows(make_infra_env, make_db_row):
    infra, env = make_infra_env()
    live = make_db_row(env, name="live-db", status="ACTIVE")
    deleting = make_db_row(env, name="deleting-db", status="DELETING")
    deleted = make_db_row(env, name="deleted-db", status="DELETED")

    config = TerraformWorker._generate_config(
        {"aws_region": "us-east-1", "vpc_cidr": "10.0.0.0/16"},
        str(infra.id), "bucket", "table", "us-east-1",
        ComputeType.ECS_FARGATE, "123456789012",
    )
    assert f'module "{live.module_name()}"' in config
    assert f'module "{deleting.module_name()}"' not in config
    assert f'module "{deleted.module_name()}"' not in config


def test_generate_config_rejects_invalid_database_name(make_infra_env, make_db_row):
    infra, env = make_infra_env()
    row = make_db_row(env)
    # Bypass API validation to simulate a row written before the check existed.
    from api.models.database import Database
    Database.objects.filter(id=row.id).update(name="INVALID NAME!")

    with pytest.raises(ValueError):
        TerraformWorker._generate_config(
            {"aws_region": "us-east-1", "vpc_cidr": "10.0.0.0/16"},
            str(infra.id), "bucket", "table", "us-east-1",
        ComputeType.ECS_FARGATE, "123456789012",
        )


def test_generate_config_with_no_databases_omits_db_secret_arns(make_infra_env):
    infra, _env = make_infra_env()
    config = TerraformWorker._generate_config(
        {"aws_region": "us-east-1", "vpc_cidr": "10.0.0.0/16"},
        str(infra.id), "bucket", "table", "us-east-1",
        ComputeType.ECS_FARGATE, "123456789012",
    )
    assert "db_secret_arns  = []" in config


# ── output reconciliation ────────────────────────────────────────────────────

def test_reconcile_databases_marks_active_from_outputs(make_infra_env, make_db_row):
    _infra, env = make_infra_env()
    row = make_db_row(env)
    mod = row.module_name()
    outputs = {
        f"{mod}_endpoint": {"value": "db.example.com"},
        f"{mod}_port": {"value": 5432},
        f"{mod}_secret_arn": {"value": "arn:aws:secretsmanager:us-east-1:123456789012:secret:x"},
    }
    payload = TerraformWorker._reconcile_databases(env, outputs)
    row.refresh_from_db()
    assert row.status == "ACTIVE"
    assert row.host == "db.example.com"
    assert row.port == 5432
    assert payload == [{
        "id": str(row.id), "name": row.name, "engine": row.engine,
        "host": "db.example.com", "port": 5432,
        "secret_arn": "arn:aws:secretsmanager:us-east-1:123456789012:secret:x",
        "status": "ACTIVE",
    }]


def test_reconcile_databases_marks_deleting_row_deleted(make_infra_env, make_db_row):
    _infra, env = make_infra_env()
    row = make_db_row(env, status="DELETING")
    payload = TerraformWorker._reconcile_databases(env, {})
    row.refresh_from_db()
    assert row.status == "DELETED"
    assert row.host is None
    assert payload == []  # DELETED rows are excluded from the environment.updated payload


def test_reconcile_databases_marks_missing_output_as_error(make_infra_env, make_db_row):
    _infra, env = make_infra_env()
    row = make_db_row(env, status="PROVISIONING")
    TerraformWorker._reconcile_databases(env, {})
    row.refresh_from_db()
    assert row.status == "ERROR"
    assert row.error_message


# ── UPDATING / rollback gate ─────────────────────────────────────────────────

def test_mock_provision_reconciles_pending_db_on_already_active_env(make_infra_env, make_db_row):
    """A DB create on an already-ACTIVE mock env must not be swallowed by an
    'already active' short-circuit — it has to run the UPDATING apply and resolve
    the pending row, not leave it stuck PENDING forever."""
    infra, env = make_infra_env(env_status="ACTIVE")
    row = make_db_row(env, status="PENDING")

    TerraformWorker._mock_provision(str(infra.id), infra)

    env.refresh_from_db()
    row.refresh_from_db()
    assert env.status == "ACTIVE"
    assert env.first_activated_at is not None
    assert row.status == "ACTIVE"
    assert row.host is not None
    assert row.secret_arn is not None


def test_mock_provision_deletes_pending_deletion(make_infra_env, make_db_row):
    infra, env = make_infra_env(env_status="ACTIVE")
    row = make_db_row(env, status="DELETING")

    TerraformWorker._mock_provision(str(infra.id), infra)

    row.refresh_from_db()
    assert row.status == "DELETED"
