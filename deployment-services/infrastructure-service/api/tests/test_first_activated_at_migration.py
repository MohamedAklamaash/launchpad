"""Migration 0017 backfill: existing ACTIVE environments predate first_activated_at, and
the failure/reap gates keyed on it would tear down live resources if they read NULL there."""
import importlib
import uuid
from datetime import timedelta

import pytest
from django.apps import apps
from django.utils import timezone

MIGRATION = importlib.import_module("api.migrations.0017_environment_first_activated_at_and_more")


@pytest.fixture
def make_env(db):
    from api.models.environment import Environment
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    def _make(*, status, first_activated_at=None, vpc_id=None):
        user = User.objects.create(
            id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="t", role="super_admin"
        )
        infra = Infrastructure.objects.create(
            user=user, name=f"infra-{uuid.uuid4()}", cloud_provider="aws",
            max_cpu=1024, max_memory=512, code="123456789012",
        )
        return Environment.objects.create(
            infrastructure=infra, status=status, first_activated_at=first_activated_at, vpc_id=vpc_id
        )

    return _make


def _backfill():
    MIGRATION.backfill_first_activated_at(apps, None)


def test_backfill_stamps_existing_active_environments(make_env):
    env = make_env(status="ACTIVE")
    assert env.first_activated_at is None

    _backfill()

    env.refresh_from_db()
    assert env.first_activated_at == env.updated_at


def test_backfill_leaves_already_stamped_environments_alone(make_env):
    stamped = timezone.now() - timedelta(days=90)
    env = make_env(status="ACTIVE", first_activated_at=stamped)

    _backfill()

    env.refresh_from_db()
    assert env.first_activated_at == stamped


@pytest.mark.parametrize("status", ["PENDING", "PROVISIONING", "ERROR", "DESTROYING", "DESTROYED"])
def test_backfill_ignores_non_active_environments(make_env, status):
    env = make_env(status=status)

    _backfill()

    env.refresh_from_db()
    assert env.first_activated_at is None


def test_backfill_stamps_error_row_with_real_outputs(make_env):
    """A previously-live env that moved to ERROR must not read as never-activated."""
    env = make_env(status="ERROR", vpc_id="vpc-0123456789abcdef0")
    assert env.first_activated_at is None

    _backfill()

    env.refresh_from_db()
    assert env.first_activated_at == env.updated_at


def test_backfill_is_idempotent(make_env):
    env = make_env(status="ACTIVE")
    _backfill()
    env.refresh_from_db()
    stamped = env.first_activated_at

    _backfill()

    env.refresh_from_db()
    assert env.first_activated_at == stamped
