"""InfraExitedEventConsumer — H2 (hardening) read-model mirror for Infrastructure.exited_at.

Exercises the callback directly (bypassing the real ResilientPikaConsumer, which needs a
live broker), the same pattern test_host_readiness_consumer.py uses. exited_at is a
one-way latch, not a versioned snapshot — see the consumer's own docstring for why it is
deliberately NOT gated by host_readiness_version.
"""
import json
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def consumer():
    from api.messaging.consumers.infrastructure import InfraExitedEventConsumer
    return InfraExitedEventConsumer.__new__(InfraExitedEventConsumer)


@pytest.fixture
def make_infra(schema_db):
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    def _make(**overrides):
        user = User.objects.create(
            id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="t",
        )
        defaults = {
            "id": uuid.uuid4(), "user": user, "name": f"infra-{uuid.uuid4()}", "cloud_provider": "aws",
            "max_cpu": 1024, "max_memory": 512,
        }
        defaults.update(overrides)
        return Infrastructure.objects.create(**defaults)
    return _make


def _deliver(consumer, payload):
    ch = MagicMock()
    method = SimpleNamespace(delivery_tag=1)
    properties = SimpleNamespace(correlation_id="cid")
    body = json.dumps({
        "type": "infrastructure.exited",
        "payload": payload,
    }).encode()
    consumer.callback(ch, method, properties, body)
    return ch


@pytest.mark.django_db
def test_missing_infra_id_is_discarded(consumer):
    ch = _deliver(consumer, {"exited_at": "2026-01-01T00:00:00Z"})
    ch.basic_nack.assert_called_once_with(delivery_tag=1, requeue=False)


@pytest.mark.django_db
def test_missing_exited_at_is_discarded(consumer, make_infra):
    infra = make_infra()
    ch = _deliver(consumer, {"infra_id": str(infra.id)})
    ch.basic_nack.assert_called_once_with(delivery_tag=1, requeue=False)
    infra.refresh_from_db()
    assert infra.exited_at is None


@pytest.mark.django_db
def test_unmaterialized_infra_is_requeued_then_ackable_once_it_exists(consumer, make_infra):
    infra_id = str(uuid.uuid4())
    consumer._retry_counts = {}

    payload = {"infra_id": infra_id, "exited_at": "2026-01-01T00:00:00Z"}
    ch = _deliver(consumer, payload)
    ch.basic_nack.assert_called_once_with(delivery_tag=1, requeue=True)
    # RECOMMENDED 3 (security review): the retry delay must go through the pika
    # connection's own sleep (keeps heartbeats alive), never a bare time.sleep (which
    # would starve the BlockingConnection's I/O loop for the delay's duration — this
    # module no longer imports `time` at all, see H6).
    ch.connection.sleep.assert_called_once_with(1)  # delay = min(2**0, 5) = 1

    infra = make_infra(id=infra_id)
    ch2 = _deliver(consumer, payload)
    ch2.basic_ack.assert_called_once_with(delivery_tag=1)
    infra.refresh_from_db()
    assert infra.exited_at is not None


@pytest.mark.django_db
def test_unknown_infra_is_discarded_after_a_short_budget_and_the_queue_moves_on(consumer, make_infra):
    """Same short-budget bound as HostReadinessEventConsumer: a junk infra id must not
    hold this prefetch=1 queue for minutes. MAX_RETRIES=3 with a capped exponential delay
    bounds the total wait to a few seconds before the event is discarded, and the next
    message is processed normally."""
    from api.messaging.consumers.infrastructure import InfraExitedEventConsumer

    unknown_infra_id = str(uuid.uuid4())
    consumer._retry_counts = {}
    payload = {"infra_id": unknown_infra_id, "exited_at": "2026-01-01T00:00:00Z"}

    total_delay = 0
    for attempt in range(InfraExitedEventConsumer.MAX_RETRIES):
        ch = _deliver(consumer, payload)
        ch.basic_nack.assert_called_once_with(delivery_tag=1, requeue=True)
        ch.connection.sleep.assert_called_once()
        total_delay += ch.connection.sleep.call_args.args[0]

    assert total_delay <= 10, "unknown-infra retries must total only a few seconds, not minutes"

    ch_final = _deliver(consumer, payload)
    ch_final.basic_nack.assert_called_once_with(delivery_tag=1, requeue=False)
    ch_final.connection.sleep.assert_not_called()
    assert unknown_infra_id not in consumer._retry_counts

    infra = make_infra()
    ch_next = _deliver(consumer, {"infra_id": str(infra.id), "exited_at": "2026-02-01T00:00:00Z"})
    ch_next.basic_ack.assert_called_once_with(delivery_tag=1)
    infra.refresh_from_db()
    assert infra.exited_at is not None


