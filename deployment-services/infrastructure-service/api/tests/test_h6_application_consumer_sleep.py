"""H6 (hardening, see plan/H-hardening.md and its H2 note): ApplicationEventConsumer's
retry-on-error path used a bare `time.sleep(1)` inside its pika callback, which blocks the
whole BlockingConnection's I/O thread and starves the broker heartbeat, risking a dropped
connection under load — the same bug H2 already fixed for InfraExitedEventConsumer.
`ch.connection.sleep()` fixes it the same way.
"""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.mark.django_db
def test_application_event_consumer_retries_with_connection_sleep(monkeypatch):
    from api.messaging.consumer.application_consumer import ApplicationEventConsumer
    from api.models import Application

    monkeypatch.setattr(
        Application.objects, "update_or_create",
        MagicMock(side_effect=RuntimeError("db unavailable")),
    )

    consumer = ApplicationEventConsumer.__new__(ApplicationEventConsumer)
    ch = MagicMock()
    method = SimpleNamespace(delivery_tag=1, routing_key="application.created")
    properties = SimpleNamespace()
    body = json.dumps({
        "id": "11111111-1111-1111-1111-111111111111",
        "infrastructure_id": "22222222-2222-2222-2222-222222222222",
        "name": "app",
        "user_id": "33333333-3333-3333-3333-333333333333",
    }).encode()

    consumer.callback(ch, method, properties, body)

    ch.basic_nack.assert_called_once_with(delivery_tag=1, requeue=True)
    ch.connection.sleep.assert_called_once_with(1)
