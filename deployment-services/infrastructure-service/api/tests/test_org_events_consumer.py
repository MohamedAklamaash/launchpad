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
    from api.messaging.consumer.org_consumer import OrgEventConsumer
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
    from api.messaging.consumer.org_consumer import OrgEventConsumer

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
def test_membership_removed_tombstones_the_row_without_deleting_it(consumer):
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
    # The row still exists (a hard delete would leave nothing for a stale, redelivered
    # org.membership.changed to be rejected against -- see the resurrection test below),
    # but it is excluded from the active-membership helper ticket 7 will use.
    membership = Membership.objects.get(organization_id=org_id, account_id=account_id)
    assert membership.removed_at is not None
    assert membership.version == 2
    assert not Membership.objects.active().filter(organization_id=org_id, account_id=account_id).exists()


@pytest.mark.django_db
def test_membership_removed_with_stale_version_does_not_apply(consumer):
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
    assert membership.removed_at is None


@pytest.mark.django_db
def test_stale_membership_changed_after_removal_does_not_resurrect_the_member(consumer):
    """The exact privilege-retention bug this fix closes: a stale org.membership.changed
    (e.g. redelivered after a broker/consumer hiccup) that was published BEFORE a
    subsequent org.membership.removed must not hand the removed member their role back
    just because it arrives after the removal was applied."""
    from api.models.membership import Membership
    from api.models.organization import Organization

    org_id = str(uuid.uuid4())
    owner_id = str(uuid.uuid4())
    account_id = str(uuid.uuid4())
    Organization.objects.create(id=org_id, owner_account_id=owner_id, version=1)

    _deliver(consumer, "org.membership.changed", {
        "organization_id": org_id, "account_id": account_id, "role": "admin", "version": 1,
    })
    _deliver(consumer, "org.membership.removed", {
        "organization_id": org_id, "account_id": account_id, "version": 2,
    })

    # The stale v1 "admin" grant redelivered after the v2 removal.
    ch = _deliver(consumer, "org.membership.changed", {
        "organization_id": org_id, "account_id": account_id, "role": "admin", "version": 1,
    })

    ch.basic_ack.assert_called_once_with(delivery_tag=1)
    membership = Membership.objects.get(organization_id=org_id, account_id=account_id)
    assert membership.removed_at is not None, "a stale event must not revive a tombstoned membership"
    assert membership.version == 2
    assert not Membership.objects.active().filter(organization_id=org_id, account_id=account_id).exists()


@pytest.mark.django_db
def test_membership_readded_with_higher_version_revives_the_tombstone(consumer):
    from api.models.membership import Membership
    from api.models.organization import Organization

    org_id = str(uuid.uuid4())
    owner_id = str(uuid.uuid4())
    account_id = str(uuid.uuid4())
    Organization.objects.create(id=org_id, owner_account_id=owner_id, version=1)

    _deliver(consumer, "org.membership.changed", {
        "organization_id": org_id, "account_id": account_id, "role": "admin", "version": 1,
    })
    _deliver(consumer, "org.membership.removed", {
        "organization_id": org_id, "account_id": account_id, "version": 2,
    })

    # A genuine re-add: a fresh org.membership.changed with a version higher than the
    # tombstone's.
    ch = _deliver(consumer, "org.membership.changed", {
        "organization_id": org_id, "account_id": account_id, "role": "viewer", "version": 3,
    })

    ch.basic_ack.assert_called_once_with(delivery_tag=1)
    membership = Membership.objects.get(organization_id=org_id, account_id=account_id)
    assert membership.removed_at is None
    assert membership.role == "viewer"
    assert membership.version == 3
    assert Membership.objects.active().filter(organization_id=org_id, account_id=account_id).exists()


@pytest.mark.django_db
def test_active_queryset_excludes_tombstoned_memberships(consumer):
    from api.models.membership import Membership
    from api.models.organization import Organization

    org_id = str(uuid.uuid4())
    owner_id = str(uuid.uuid4())
    account_id = str(uuid.uuid4())
    Organization.objects.create(id=org_id, owner_account_id=owner_id, version=1)
    Membership.objects.create(organization_id=org_id, account_id=account_id, role="developer", version=1)

    assert Membership.objects.active().filter(organization_id=org_id).count() == 1

    _deliver(consumer, "org.membership.removed", {
        "organization_id": org_id, "account_id": account_id, "version": 2,
    })

    assert Membership.objects.filter(organization_id=org_id).count() == 1
    assert Membership.objects.active().filter(organization_id=org_id).count() == 0


