"""Pytest bootstrap for the application-service.

A normal `migrate` cannot run on SQLite because the PRE-EXISTING migration 0005
(`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, Postgres-only) raises a syntax error
on SQLite — unrelated to the MODE=dev change. To still get DB-backed coverage of
upsert_infrastructure we build the handful of tables we need directly from the
current model state via schema_editor, sidestepping the broken raw-SQL migration.
"""
import json
import os
import sys
from types import SimpleNamespace

import django
import pytest

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "test_settings")


def pytest_configure(config):
    django.setup()


@pytest.fixture(autouse=True)
def no_real_broker(monkeypatch):
    """Tests must never open a socket to a real RabbitMQ broker.

    A test that exercises a service function without explicitly mocking the producer
    used to call straight through to ResilientPikaProducer.connect()/publish() and land
    real messages on whatever broker happens to be running locally. Patching at the CLASS
    level (not per-instance) covers every ResilientPikaProducer instance regardless of
    when it was constructed, including module-level singletons like `_producer`
    (api/messaging/producer/producer.py), which is created at import time, before this
    fixture ever runs. RABBITMQ_URL is also unroutable in test_settings and connect()
    itself refuses to run under pytest (shared/resilience/amqp.py) — this fixture is the
    primary layer; those are the backstop for anything that bypasses it.

    Returns the list of recorded publishes (routing_key, body, properties) so a test that
    wants to assert on what would have been published can use this fixture directly.
    """
    from shared.resilience.amqp import ResilientPikaProducer

    published: list[SimpleNamespace] = []

    def _fake_connect(self):
        self.connection = None
        self.channel = None

    def _fake_publish(self, routing_key, body, properties=None):
        serialized = json.dumps(body) if not isinstance(body, (str, bytes)) else body
        published.append(SimpleNamespace(routing_key=routing_key, body=serialized, properties=properties))

    monkeypatch.setattr(ResilientPikaProducer, "connect", _fake_connect)
    monkeypatch.setattr(ResilientPikaProducer, "publish", _fake_publish)
    return published


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


@pytest.fixture
def platform_builder_mock_mode(monkeypatch):
    """Routes aws/platform_builder.py's real mock branch for a test that drives
    ApplicationDeploymentService.deploy_application through MockSession without hitting
    GitHub or real AWS, and without patching platform_builder.start_build/wait_for_build
    itself.

    Setting `Infrastructure.is_mock = True` alone is NOT enough: aws/session.py's
    gate_mismatch (called by both aws/platform_builder.py:start_build and
    aws/platform_builder_credentials.py:mint_push_credential) treats an is_mock infra
    running under a non-dev MODE as a *mismatch* to refuse, not a silent mock — and
    test_settings.py deliberately pins MODE=prod (see its own docstring). So every
    module that reads `app_config.mode` on this path has to agree it's "dev" too —
    mirrors test_platform_build_wiring.py's test_mock_builder_end_to_end's four-way
    patch, pulled out here for reuse."""
    from types import SimpleNamespace

    import aws.github_source as github_source_mod
    import aws.platform_builder as platform_builder_mod
    import aws.platform_builder_credentials as platform_builder_credentials_mod
    import aws.session as aws_session_mod

    dev = SimpleNamespace(mode="dev", aws_access_key_id="x", aws_secret_access_key="y")
    monkeypatch.setattr(aws_session_mod, "app_config", dev)
    monkeypatch.setattr(platform_builder_credentials_mod, "app_config", dev)
    monkeypatch.setattr(platform_builder_mod, "app_config", dev)
    monkeypatch.setattr(github_source_mod, "app_config", dev)
    # _mock_build_handle's fetch/upload calls no-op in dev mode but still read the bucket
    # name unconditionally first (aws/github_source.py:source_bucket).
    monkeypatch.setenv("PLATFORM_BUILDER_SOURCE_BUCKET", "launchpad-platform-builder-sources")
