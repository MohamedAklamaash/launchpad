"""OrgEventConsumer — F9 ticket 4's Organization/Membership read-model mirror.

Exercises the callback directly (bypassing the real ResilientPikaConsumer, which needs a
live broker), the same pattern test_host_readiness_consumer.py uses.
"""
import json
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def consumer():
    from api.messaging.consumer.org_events import OrgEventConsumer
    c = OrgEventConsumer.__new__(OrgEventConsumer)
    from api.repositories.organization import OrganizationRepository
    c.org_repo = OrganizationRepository()
    c._retry_counts = {}
    return c


def _deliver(consumer, routing_key, payload):
    ch = MagicMock()
    method = SimpleNamespace(delivery_tag=1, routing_key=routing_key)
    properties = SimpleNamespace(correlation_id="cid")
    body = json.dumps({"type": routing_key, "payload": payload}).encode()
    consumer.callback(ch, method, properties, body)
    return ch


@pytest.mark.django_db
def test_org_created_materializes_organization_and_owner_membership(consumer):
    from api.models.membership import Membership
    from api.models.organization import Organization

    org_id = str(uuid.uuid4())
    owner_id = str(uuid.uuid4())

    ch = _deliver(consumer, "org.created", {
        "organization_id": org_id, "owner_account_id": owner_id, "version": 1,
    })

    ch.basic_ack.assert_called_once_with(delivery_tag=1)
    org = Organization.objects.get(id=org_id)
    assert org.owner_account_id == uuid.UUID(owner_id)
    membership = Membership.objects.get(organization_id=org_id, account_id=owner_id)
    assert membership.role == "owner"
    assert membership.version == 1


@pytest.mark.django_db
def test_membership_changed_before_org_created_retries_then_gives_up(consumer):
    from api.messaging.consumer.org_events import OrgEventConsumer

    org_id = str(uuid.uuid4())
    account_id = str(uuid.uuid4())
    payload = {"organization_id": org_id, "account_id": account_id, "role": "developer", "version": 1}

    total_delay = 0
    for _ in range(OrgEventConsumer.MAX_RETRIES):
        ch = _deliver(consumer, "org.membership.changed", payload)
        ch.basic_nack.assert_called_once_with(delivery_tag=1, requeue=True)
        ch.connection.sleep.assert_called_once()
        total_delay += ch.connection.sleep.call_args.args[0]

    assert total_delay <= 10

    ch_final = _deliver(consumer, "org.membership.changed", payload)
    ch_final.basic_ack.assert_called_once_with(delivery_tag=1)
    ch_final.connection.sleep.assert_not_called()

    from api.models.membership import Membership
    assert not Membership.objects.filter(organization_id=org_id, account_id=account_id).exists()


@pytest.mark.django_db
def test_membership_changed_retries_then_applies_once_org_materializes(consumer):
    from api.models.membership import Membership
    from api.models.organization import Organization

    org_id = str(uuid.uuid4())
    owner_id = str(uuid.uuid4())
    account_id = str(uuid.uuid4())
    payload = {"organization_id": org_id, "account_id": account_id, "role": "admin", "version": 1}

    ch = _deliver(consumer, "org.membership.changed", payload)
    ch.basic_nack.assert_called_once_with(delivery_tag=1, requeue=True)

    Organization.objects.create(id=org_id, owner_account_id=owner_id, version=1)

    ch2 = _deliver(consumer, "org.membership.changed", payload)
    ch2.basic_ack.assert_called_once_with(delivery_tag=1)
    membership = Membership.objects.get(organization_id=org_id, account_id=account_id)
    assert membership.role == "admin"
    assert membership.version == 1


@pytest.mark.django_db
def test_out_of_order_delivery_older_version_after_newer_is_rejected(consumer):
    from api.models.membership import Membership
    from api.models.organization import Organization

    org_id = str(uuid.uuid4())
    owner_id = str(uuid.uuid4())
    account_id = str(uuid.uuid4())
    Organization.objects.create(id=org_id, owner_account_id=owner_id, version=1)

    ch_new = _deliver(consumer, "org.membership.changed", {
        "organization_id": org_id, "account_id": account_id, "role": "admin", "version": 5,
    })
    ch_new.basic_ack.assert_called_once_with(delivery_tag=1)

    # An older version arriving after the newer one must not clobber it.
    ch_old = _deliver(consumer, "org.membership.changed", {
        "organization_id": org_id, "account_id": account_id, "role": "viewer", "version": 2,
    })
    ch_old.basic_ack.assert_called_once_with(delivery_tag=1)

    membership = Membership.objects.get(organization_id=org_id, account_id=account_id)
    assert membership.role == "admin"
    assert membership.version == 5


@pytest.mark.django_db
def test_idempotent_redelivery_of_the_same_version_is_a_no_op(consumer):
    from api.models.membership import Membership
    from api.models.organization import Organization

    org_id = str(uuid.uuid4())
    owner_id = str(uuid.uuid4())
    account_id = str(uuid.uuid4())
    Organization.objects.create(id=org_id, owner_account_id=owner_id, version=1)

    payload = {"organization_id": org_id, "account_id": account_id, "role": "developer", "version": 3}
    _deliver(consumer, "org.membership.changed", payload)

    # Exact same message redelivered (e.g. broker redelivery after an ack race) — role
    # in the duplicate is deliberately different to prove it is not reapplied.
    ch = _deliver(consumer, "org.membership.changed", {**payload, "role": "viewer"})
    ch.basic_ack.assert_called_once_with(delivery_tag=1)

    membership = Membership.objects.get(organization_id=org_id, account_id=account_id)
    assert membership.role == "developer"
    assert membership.version == 3