@pytest.mark.django_db
def test_membership_removed_before_org_created_retries_then_gives_up(consumer):
    from api.messaging.consumer.org_consumer import OrgEventConsumer

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
    from api.messaging.consumer.org_consumer import OrgEventConsumer
    c = OrgEventConsumer.__new__(OrgEventConsumer)
    c._retry_counts = {}
    ch = MagicMock()
    method = SimpleNamespace(delivery_tag=1, routing_key="org.created")
    properties = SimpleNamespace(correlation_id="cid")
    c.callback(ch, method, properties, b"not json")
    ch.basic_nack.assert_called_once_with(delivery_tag=1, requeue=False)


# ── ORGS_ENABLED gating ─────────────────────────────────────────────────────────────────

def test_consumer_not_started_when_orgs_disabled(monkeypatch):
    from api.messaging.consumer import org_consumer

    monkeypatch.setattr(org_consumer.settings, "ORGS_ENABLED", False)
    thread_cls = MagicMock()
    monkeypatch.setattr(org_consumer.threading, "Thread", thread_cls)

    org_consumer.start_org_event_consumer()

    thread_cls.assert_not_called()


def test_consumer_started_when_orgs_enabled(monkeypatch):
    from api.messaging.consumer import org_consumer

    monkeypatch.setattr(org_consumer.settings, "ORGS_ENABLED", True)
    thread_cls = MagicMock()
    monkeypatch.setattr(org_consumer.threading, "Thread", thread_cls)

    org_consumer.start_org_event_consumer()

    thread_cls.assert_called_once()
    assert thread_cls.call_args.kwargs.get("daemon") is True


# ── OrgPermissions cache eviction (F9 ticket 5) ──────────────────────────────────────────

@pytest.mark.django_db
def test_org_created_evicts_the_owners_cached_role(consumer, monkeypatch):
    from api.messaging.consumer import org_consumer

    evict_calls = []
    monkeypatch.setattr(org_consumer.OrgPermissions, "evict", lambda org_id, account_id: evict_calls.append((org_id, account_id)))

    org_id = str(uuid.uuid4())
    owner_id = str(uuid.uuid4())
    _deliver(consumer, "org.created", {"organization_id": org_id, "owner_account_id": owner_id, "version": 1})

    assert evict_calls == [(org_id, owner_id)]


@pytest.mark.django_db
def test_membership_changed_evicts_the_targets_cached_role(consumer, monkeypatch):
    from api.messaging.consumer import org_consumer

    evict_calls = []
    monkeypatch.setattr(org_consumer.OrgPermissions, "evict", lambda org_id, account_id: evict_calls.append((org_id, account_id)))

    org_id = str(uuid.uuid4())
    owner_id = str(uuid.uuid4())
    account_id = str(uuid.uuid4())
    _deliver(consumer, "org.created", {"organization_id": org_id, "owner_account_id": owner_id, "version": 1})
    evict_calls.clear()

    _deliver(consumer, "org.membership.changed", {
        "organization_id": org_id, "account_id": account_id, "role": "developer", "version": 1,
    })

    assert evict_calls == [(org_id, account_id)]


@pytest.mark.django_db
def test_membership_removed_evicts_the_targets_cached_role(consumer, monkeypatch):
    from api.messaging.consumer import org_consumer

    org_id = str(uuid.uuid4())
    owner_id = str(uuid.uuid4())
    account_id = str(uuid.uuid4())
    _deliver(consumer, "org.created", {"organization_id": org_id, "owner_account_id": owner_id, "version": 1})
    _deliver(consumer, "org.membership.changed", {
        "organization_id": org_id, "account_id": account_id, "role": "developer", "version": 1,
    })

    evict_calls = []
    monkeypatch.setattr(org_consumer.OrgPermissions, "evict", lambda org_id, account_id: evict_calls.append((org_id, account_id)))

    _deliver(consumer, "org.membership.removed", {
        "organization_id": org_id, "account_id": account_id, "version": 2,
    })

    assert evict_calls == [(org_id, account_id)]


@pytest.mark.django_db
def test_eviction_is_best_effort_at_the_org_permissions_layer_not_the_consumer(consumer, monkeypatch):
    """The consumer callback has no try/except of its own around the eviction call —
    it relies on OrgPermissions.evict/org_role_cache catching redis.RedisError
    internally (proven directly in test_org_permissions.py). A real Redis outage during
    eviction therefore never nacks an event whose DB write already committed."""
    import redis as redis_module

    def _boom():
        raise redis_module.exceptions.ConnectionError("no route to redis")

    monkeypatch.setattr("shared.services.org_role_cache._redis", _boom)

    org_id = str(uuid.uuid4())
    owner_id = str(uuid.uuid4())
    ch = _deliver(consumer, "org.created", {
        "organization_id": org_id, "owner_account_id": owner_id, "version": 1,
    })

    ch.basic_ack.assert_called_once_with(delivery_tag=1)
    ch.basic_nack.assert_not_called()


# ── F9 ticket 8: ownership transfer arrives as two independent membership events ──────────

