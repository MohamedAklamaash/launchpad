"""Structural proof for the root conftest.py `no_real_broker` fixture: application-service
also holds a module-level ResilientPikaProducer singleton (`_producer` in
api/messaging/producer/producer.py) that a test exercising ApplicationEventProducer
without mocking it would previously have published through for real.
"""
import pika
import pytest
from shared.resilience.amqp import ResilientPikaProducer

# Captured at collection time, before the per-test `no_real_broker` fixture monkeypatches
# ResilientPikaProducer.connect — this reference always points at the real implementation,
# regardless of what's currently patched on the class.
_real_connect = ResilientPikaProducer.connect


def _fail_if_dialed(*args, **kwargs):
    raise AssertionError(
        "pika.BlockingConnection was invoked during a test — a real broker connection "
        "was attempted instead of going through the no_real_broker recorder"
    )


def test_connect_guard_refuses_a_real_connection_under_pytest(monkeypatch):
    """Belt-and-suspenders backstop (shared/resilience/amqp.py) for anything that bypasses
    the no_real_broker fixture: the real, unpatched connect() must refuse to run at all
    under pytest rather than falling through to pika.BlockingConnection."""
    monkeypatch.setattr(pika, "BlockingConnection", _fail_if_dialed)
    producer = ResilientPikaProducer(url="amqp://guest:guest@localhost:5672/", exchange="application_events")

    with pytest.raises(RuntimeError, match="under pytest"):
        _real_connect(producer)


def test_producer_publish_never_opens_a_real_connection(monkeypatch, no_real_broker):
    monkeypatch.setattr(pika, "BlockingConnection", _fail_if_dialed)

    producer = ResilientPikaProducer(url="amqp://guest:guest@localhost:5672/", exchange="application_events")
    producer.connect()
    producer.publish(routing_key="application.created", body={"id": "test-app"})

    assert len(no_real_broker) == 1
    assert no_real_broker[0].routing_key == "application.created"


@pytest.mark.django_db
def test_application_event_producer_singleton_is_covered_even_though_it_was_built_at_import_time(
    monkeypatch, no_real_broker
):
    """`_producer` (api/messaging/producer/producer.py) is a module-level singleton
    constructed the first time anything imports that module — long before this fixture
    runs for a given test. The class-level patch still covers it."""
    from api.messaging.producer.producer import ApplicationEventProducer

    monkeypatch.setattr(pika, "BlockingConnection", _fail_if_dialed)

    ApplicationEventProducer.publish_application_created(
        app_id="00000000-0000-0000-0000-000000000001",
        infrastructure_id="00000000-0000-0000-0000-000000000002",
        name="test-app",
        user_id="00000000-0000-0000-0000-000000000003",
    )

    assert any(m.routing_key == "application.created" for m in no_real_broker)
