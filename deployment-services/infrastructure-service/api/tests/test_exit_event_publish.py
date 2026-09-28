"""H2 (hardening): Complete Exit publishes infrastructure.exited so application-service's
read-model mirror learns exited_at. See plan/H-hardening.md.

Reuses test_complete_exit.py's fixtures/helpers rather than redefining them."""
import time
import uuid
from unittest.mock import MagicMock, patch

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate
from shared.utils.jwt import JWTUser


@pytest.fixture(autouse=True)
def _fake_redis(monkeypatch):
    class _Pipe:
        def __init__(self, store):
            self.store, self.ops = store, []
        def incr(self, key):
            self.ops.append(("incr", key)); return self
        def ttl(self, key):
            self.ops.append(("ttl", key)); return self
        def execute(self):
            results = []
            for op, key in self.ops:
                if op == "incr":
                    self.store.counts[key] = self.store.counts.get(key, 0) + 1
                    results.append(self.store.counts[key])
                else:
                    results.append(self.store.expiry.get(key, -1))
            return results
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class _Redis:
        def __init__(self):
            self.counts, self.expiry = {}, {}
        def pipeline(self):
            return _Pipe(self)
        def expire(self, key, seconds):
            self.expiry[key] = seconds

    fake = _Redis()
    monkeypatch.setattr("shared.ratelimit.budget._redis", lambda: fake)
    return fake


@pytest.fixture
def factory():
    return APIRequestFactory()


@pytest.fixture
def make_user(db):
    from api.models.user import User

    def _make():
        return User.objects.create(
            id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="t", role="super_admin",
        )
    return _make


@pytest.fixture
def make_infra(db, make_user):
    from api.models.infrastructure import Infrastructure

    def _make(*, owner=None):
        owner = owner or make_user()
        infra = Infrastructure.objects.create(
            user=owner, name=f"infra-{uuid.uuid4()}", cloud_provider="aws",
            max_cpu=1024, max_memory=512, code="123456789012", metadata={"aws_region": "us-east-1"},
            dns_label=uuid.uuid4().hex[:16],
        )
        return owner, infra
    return _make


def _jwt_user(user, *, auth_time=None):
    payload = {"sub": str(user.id)}
    if auth_time is not None:
        payload["auth_time"] = auth_time
    return JWTUser(**payload)


def _fresh_auth_time():
    return int(time.time())


def _post(factory, user, infra_id, body=None):
    from api.views.exit_export import infrastructure_complete_exit

    request = factory.post(f"/api/v1/infrastructures/{infra_id}/exit/", data=body or {}, format="json")
    force_authenticate(request, user=user)
    return infrastructure_complete_exit(request, infra_id=infra_id)


@patch("api.views.exit_export.request_and_await_dns_teardown")
@patch("api.messaging.producer.producer.infra_producer.publish_infrastructure_exited")
def test_complete_exit_publishes_infrastructure_exited(mock_publish, mock_teardown, factory, make_infra):
    owner, infra = make_infra()
    mock_teardown.return_value = None

    resp = _post(factory, _jwt_user(owner, auth_time=_fresh_auth_time()), str(infra.id), {"confirm": True})

    assert resp.status_code == 200
    infra.refresh_from_db()
    mock_publish.assert_called_once_with(infra.id, infra.exited_at)


@patch("api.views.exit_export.request_and_await_dns_teardown")
@patch("api.messaging.producer.producer.infra_producer.publish_infrastructure_exited")
def test_publish_failure_does_not_break_complete_exit(mock_publish, mock_teardown, factory, make_infra):
    """Best-effort, like host_readiness's own publish — a broker hiccup must never turn a
    successful exit into a 500."""
    owner, infra = make_infra()
    mock_teardown.return_value = None
    mock_publish.side_effect = RuntimeError("broker down")

    resp = _post(factory, _jwt_user(owner, auth_time=_fresh_auth_time()), str(infra.id), {"confirm": True})

    assert resp.status_code == 200
    infra.refresh_from_db()
    assert infra.exited_at is not None


