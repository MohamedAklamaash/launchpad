"""H6 (hardening, see plan/H-hardening.md and its H2 note): every retry delay inside a
pika callback must use `ch.connection.sleep()`, never a bare `time.sleep()`. A bare sleep
blocks the whole BlockingConnection's I/O thread, so the broker stops seeing heartbeats and
can drop the connection under load — exactly the bug H2 already fixed once for
InfraExitedEventConsumer. This file locks in the same fix across the other consumers that
had the same bare-sleep retry pattern.

Exercises each callback directly (bypassing the real ResilientPikaConsumer, which needs a
live broker), the same pattern the rest of this codebase's consumer tests use. `ch` is a
MagicMock, so `ch.connection.sleep(...)` records a call instead of actually sleeping —
these tests would hang (or, before the fix, actually sleep) if the code still called the
real `time.sleep`.
"""
import json
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def _deliver(consumer, event_type, payload):
    ch = MagicMock()
    method = SimpleNamespace(delivery_tag=1)
    properties = SimpleNamespace(correlation_id="cid")
    body = json.dumps({"type": event_type, "payload": payload}).encode()
    consumer.callback(ch, method, properties, body)
    return ch


# ── InfraEventConsumer (infrastructure.created) ─────────────────────────────────────────

@pytest.mark.django_db
def test_infra_event_consumer_retries_with_connection_sleep(schema_db):
    from api.messaging.consumers.infrastructure import InfraEventConsumer
    from api.repositories.infrastructure import InfrastructureRepository

    consumer = InfraEventConsumer.__new__(InfraEventConsumer)
    consumer.infra_repo = InfrastructureRepository()
    consumer._retry_counts = {}

    # user_id references no User row — upsert_infrastructure raises User.DoesNotExist
    # (an ObjectDoesNotExist subclass), the same "owner not synced yet" condition this
    # consumer is designed to retry.
    payload = {"id": str(uuid.uuid4()), "user_id": str(uuid.uuid4())}
    ch = _deliver(consumer, "infrastructure.created", payload)

    ch.basic_nack.assert_called_once_with(delivery_tag=1, requeue=True)
    ch.connection.sleep.assert_called_once_with(1)


# ── InfraUpdatedEventConsumer (infrastructure.updated) ──────────────────────────────────

@pytest.mark.django_db
def test_infra_updated_consumer_retries_with_connection_sleep(schema_db):
    from api.messaging.consumers.infrastructure import InfraUpdatedEventConsumer
    from api.repositories.infrastructure import InfrastructureRepository

    consumer = InfraUpdatedEventConsumer.__new__(InfraUpdatedEventConsumer)
    consumer.infra_repo = InfrastructureRepository()
    consumer._retry_counts = {}

    payload = {"id": str(uuid.uuid4()), "name": "renamed"}
    ch = _deliver(consumer, "infrastructure.updated", payload)

    ch.basic_nack.assert_called_once_with(delivery_tag=1, requeue=True)
    ch.connection.sleep.assert_called_once_with(1)


# ── EnvironmentEventConsumer (environment.updated) ──────────────────────────────────────

@pytest.mark.django_db
def test_environment_consumer_retries_with_connection_sleep_when_infra_missing(schema_db):
    from api.messaging.consumers.environment import EnvironmentEventConsumer

    consumer = EnvironmentEventConsumer.__new__(EnvironmentEventConsumer)
    consumer._retry_counts = {}

    payload = {"id": str(uuid.uuid4()), "infrastructure_id": str(uuid.uuid4()), "status": "ACTIVE"}
    ch = _deliver(consumer, "environment.updated", payload)

    ch.basic_nack.assert_called_once_with(delivery_tag=1, requeue=True)
    ch.connection.sleep.assert_called_once_with(1)


@pytest.mark.django_db
def test_environment_consumer_retries_with_connection_sleep_on_transient_error(schema_db, monkeypatch):
    from django.db import OperationalError

    from api.messaging.consumers import environment as environment_module
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    user = User.objects.create(id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="t")
    infra = Infrastructure.objects.create(
        id=uuid.uuid4(), user=user, name="infra", cloud_provider="aws", max_cpu=1024, max_memory=512,
    )
    monkeypatch.setattr(
        environment_module.Environment.objects, "update_or_create",
        MagicMock(side_effect=OperationalError("connection lost")),
    )

    consumer = environment_module.EnvironmentEventConsumer.__new__(environment_module.EnvironmentEventConsumer)
    consumer._retry_counts = {}

    payload = {"id": str(uuid.uuid4()), "infrastructure_id": str(infra.id), "status": "ACTIVE"}
    ch = _deliver(consumer, "environment.updated", payload)

    ch.basic_nack.assert_called_once_with(delivery_tag=1, requeue=True)
    ch.connection.sleep.assert_called_once_with(1)


# ── AuthEventConsumer (auth.user.registered) ────────────────────────────────────────────

@pytest.mark.django_db
def test_auth_event_consumer_retries_with_connection_sleep(schema_db):
    from api.messaging.consumers.user import AuthEventConsumer
    from api.repositories.user import PendingInvitedInfrastructure

    consumer = AuthEventConsumer.__new__(AuthEventConsumer)
    consumer.user_repo = MagicMock()
    consumer.user_repo.upsert_user.side_effect = PendingInvitedInfrastructure(missing=["infra-x"])
    consumer._retry_counts = {}

    payload = {"id": str(uuid.uuid4()), "email": "a@example.com", "user_name": "a"}
    ch = _deliver(consumer, "auth.user.registered", payload)

    ch.basic_nack.assert_called_once_with(delivery_tag=1, requeue=True)
    ch.connection.sleep.assert_called_once_with(1)