def _transfer_setup(consumer):
    org_id = str(uuid.uuid4())
    old_owner, new_owner = str(uuid.uuid4()), str(uuid.uuid4())
    _deliver(consumer, "org.created", {"organization_id": org_id, "owner_account_id": old_owner, "version": 1})
    _deliver(consumer, "org.membership.changed", {
        "organization_id": org_id, "account_id": new_owner, "role": "developer", "version": 1,
    })
    return org_id, old_owner, new_owner


def _active_owners(org_id):
    from api.models.membership import Membership
    return {
        str(a) for a in Membership.objects.active().filter(organization_id=org_id, role="owner")
        .values_list("account_id", flat=True)
    }


def _owner_column(org_id):
    from api.models.organization import Organization
    return str(Organization.objects.get(id=org_id).owner_account_id)


@pytest.mark.django_db
def test_transfer_published_demote_first_never_shows_two_owners_and_ends_with_the_new_owner(consumer):
    org_id, old_owner, new_owner = _transfer_setup(consumer)

    _deliver(consumer, "org.membership.changed", {
        "organization_id": org_id, "account_id": old_owner, "role": "admin", "version": 2,
    })
    assert _active_owners(org_id) == set()
    assert _owner_column(org_id) == old_owner

    _deliver(consumer, "org.membership.changed", {
        "organization_id": org_id, "account_id": new_owner, "role": "owner", "version": 2,
    })
    assert _active_owners(org_id) == {new_owner}
    assert _owner_column(org_id) == new_owner


@pytest.mark.django_db
def test_transfer_events_applied_in_the_other_order_converge_to_the_same_single_owner(consumer):
    org_id, old_owner, new_owner = _transfer_setup(consumer)

    _deliver(consumer, "org.membership.changed", {
        "organization_id": org_id, "account_id": new_owner, "role": "owner", "version": 2,
    })
    assert _active_owners(org_id) == {old_owner, new_owner}
    assert _owner_column(org_id) == old_owner

    _deliver(consumer, "org.membership.changed", {
        "organization_id": org_id, "account_id": old_owner, "role": "admin", "version": 2,
    })
    assert _active_owners(org_id) == {new_owner}
    assert _owner_column(org_id) == new_owner


@pytest.mark.django_db
def test_second_transfer_back_moves_the_owner_column_even_with_a_lower_membership_version(consumer):
    org_id, old_owner, new_owner = _transfer_setup(consumer)
    for version in (2, 3, 4, 5):
        _deliver(consumer, "org.membership.changed", {
            "organization_id": org_id, "account_id": new_owner, "role": "developer", "version": version,
        })
    _deliver(consumer, "org.membership.changed", {
        "organization_id": org_id, "account_id": old_owner, "role": "admin", "version": 2,
    })
    _deliver(consumer, "org.membership.changed", {
        "organization_id": org_id, "account_id": new_owner, "role": "owner", "version": 6,
    })
    assert _owner_column(org_id) == new_owner

    _deliver(consumer, "org.membership.changed", {
        "organization_id": org_id, "account_id": new_owner, "role": "admin", "version": 7,
    })
    _deliver(consumer, "org.membership.changed", {
        "organization_id": org_id, "account_id": old_owner, "role": "owner", "version": 3,
    })

    assert _active_owners(org_id) == {old_owner}
    assert _owner_column(org_id) == old_owner


@pytest.mark.django_db
def test_stale_org_created_after_a_transfer_does_not_resurrect_the_old_owner(consumer):
    org_id, old_owner, new_owner = _transfer_setup(consumer)
    _deliver(consumer, "org.membership.changed", {
        "organization_id": org_id, "account_id": old_owner, "role": "admin", "version": 2,
    })
    _deliver(consumer, "org.membership.changed", {
        "organization_id": org_id, "account_id": new_owner, "role": "owner", "version": 2,
    })

    _deliver(consumer, "org.created", {"organization_id": org_id, "owner_account_id": old_owner, "version": 1})

    assert _active_owners(org_id) == {new_owner}
    assert _owner_column(org_id) == new_owner


@pytest.mark.django_db
def test_transfer_evicts_both_cached_roles(consumer, monkeypatch):
    org_id, old_owner, new_owner = _transfer_setup(consumer)
    evicted = []
    consumer_module = __import__(type(consumer).__module__, fromlist=["OrgPermissions"])
    monkeypatch.setattr(consumer_module.OrgPermissions, "evict", lambda o, a: evicted.append((o, a)))

    _deliver(consumer, "org.membership.changed", {
        "organization_id": org_id, "account_id": old_owner, "role": "admin", "version": 2,
    })
    _deliver(consumer, "org.membership.changed", {
        "organization_id": org_id, "account_id": new_owner, "role": "owner", "version": 2,
    })

    assert evicted == [(org_id, old_owner), (org_id, new_owner)]