@patch("api.views.exit_export.request_and_await_dns_teardown")
@patch("api.messaging.producer.producer.infra_producer.publish_infrastructure_exited")
def test_already_exited_409_republishes_the_event(mock_publish, mock_teardown, factory, make_infra):
    """Self-heal: a lost or not-yet-consumed event on the first call is a non-issue as long
    as the owner's own retry (which lands on the 409 branch) republishes it — application-
    service's consumer is idempotent (first-wins latch), so a duplicate publish is harmless."""
    owner, infra = make_infra()
    mock_teardown.return_value = None

    first = _post(factory, _jwt_user(owner, auth_time=_fresh_auth_time()), str(infra.id), {"confirm": True})
    assert first.status_code == 200
    assert mock_publish.call_count == 1

    second = _post(factory, _jwt_user(owner, auth_time=_fresh_auth_time()), str(infra.id), {"confirm": True})

    assert second.status_code == 409
    assert mock_publish.call_count == 2
    infra.refresh_from_db()
    mock_publish.assert_called_with(infra.id, infra.exited_at)


@pytest.mark.django_db
def test_republish_exited_events_command_publishes_for_every_exited_infra(make_infra):
    from django.core.management import call_command
    from django.utils import timezone

    _owner1, infra1 = make_infra()
    infra1.exited_at = timezone.now()
    infra1.save(update_fields=["exited_at"])

    _owner2, infra2 = make_infra()  # never exited — must not be republished

    with patch("api.messaging.producer.producer.infra_producer.connect") as mock_connect, \
         patch("api.messaging.producer.producer.infra_producer.publish_infrastructure_exited") as mock_publish:
        call_command("republish_exited_events")

    mock_connect.assert_called_once()
    mock_publish.assert_called_once_with(infra1.id, infra1.exited_at)
    assert infra2.id not in [c.args[0] for c in mock_publish.call_args_list]


# ── R2(a): the exited-events queue is declared/bound from the producer side too ──────

def test_connect_declares_and_binds_the_application_service_exited_queue():
    """H2 R2(a): a topic exchange silently drops a message with no matching binding, so if
    application-service's own consumer has never started, a publish before that point
    would otherwise vanish. InfraEventProducer.connect() must declare+bind that queue
    itself as a backstop."""
    from api.messaging.producer.producer import InfraEventProducer

    producer = InfraEventProducer()
    with patch.object(producer.producer, "connect"), \
         patch.object(producer.producer, "declare_queue") as declare_queue:
        producer.connect()

    declare_queue.assert_called_once_with(
        queue="application-service.infra-exited-events",
        routing_key="infrastructure.exited",
    )


def test_connect_survives_a_declare_queue_failure():
    """A broker that refuses the declare (e.g. a permissions issue, or a genuine property
    mismatch) must not take down the producer's connect() — publishing itself doesn't
    depend on this backstop having succeeded."""
    from api.messaging.producer.producer import InfraEventProducer

    producer = InfraEventProducer()
    with patch.object(producer.producer, "connect"), \
         patch.object(producer.producer, "declare_queue", side_effect=RuntimeError("boom")):
        producer.connect()  # must not raise


def test_declare_queue_matches_the_consumers_own_declare_args():
    """The declare/bind call must use IDENTICAL properties to
    ResilientPikaConsumer._connect_and_consume's own queue_declare — RabbitMQ closes the
    channel with PRECONDITION_FAILED on any mismatch (durable, exclusive, auto_delete, or
    extra `arguments`), which would break the consumer's very next reconnect."""
    from shared.resilience.amqp import ResilientPikaProducer

    producer = ResilientPikaProducer(url="amqp://localhost/", exchange="infrastructure.events")
    producer.channel = MagicMock()

    producer.declare_queue(queue="application-service.infra-exited-events", routing_key="infrastructure.exited")

    producer.channel.queue_declare.assert_called_once_with(
        queue="application-service.infra-exited-events",
        durable=True, exclusive=False, auto_delete=False,
    )
    producer.channel.queue_bind.assert_called_once_with(
        queue="application-service.infra-exited-events",
        exchange="infrastructure.events", routing_key="infrastructure.exited",
    )


