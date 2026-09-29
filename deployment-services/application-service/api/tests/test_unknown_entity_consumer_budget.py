"""A message referencing an entity that never materializes (an unknown infra id, most
often a test suite's junk id that leaked onto the real broker — see root conftest.py's
no_real_broker fixture) must not hold a prefetch=1 queue for minutes. Every consumer with
the "not synced yet — deferred (attempt N/MAX, delay up to Ns)" retry pattern bounds the
total wait to MAX_RETRIES=3 short, capped-exponential attempts before discarding.

test_host_readiness_consumer.py and test_infra_exited_consumer.py cover
HostReadinessEventConsumer and InfraExitedEventConsumer directly (their own read-model
lookups need dedicated fixtures). This file covers the remaining three consumers that
share the same bounded-retry shape: InfraEventConsumer, InfraUpdatedEventConsumer, and
EnvironmentEventConsumer.
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


def _drain_budget_and_assert_discarded(consumer, event_type, payload, retry_key, max_retries):
    total_delay = 0
    for _ in range(max_retries):
        ch = _deliver(consumer, event_type, payload)
        ch.basic_nack.assert_called_once_with(delivery_tag=1, requeue=True)
        ch.connection.sleep.assert_called_once()
        total_delay += ch.connection.sleep.call_args.args[0]

    assert total_delay <= 10, "unknown-entity retries must total only a few seconds, not minutes"

    ch_final = _deliver(consumer, event_type, payload)
    ch_final.basic_nack.assert_called_once_with(delivery_tag=1, requeue=False)
    ch_final.connection.sleep.assert_not_called()
    assert retry_key not in consumer._retry_counts


# ── InfraEventConsumer (infrastructure.created) ─────────────────────────────────────────

@pytest.mark.django_db
def test_infra_event_consumer_discards_unknown_owner_after_short_budget_then_processes_next(schema_db):
    from api.messaging.consumers.infrastructure import InfraEventConsumer
    from api.models.user import User
    from api.repositories.infrastructure import InfrastructureRepository

    consumer = InfraEventConsumer.__new__(InfraEventConsumer)
    consumer.infra_repo = InfrastructureRepository()
    consumer._retry_counts = {}

    infra_id = str(uuid.uuid4())
    unknown_user_id = str(uuid.uuid4())
    payload = {"id": infra_id, "user_id": unknown_user_id}

    _drain_budget_and_assert_discarded(
        consumer, "infrastructure.created", payload,
        retry_key=infra_id, max_retries=InfraEventConsumer.MAX_RETRIES,
    )

    real_user = User.objects.create(id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="t")
    next_infra_id = str(uuid.uuid4())
    ch_next = _deliver(consumer, "infrastructure.created", {"id": next_infra_id, "user_id": str(real_user.id)})
    ch_next.basic_ack.assert_called_once_with(delivery_tag=1)


# ── InfraUpdatedEventConsumer (infrastructure.updated) ──────────────────────────────────

@pytest.mark.django_db
def test_infra_updated_consumer_discards_unmaterialized_infra_after_short_budget_then_processes_next(schema_db):
    from api.messaging.consumers.infrastructure import InfraUpdatedEventConsumer
    from api.models.infrastructure import Infrastructure
    from api.models.user import User
    from api.repositories.infrastructure import InfrastructureRepository

    consumer = InfraUpdatedEventConsumer.__new__(InfraUpdatedEventConsumer)
    consumer.infra_repo = InfrastructureRepository()
    consumer._retry_counts = {}

    unknown_infra_id = str(uuid.uuid4())
    payload = {"id": unknown_infra_id, "name": "renamed"}

    _drain_budget_and_assert_discarded(
        consumer, "infrastructure.updated", payload,
        retry_key=unknown_infra_id, max_retries=InfraUpdatedEventConsumer.MAX_RETRIES,
    )

    user = User.objects.create(id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="t")
    infra = Infrastructure.objects.create(
        id=uuid.uuid4(), user=user, name="infra", cloud_provider="aws", max_cpu=1024, max_memory=512,
    )
    ch_next = _deliver(consumer, "infrastructure.updated", {"id": str(infra.id), "name": "renamed-again"})
    ch_next.basic_ack.assert_called_once_with(delivery_tag=1)
    infra.refresh_from_db()
    assert infra.name == "renamed-again"


# ── EnvironmentEventConsumer (environment.updated) ──────────────────────────────────────

@pytest.mark.django_db
def test_environment_consumer_discards_unknown_infra_after_short_budget_then_processes_next(schema_db):
    from api.messaging.consumers.environment import EnvironmentEventConsumer
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    consumer = EnvironmentEventConsumer.__new__(EnvironmentEventConsumer)
    consumer._retry_counts = {}

    env_id = str(uuid.uuid4())
    unknown_infra_id = str(uuid.uuid4())
    payload = {"id": env_id, "infrastructure_id": unknown_infra_id, "status": "ACTIVE"}

    _drain_budget_and_assert_discarded(
        consumer, "environment.updated", payload,
        retry_key=env_id, max_retries=EnvironmentEventConsumer.MAX_RETRIES,
    )

    user = User.objects.create(id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="t")
    infra = Infrastructure.objects.create(
        id=uuid.uuid4(), user=user, name="infra", cloud_provider="aws", max_cpu=1024, max_memory=512,
    )
    next_env_id = str(uuid.uuid4())
    ch_next = _deliver(consumer, "environment.updated", {
        "id": next_env_id, "infrastructure_id": str(infra.id), "status": "ACTIVE",
    })
    ch_next.basic_ack.assert_called_once_with(delivery_tag=1)
