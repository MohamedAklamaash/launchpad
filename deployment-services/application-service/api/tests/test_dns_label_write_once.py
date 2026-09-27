"""R4 (security review): dns_label is write-once in the application-service read-model.

It feeds directly into every hostname this service builds (api/common/host_url.py), so
neither ingestion path (infrastructure.created's upsert_infrastructure, or
infrastructure.host_readiness_updated's own consumer) may let a later event with a
DIFFERENT value silently repoint an infra's hostname namespace once one is already stored.
"""
import json
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def make_user(schema_db):
    from api.models.user import User

    def _make():
        return User.objects.create(
            id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="tester",
        )

    return _make


@pytest.fixture
def repo():
    from api.repositories.infrastructure import InfrastructureRepository
    return InfrastructureRepository()


def _base_payload(user_id, infra_id, **overrides):
    payload = {
        "id": str(infra_id), "user_id": str(user_id), "name": "infra-x",
        "cloud_provider": "aws", "max_cpu": 1.0, "max_memory": 2.0,
    }
    payload.update(overrides)
    return payload


# ── upsert_infrastructure (infrastructure.created) ──────────────────────────────────────

@pytest.mark.django_db
def test_dns_label_is_set_on_first_write(repo, make_user):
    user = make_user()
    infra, _ = repo.upsert_infrastructure(_base_payload(user.id, uuid.uuid4(), dns_label="0123456789abcdef"))
    assert infra.dns_label == "0123456789abcdef"


@pytest.mark.django_db
def test_dns_label_cannot_be_overwritten_by_a_different_value(repo, make_user, caplog):
    user = make_user()
    infra_id = uuid.uuid4()
    repo.upsert_infrastructure(_base_payload(user.id, infra_id, dns_label="0123456789abcdef"))

    infra2, _ = repo.upsert_infrastructure(_base_payload(user.id, infra_id, dns_label="ffffffffffffffff"))

    assert infra2.dns_label == "0123456789abcdef"
    infra2.refresh_from_db()
    assert infra2.dns_label == "0123456789abcdef"


@pytest.mark.django_db
def test_dns_label_write_with_the_same_value_is_a_noop(repo, make_user):
    user = make_user()
    infra_id = uuid.uuid4()
    repo.upsert_infrastructure(_base_payload(user.id, infra_id, dns_label="0123456789abcdef"))

    infra2, _ = repo.upsert_infrastructure(_base_payload(user.id, infra_id, dns_label="0123456789abcdef"))

    assert infra2.dns_label == "0123456789abcdef"


@pytest.mark.django_db
def test_dns_label_omitted_on_a_later_event_leaves_the_stored_value_untouched(repo, make_user):
    user = make_user()
    infra_id = uuid.uuid4()
    repo.upsert_infrastructure(_base_payload(user.id, infra_id, dns_label="0123456789abcdef"))

    infra2, _ = repo.upsert_infrastructure(_base_payload(user.id, infra_id))

    assert infra2.dns_label == "0123456789abcdef"


@pytest.mark.django_db
def test_malformed_dns_label_is_rejected_on_first_write(repo, make_user):
    user = make_user()
    infra, _ = repo.upsert_infrastructure(
        _base_payload(user.id, uuid.uuid4(), dns_label="../../etc/passwd"),
    )
    assert infra.dns_label is None


# ── HostReadinessEventConsumer (infrastructure.host_readiness_updated) ──────────────────

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
            "id": uuid.uuid4(), "user": user, "name": f"infra-{uuid.uuid4()}",
            "cloud_provider": "aws", "max_cpu": 1024, "max_memory": 512,
        }
        defaults.update(overrides)
        return Infrastructure.objects.create(**defaults)
    return _make


def _deliver(consumer, payload):
    ch = MagicMock()
    method = SimpleNamespace(delivery_tag=1)
    properties = SimpleNamespace(correlation_id="cid")
    body = json.dumps({
        "type": "infrastructure.host_readiness_updated", "payload": payload,
    }).encode()
    consumer.callback(ch, method, properties, body)
    return ch


@pytest.mark.django_db
def test_host_readiness_event_cannot_overwrite_an_existing_dns_label(consumer, make_infra):
    infra = make_infra(dns_label="0123456789abcdef")
    consumer._retry_counts = {}

    ch = _deliver(consumer, {
        "infra_id": str(infra.id), "host_readiness_version": 1, "dns_label": "ffffffffffffffff",
        "dns_synced": True, "https_ready": True,
    })

    ch.basic_ack.assert_called_once_with(delivery_tag=1)
    infra.refresh_from_db()
    assert infra.dns_label == "0123456789abcdef"
    # The rest of the snapshot still applies — only dns_label is refused.
    assert infra.dns_synced is True
    assert infra.https_ready is True


@pytest.mark.django_db
def test_host_readiness_event_rejects_a_malformed_dns_label(consumer, make_infra):
    infra = make_infra(dns_label=None)
    consumer._retry_counts = {}

    _deliver(consumer, {
        "infra_id": str(infra.id), "host_readiness_version": 1,
        "dns_label": "; DROP TABLE api_infrastructure;",
        "dns_synced": True, "https_ready": True,
    })

    infra.refresh_from_db()
    assert infra.dns_label is None
