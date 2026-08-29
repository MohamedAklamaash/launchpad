"""Locks the cross-tenant notification leak fix: infra event emails must go to the
infra's own owner, not to whichever super_admin the User table happened to yield first.

The two-owner shape is deliberate — a single-owner assertion could pass by accident,
since the pre-fix "first super_admin" lookup had no deterministic ordering.
"""
import json
import uuid
from unittest.mock import MagicMock, patch

import pytest
from api.services.notification import NotificationService

SENDERS = {
    "provision_success": (NotificationService.send_provision_success, ()),
    "provision_failure": (NotificationService.send_provision_failure, ("boom",)),
    "destroy_success": (NotificationService.send_destroy_success, ()),
    "destroy_failure": (NotificationService.send_destroy_failure, ("boom",)),
}


@pytest.fixture
def make_user(db):
    from api.models.user import User

    def _make(name):
        return User.objects.create(
            id=uuid.uuid4(), email=f"{name}-{uuid.uuid4()}@example.com",
            user_name=name, role="super_admin",
        )

    return _make


def _send(send, *args):
    pipe = MagicMock()
    r = MagicMock()
    r.pipeline.return_value = pipe
    with patch("api.services.notification._get_redis", return_value=r):
        send(*args)
    return pipe


def _job_payload(pipe):
    assert pipe.hset.call_count == 1
    return json.loads(pipe.hset.call_args.kwargs["mapping"]["data"])


@pytest.mark.django_db
@pytest.mark.parametrize("event", sorted(SENDERS))
def test_each_owner_is_notified_at_their_own_address(make_user, event):
    send, extra = SENDERS[event]
    owner_a = make_user("tenant-a")
    owner_b = make_user("tenant-b")

    payload_a = _job_payload(_send(send, str(owner_a.id), "infra-a", "a", *extra))
    payload_b = _job_payload(_send(send, str(owner_b.id), "infra-b", "b", *extra))

    assert payload_a["email"] == owner_a.email
    assert payload_a["user_name"] == "tenant-a"
    assert payload_a["user_id"] == str(owner_a.id)
    assert payload_b["email"] == owner_b.email
    assert payload_b["user_name"] == "tenant-b"
    assert payload_b["user_id"] == str(owner_b.id)


@pytest.mark.django_db
def test_failure_notification_carries_the_error(make_user):
    owner = make_user("owner")
    payload = _job_payload(
        _send(NotificationService.send_destroy_failure, str(owner.id), "infra-1", "prod", "boom")
    )
    assert payload["error"] == "boom"


@pytest.mark.django_db
def test_unknown_user_is_skipped_without_enqueue(make_user):
    make_user("bystander")

    pipe = _send(NotificationService.send_provision_success, str(uuid.uuid4()), "infra-1", "prod")
    pipe.hset.assert_not_called()


@pytest.mark.django_db
def test_malformed_user_id_is_skipped_without_raising():
    pipe = _send(NotificationService.send_provision_success, "not-a-uuid", "infra-1", "prod")
    pipe.hset.assert_not_called()
