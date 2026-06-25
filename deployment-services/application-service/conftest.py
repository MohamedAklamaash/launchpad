"""Pytest bootstrap for the application-service.

A normal `migrate` cannot run on SQLite because the PRE-EXISTING migration 0005
(`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, Postgres-only) raises a syntax error
on SQLite — unrelated to the MODE=dev change. To still get DB-backed coverage of
upsert_infrastructure we build the handful of tables we need directly from the
current model state via schema_editor, sidestepping the broken raw-SQL migration.
"""
import os
import sys

import django
import pytest

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "test_settings")


def pytest_configure(config):
    django.setup()


@pytest.fixture(scope="session")
def django_db_setup(django_db_blocker):
    """Create an empty test database WITHOUT running migrations.

    pytest-django's default setup runs `migrate`, which fails on the pre-existing
    Postgres-only migration 0005. We disable migrations for the `api` app (so
    create_test_db won't run the broken raw SQL) and let per-test fixtures build
    only the tables they need from model state via schema_editor.
    """
    from django.conf import settings
    from django.db import connection

    # MigrationLoader treats a None target as "no migrations" for that app.
    settings.MIGRATION_MODULES = {"api": None}

    with django_db_blocker.unblock():
        connection.creation.create_test_db(verbosity=0, autoclobber=True)
        yield
        connection.creation.destroy_test_db(":memory:", verbosity=0)


@pytest.fixture
def schema_db(django_db_setup):
    """DB is built from model state (migrations disabled in django_db_setup).

    All api tables exist via syncdb, so this is just a marker fixture the DB-backed
    tests depend on. Combined with @pytest.mark.django_db, each test runs inside a
    transaction that is rolled back, keeping tests isolated.
    """
    yield
