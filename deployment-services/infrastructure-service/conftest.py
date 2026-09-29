import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# Make the service root importable.
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "test_settings")


@pytest.fixture(autouse=True)
def no_real_broker(monkeypatch):
    """Tests must never open a socket to a real RabbitMQ broker.

    A test that exercises a view/service without explicitly mocking the producer (e.g.
    test_onboarding_callback.py's callback flow) used to call straight through to
    ResilientPikaProducer.connect()/publish() and land real messages — for infra ids that
    exist in no database — on whatever broker happens to be running locally. Patching at
    the CLASS level (not per-instance) covers every ResilientPikaProducer instance
    regardless of when it was constructed, including module-level singletons like
    `infra_producer` (api/messaging/producer/producer.py), which is created at import
    time, before this fixture ever runs. RABBITMQ_URL is also unroutable in test_settings
    and connect() itself refuses to run under pytest (shared/resilience/amqp.py) — this
    fixture is the primary layer; those are the backstop for anything that bypasses it.

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
