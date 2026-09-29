"""HostReadinessEventConsumer — F1b part 3a's cross-service read-model mirror.

Exercises the callback directly (bypassing the real ResilientPikaConsumer, which needs a
live broker) with a fake channel/method/properties, the same pattern the rest of this
codebase's consumer tests use.
"""
import json
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def consumer():
    from api.messaging.consumers.infrastructure import HostReadinessEventConsumer
    return HostReadinessEventConsumer.__new__(HostReadinessEventConsumer)


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
        "type": "infrastructure.host_readiness_updated",
        "payload": payload,
    }).encode()
    consumer.callback(ch, method, properties, body)
    return ch


@pytest.mark.django_db
def test_missing_infra_id_is_discarded(consumer):
    ch = _deliver(consumer, {"host_readiness_version": 1})
    ch.basic_nack.assert_called_once_with(delivery_tag=1, requeue=False)


# ── RECOMMENDED item 2: host_readiness_version is required ──────────────────────────────

@pytest.mark.django_db
def test_missing_host_readiness_version_is_discarded(consumer, make_infra):
    infra = make_infra()
    ch = _deliver(consumer, {"infra_id": str(infra.id), "dns_synced": True, "https_ready": True})
    ch.basic_nack.assert_called_once_with(delivery_tag=1, requeue=False)
    infra.refresh_from_db()
    assert infra.dns_synced is False


@pytest.mark.django_db
def test_non_integer_host_readiness_version_is_discarded(consumer, make_infra):
    infra = make_infra()
    ch = _deliver(consumer, {
        "infra_id": str(infra.id), "host_readiness_version": "not-a-number",
        "dns_synced": True, "https_ready": True,
    })
    ch.basic_nack.assert_called_once_with(delivery_tag=1, requeue=False)
    infra.refresh_from_db()
    assert infra.dns_synced is False


@pytest.mark.django_db
def test_unmaterialized_infra_is_requeued_then_ackable_once_it_exists(consumer, make_infra):
    infra_id = str(uuid.uuid4())
    consumer._retry_counts = {}

    payload = {"infra_id": infra_id, "host_readiness_version": 1, "dns_synced": True, "https_ready": True}
    ch = _deliver(consumer, payload)
    ch.basic_nack.assert_called_once_with(delivery_tag=1, requeue=True)
    # H6 (hardening): the retry delay must use ch.connection.sleep, never a bare
    # time.sleep — this callback runs on the pika BlockingConnection's own I/O thread,
    # and a bare sleep there starves the heartbeat and can drop the connection.
    ch.connection.sleep.assert_called_once_with(1)

    infra = make_infra(id=infra_id)
    ch2 = _deliver(consumer, payload)
    ch2.basic_ack.assert_called_once_with(delivery_tag=1)
    infra.refresh_from_db()
    assert infra.dns_synced is True
    assert infra.https_ready is True
    assert infra.host_readiness_version == 1


@pytest.mark.django_db
def test_unknown_infra_is_discarded_after_a_short_budget_and_the_queue_moves_on(consumer, make_infra):
    """A junk infra id (e.g. published by a test suite that leaked onto the real broker —
    see conftest.py's no_real_broker fixture) must not hold this prefetch=1 queue for
    minutes: MAX_RETRIES=3 with a capped exponential delay bounds the total wait to a few
    seconds before the event is discarded, and the next message is processed normally."""
    from api.messaging.consumers.infrastructure import HostReadinessEventConsumer

    unknown_infra_id = str(uuid.uuid4())
    consumer._retry_counts = {}
    payload = {"infra_id": unknown_infra_id, "host_readiness_version": 1, "dns_synced": True, "https_ready": True}

    total_delay = 0
    for attempt in range(HostReadinessEventConsumer.MAX_RETRIES):
        ch = _deliver(consumer, payload)
        ch.basic_nack.assert_called_once_with(delivery_tag=1, requeue=True)
        ch.connection.sleep.assert_called_once()
        total_delay += ch.connection.sleep.call_args.args[0]

    assert total_delay <= 10, "unknown-infra retries must total only a few seconds, not minutes"

    # One more delivery past the budget: discarded outright, not requeued again.
    ch_final = _deliver(consumer, payload)
    ch_final.basic_nack.assert_called_once_with(delivery_tag=1, requeue=False)
    ch_final.connection.sleep.assert_not_called()
    assert unknown_infra_id not in consumer._retry_counts

    # The queue is not stalled: the very next message, for a real infra, is processed.
    infra = make_infra()
    ch_next = _deliver(consumer, {
        "infra_id": str(infra.id), "host_readiness_version": 1, "dns_synced": True, "https_ready": True,
    })
    ch_next.basic_ack.assert_called_once_with(delivery_tag=1)
    infra.refresh_from_db()
    assert infra.dns_synced is True