def test_declare_queue_args_match_the_consumers_live_declare_call():
    """Cross-checks against the consumer's OWN queue_declare call (not a hardcoded copy of
    its kwargs), so this fails the moment either side's declare properties drift —
    catching exactly the class of bug a hardcoded-literal test like the one above cannot."""
    from unittest.mock import MagicMock as _MM

    import pika
    from shared.resilience.amqp import ResilientPikaConsumer, ResilientPikaProducer

    consumer_queue_declare_kwargs = {}

    class _FakeChannel:
        def exchange_declare(self, **kw): pass
        def queue_declare(self, **kw): consumer_queue_declare_kwargs.update(kw)
        def queue_bind(self, **kw): pass
        def basic_qos(self, **kw): pass
        def basic_consume(self, **kw): pass
        def start_consuming(self): pass

    class _FakeConnection:
        is_closed = False
        def channel(self): return _FakeChannel()

    with patch.object(pika, "BlockingConnection", return_value=_FakeConnection()), \
         patch.object(pika, "URLParameters"):
        consumer = ResilientPikaConsumer(
            url="amqp://localhost/", exchange="infrastructure.events",
            queue="application-service.infra-exited-events", routing_key="infrastructure.exited",
        )
        consumer._connect_and_consume(lambda *a: None)

    producer = ResilientPikaProducer(url="amqp://localhost/", exchange="infrastructure.events")
    producer.channel = _MM()
    producer.declare_queue(queue="application-service.infra-exited-events", routing_key="infrastructure.exited")
    producer_queue_declare_kwargs = dict(producer.channel.queue_declare.call_args.kwargs)

    assert consumer_queue_declare_kwargs == producer_queue_declare_kwargs


def test_declare_queue_before_connect_raises():
    from shared.resilience.amqp import ResilientPikaProducer

    producer = ResilientPikaProducer(url="amqp://localhost/", exchange="infrastructure.events")
    with pytest.raises(RuntimeError):
        producer.declare_queue(queue="q", routing_key="rk")


# ── R2(b): a periodic worker tick republishes as a backstop ──────────────────────────

@pytest.mark.django_db
def test_republish_all_exited_infrastructures_connects_and_publishes_every_exited_infra(make_infra):
    from api.services.exit_republish import republish_all_exited_infrastructures

    _owner1, infra1 = make_infra()
    from django.utils import timezone
    infra1.exited_at = timezone.now()
    infra1.save(update_fields=["exited_at"])
    _owner2, infra2 = make_infra()  # never exited

    with patch("api.messaging.producer.producer.infra_producer.connect") as mock_connect, \
         patch("api.messaging.producer.producer.infra_producer.publish_infrastructure_exited") as mock_publish:
        count = republish_all_exited_infrastructures()

    mock_connect.assert_called_once()
    assert count == 1
    mock_publish.assert_called_once_with(infra1.id, infra1.exited_at)
    assert infra2.id not in [c.args[0] for c in mock_publish.call_args_list]


def test_worker_declares_the_exited_republish_interval():
    """The tick's own wiring lives inline in Command.handle's dispatch loop, matching how
    the reap and cert-check ticks are tested in this codebase — only the underlying
    function they call (republish_all_exited_infrastructures, tested above) is unit
    tested, not the while-loop wiring around it. This just pins the constant exists and is
    a sane default."""
    import importlib

    run_worker = importlib.import_module("api.management.commands.run_worker")
    assert run_worker.EXITED_EVENT_REPUBLISH_INTERVAL_SECONDS > 0


@pytest.mark.django_db
def test_republish_exited_events_fails_loudly_when_the_broker_is_unreachable(make_infra):
    """publish() itself swallows a connect failure and just leaves the message buffered —
    the command must connect explicitly up front so an unreachable broker is a hard
    failure, not a false 'republished N' success report."""
    from django.core.management import call_command
    from django.utils import timezone

    _owner, infra = make_infra()
    infra.exited_at = timezone.now()
    infra.save(update_fields=["exited_at"])

    with patch("api.messaging.producer.producer.infra_producer.connect", side_effect=RuntimeError("broker down")), \
         pytest.raises(RuntimeError):
        call_command("republish_exited_events")


@pytest.mark.django_db
def test_republish_exited_events_dry_run_does_not_publish(make_infra):
    from django.core.management import call_command
    from django.utils import timezone

    _owner, infra = make_infra()
    infra.exited_at = timezone.now()
    infra.save(update_fields=["exited_at"])

    with patch("api.messaging.producer.producer.infra_producer.publish_infrastructure_exited") as mock_publish:
        call_command("republish_exited_events", "--dry-run")

    mock_publish.assert_not_called()