@pytest.mark.django_db
def test_sets_exited_at_and_acks(consumer, make_infra):
    infra = make_infra()
    consumer._retry_counts = {}

    ch = _deliver(consumer, {"infra_id": str(infra.id), "exited_at": "2026-03-01T12:00:00Z"})

    ch.basic_ack.assert_called_once_with(delivery_tag=1)
    infra.refresh_from_db()
    assert infra.exited_at is not None
    assert infra.exited_at.year == 2026 and infra.exited_at.month == 3


# ── monotonic latch: once set, never cleared or changed by a later/earlier event ─────

@pytest.mark.django_db
def test_a_later_exited_event_does_not_change_an_already_set_value(consumer, make_infra):
    infra = make_infra()
    consumer._retry_counts = {}

    _deliver(consumer, {"infra_id": str(infra.id), "exited_at": "2026-01-01T00:00:00Z"})
    infra.refresh_from_db()
    first_value = infra.exited_at

    ch = _deliver(consumer, {"infra_id": str(infra.id), "exited_at": "2026-06-01T00:00:00Z"})

    ch.basic_ack.assert_called_once_with(delivery_tag=1)
    infra.refresh_from_db()
    assert infra.exited_at == first_value


@pytest.mark.django_db
def test_an_earlier_exited_event_does_not_clear_or_change_an_already_set_value(consumer, make_infra):
    infra = make_infra()
    consumer._retry_counts = {}

    _deliver(consumer, {"infra_id": str(infra.id), "exited_at": "2026-06-01T00:00:00Z"})
    infra.refresh_from_db()
    first_value = infra.exited_at

    ch = _deliver(consumer, {"infra_id": str(infra.id), "exited_at": "2020-01-01T00:00:00Z"})

    ch.basic_ack.assert_called_once_with(delivery_tag=1)
    infra.refresh_from_db()
    assert infra.exited_at == first_value


@pytest.mark.django_db
def test_a_duplicate_redelivered_event_is_a_no_op_ack(consumer, make_infra):
    infra = make_infra()
    consumer._retry_counts = {}
    payload = {"infra_id": str(infra.id), "exited_at": "2026-01-01T00:00:00Z"}

    _deliver(consumer, payload)
    infra.refresh_from_db()
    first_value = infra.exited_at

    ch = _deliver(consumer, payload)

    ch.basic_ack.assert_called_once_with(delivery_tag=1)
    infra.refresh_from_db()
    assert infra.exited_at == first_value


# ── cross-event ordering: host_readiness_updated / infra.created never clear it ─────

@pytest.mark.django_db
def test_a_subsequent_host_readiness_event_does_not_clear_exited_at(consumer, make_infra):
    from api.messaging.consumers.infrastructure import HostReadinessEventConsumer

    infra = make_infra()
    consumer._retry_counts = {}
    _deliver(consumer, {"infra_id": str(infra.id), "exited_at": "2026-01-01T00:00:00Z"})
    infra.refresh_from_db()
    assert infra.exited_at is not None

    readiness_consumer = HostReadinessEventConsumer.__new__(HostReadinessEventConsumer)
    readiness_consumer._retry_counts = {}
    ch = MagicMock()
    method = SimpleNamespace(delivery_tag=1)
    properties = SimpleNamespace(correlation_id="cid")
    body = json.dumps({
        "type": "infrastructure.host_readiness_updated",
        "payload": {
            "infra_id": str(infra.id), "host_readiness_version": 1,
            "dns_synced": True, "https_ready": True,
        },
    }).encode()
    readiness_consumer.callback(ch, method, properties, body)

    infra.refresh_from_db()
    assert infra.exited_at is not None


@pytest.mark.django_db
def test_a_subsequent_infra_created_upsert_does_not_clear_exited_at(consumer, make_infra):
    from api.repositories.infrastructure import InfrastructureRepository

    infra = make_infra()
    consumer._retry_counts = {}
    _deliver(consumer, {"infra_id": str(infra.id), "exited_at": "2026-01-01T00:00:00Z"})
    infra.refresh_from_db()
    assert infra.exited_at is not None

    InfrastructureRepository().upsert_infrastructure({
        "id": str(infra.id), "user_id": str(infra.user_id), "name": infra.name,
        "cloud_provider": "aws", "max_cpu": 1024, "max_memory": 512,
    })

    infra.refresh_from_db()
    assert infra.exited_at is not None