@pytest.mark.django_db
def test_applies_dns_label_and_tls_status_and_acks(consumer, make_infra):
    infra = make_infra()
    consumer._retry_counts = {}

    ch = _deliver(consumer, {
        "infra_id": str(infra.id), "host_readiness_version": 1, "dns_label": "0123456789abcdef",
        "tls_status": "ISSUED", "dns_synced": True, "https_ready": True,
    })

    ch.basic_ack.assert_called_once_with(delivery_tag=1)
    infra.refresh_from_db()
    assert infra.dns_label == "0123456789abcdef"
    assert infra.tls_status == "ISSUED"
    assert infra.dns_synced is True
    assert infra.https_ready is True
    assert infra.host_readiness_version == 1


@pytest.mark.django_db
def test_dns_label_is_never_cleared_once_set(consumer, make_infra):
    infra = make_infra(dns_label="0123456789abcdef")
    consumer._retry_counts = {}

    _deliver(consumer, {"infra_id": str(infra.id), "host_readiness_version": 1, "dns_synced": True, "https_ready": True})

    infra.refresh_from_db()
    assert infra.dns_label == "0123456789abcdef"


@pytest.mark.django_db
def test_stale_event_does_not_overwrite_a_fresher_snapshot(consumer, make_infra):
    infra = make_infra()
    consumer._retry_counts = {}

    _deliver(consumer, {
        "infra_id": str(infra.id), "host_readiness_version": 5,
        "tls_status": "FAILED", "dns_synced": False, "https_ready": False,
    })
    infra.refresh_from_db()
    assert infra.tls_status == "FAILED"
    assert infra.host_readiness_version == 5

    # A lower version (e.g. redelivered, or published by a publisher that read a stale
    # in-memory counter) must not clobber the newer FAILED with an older ISSUED.
    ch = _deliver(consumer, {
        "infra_id": str(infra.id), "host_readiness_version": 2,
        "tls_status": "ISSUED", "dns_synced": True, "https_ready": True,
    })

    ch.basic_ack.assert_called_once_with(delivery_tag=1)
    infra.refresh_from_db()
    assert infra.tls_status == "FAILED"
    assert infra.dns_synced is False
    assert infra.host_readiness_version == 5


@pytest.mark.django_db
def test_equal_version_is_treated_as_stale_and_not_reapplied(consumer, make_infra):
    """Redelivery of the exact same message must not re-run the update — equal version
    means 'already applied', not 'apply again'."""
    infra = make_infra()
    consumer._retry_counts = {}
    payload = {
        "infra_id": str(infra.id), "host_readiness_version": 3,
        "tls_status": "ISSUED", "dns_synced": True, "https_ready": True,
    }
    _deliver(consumer, payload)

    ch = _deliver(consumer, {**payload, "tls_status": "FAILED"})

    ch.basic_ack.assert_called_once_with(delivery_tag=1)
    infra.refresh_from_db()
    assert infra.tls_status == "ISSUED"
