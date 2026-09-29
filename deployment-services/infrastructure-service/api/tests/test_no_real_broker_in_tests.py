"""Structural proof for the root conftest.py `no_real_broker` fixture: reproduced live on
the developer's machine, running test_onboarding_callback.py alone (before this fix)
landed real messages on application-service.infra-events for infra ids that exist in no
database, because that test exercises the onboarding callback view without mocking
`infra_producer` and ResilientPikaProducer.connect()/publish() fell through to a real
`pika.BlockingConnection` against the local broker.

These tests prove publishing during a test can no longer reach the network at all: they
replace `pika.BlockingConnection` with something that fails the test if called, then
exercise the exact code paths that used to leak.
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
    producer = ResilientPikaProducer(url="amqp://guest:guest@localhost:5672/", exchange="infrastructure.events")

    with pytest.raises(RuntimeError, match="under pytest"):
        _real_connect(producer)


def test_producer_publish_never_opens_a_real_connection(monkeypatch, no_real_broker):
    monkeypatch.setattr(pika, "BlockingConnection", _fail_if_dialed)

    producer = ResilientPikaProducer(url="amqp://guest:guest@localhost:5672/", exchange="infrastructure.events")
    producer.connect()
    producer.publish(routing_key="infrastructure.created", body={"id": "test-infra"})

    assert len(no_real_broker) == 1
    assert no_real_broker[0].routing_key == "infrastructure.created"


def test_infra_producer_singleton_is_covered_even_though_it_was_built_at_import_time(monkeypatch, no_real_broker):
    """`infra_producer` (api/messaging/producer/producer.py) is a module-level singleton
    constructed the first time anything imports that module — long before this fixture
    runs for a given test. The class-level patch still covers it: this is exactly the
    object the onboarding callback view calls in production and in
    test_onboarding_callback.py."""
    from api.messaging.producer.producer import infra_producer

    monkeypatch.setattr(pika, "BlockingConnection", _fail_if_dialed)

    infra_producer.publish_infra_created(user_id="00000000-0000-0000-0000-000000000001", infra_id="test-infra")

    assert any(m.routing_key == "infrastructure.created" for m in no_real_broker)


@pytest.mark.django_db
def test_onboarding_callback_does_not_touch_the_real_broker(monkeypatch, no_real_broker):
    """End-to-end reproduction of the actual leak site: the onboarding callback view, run
    the same way test_onboarding_callback.py's happy-path test exercises it (which does
    NOT mock `infra_producer`), must publish only into the recorder rather than opening a
    real connection."""
    import sys
    import types
    import uuid
    from unittest.mock import MagicMock, patch

    from api.models.infrastructure import Infrastructure
    from api.models.user import User
    from api.views.infrastructure import infrastructure_onboarding_callback
    from rest_framework.test import APIRequestFactory

    previous = sys.modules.get("api.services.infra_queue")
    fake = types.ModuleType("api.services.infra_queue")
    fake.InfraQueue = MagicMock()
    sys.modules["api.services.infra_queue"] = fake
    monkeypatch.setattr(pika, "BlockingConnection", _fail_if_dialed)

    try:
        user = User.objects.create(
            id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="tester",
        )
        infra = Infrastructure.objects.create(
            user=user, name="infra", cloud_provider="aws", max_cpu=1024, max_memory=512,
            code="123456789012",
        )
        token = infra.issue_onboarding_token()

        factory = APIRequestFactory()
        request = factory.post(
            "/api/v1/infrastructures/onboarding/callback/",
            {"infra_id": str(infra.id), "account_id": "123456789012", "onboarding_token": token},
            format="json",
        )

        with patch("api.cloud_providers.aws.authenticate.authenticate_infrastructure") as mock_auth, \
             patch("api.services.infra_queue.InfraQueue"):
            mock_auth.return_value = None
            response = infrastructure_onboarding_callback(request)
    finally:
        if previous is not None:
            sys.modules["api.services.infra_queue"] = previous
        else:
            sys.modules.pop("api.services.infra_queue", None)

    assert response.status_code == 202
    assert any(m.routing_key == "infrastructure.created" for m in no_real_broker)