@pytest.mark.django_db
def test_membership_removed_deletes_the_row(consumer):
    from api.models.membership import Membership
    from api.models.organization import Organization

    org_id = str(uuid.uuid4())
    owner_id = str(uuid.uuid4())
    account_id = str(uuid.uuid4())
    Organization.objects.create(id=org_id, owner_account_id=owner_id, version=1)
    Membership.objects.create(organization_id=org_id, account_id=account_id, role="viewer", version=1)

    ch = _deliver(consumer, "org.membership.removed", {
        "organization_id": org_id, "account_id": account_id, "version": 2,
    })

    ch.basic_ack.assert_called_once_with(delivery_tag=1)
    assert not Membership.objects.filter(organization_id=org_id, account_id=account_id).exists()


@pytest.mark.django_db
def test_membership_removed_with_stale_version_does_not_delete(consumer):
    from api.models.membership import Membership
    from api.models.organization import Organization

    org_id = str(uuid.uuid4())
    owner_id = str(uuid.uuid4())
    account_id = str(uuid.uuid4())
    Organization.objects.create(id=org_id, owner_account_id=owner_id, version=1)
    Membership.objects.create(organization_id=org_id, account_id=account_id, role="admin", version=5)

    # A removal older than a role change already applied must not clobber it.
    ch = _deliver(consumer, "org.membership.removed", {
        "organization_id": org_id, "account_id": account_id, "version": 3,
    })

    ch.basic_ack.assert_called_once_with(delivery_tag=1)
    membership = Membership.objects.get(organization_id=org_id, account_id=account_id)
    assert membership.role == "admin"
    assert membership.version == 5


@pytest.mark.django_db
def test_membership_removed_before_org_created_retries_then_gives_up(consumer):
    from api.messaging.consumer.org_events import OrgEventConsumer

    org_id = str(uuid.uuid4())
    account_id = str(uuid.uuid4())
    payload = {"organization_id": org_id, "account_id": account_id, "version": 1}

    for _ in range(OrgEventConsumer.MAX_RETRIES):
        ch = _deliver(consumer, "org.membership.removed", payload)
        ch.basic_nack.assert_called_once_with(delivery_tag=1, requeue=True)

    ch_final = _deliver(consumer, "org.membership.removed", payload)
    ch_final.basic_ack.assert_called_once_with(delivery_tag=1)


@pytest.mark.django_db
def test_invitation_accepted_applies_the_same_way_as_membership_changed(consumer):
    from api.models.membership import Membership
    from api.models.organization import Organization

    org_id = str(uuid.uuid4())
    owner_id = str(uuid.uuid4())
    account_id = str(uuid.uuid4())
    Organization.objects.create(id=org_id, owner_account_id=owner_id, version=1)

    ch = _deliver(consumer, "org.invitation.accepted", {
        "organization_id": org_id, "account_id": account_id, "email": "a@example.com",
        "role": "developer", "version": 1,
    })

    ch.basic_ack.assert_called_once_with(delivery_tag=1)
    membership = Membership.objects.get(organization_id=org_id, account_id=account_id)
    assert membership.role == "developer"


@pytest.mark.django_db
def test_missing_organization_id_is_discarded(consumer):
    ch = _deliver(consumer, "org.membership.changed", {"account_id": str(uuid.uuid4()), "role": "admin", "version": 1})
    ch.basic_nack.assert_called_once_with(delivery_tag=1, requeue=False)


@pytest.mark.django_db
def test_missing_version_is_discarded(consumer):
    ch = _deliver(consumer, "org.created", {
        "organization_id": str(uuid.uuid4()), "owner_account_id": str(uuid.uuid4()),
    })
    ch.basic_nack.assert_called_once_with(delivery_tag=1, requeue=False)


@pytest.mark.django_db
def test_unrecognized_role_is_discarded(consumer):
    ch = _deliver(consumer, "org.membership.changed", {
        "organization_id": str(uuid.uuid4()), "account_id": str(uuid.uuid4()),
        "role": "superuser", "version": 1,
    })
    ch.basic_nack.assert_called_once_with(delivery_tag=1, requeue=False)


def test_malformed_json_is_discarded():
    from api.messaging.consumer.org_events import OrgEventConsumer
    c = OrgEventConsumer.__new__(OrgEventConsumer)
    c._retry_counts = {}
    ch = MagicMock()
    method = SimpleNamespace(delivery_tag=1, routing_key="org.created")
    properties = SimpleNamespace(correlation_id="cid")
    c.callback(ch, method, properties, b"not json")
    ch.basic_nack.assert_called_once_with(delivery_tag=1, requeue=False)


# ── ORGS_ENABLED gating ─────────────────────────────────────────────────────────────────
#
# Split out of run_worker.Command.handle() as `_org_consumer_threads()` specifically so
# this is testable without starting a real consumer thread or running the whole command
# (which waits on a DB check and then joins its threads forever).

@pytest.mark.django_db
def test_org_consumer_not_started_when_orgs_disabled(settings):
    from api.management.commands.run_worker import _org_consumer_threads

    settings.ORGS_ENABLED = False
    assert _org_consumer_threads() == []


@pytest.mark.django_db
def test_org_consumer_thread_created_when_orgs_enabled(settings):
    from api.management.commands.run_worker import _org_consumer_threads

    settings.ORGS_ENABLED = True
    threads = _org_consumer_threads()
    assert len(threads) == 1
    assert threads[0].daemon is True
