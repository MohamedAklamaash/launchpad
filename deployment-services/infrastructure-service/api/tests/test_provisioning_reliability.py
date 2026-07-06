import uuid
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone


@pytest.fixture
def make_infra(db):
    from api.models.user import User
    from api.models.infrastructure import Infrastructure

    def _make(*, is_cloud_authenticated=False):
        user = User.objects.create(
            id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@e.io", user_name="u", role="super_admin"
        )
        return Infrastructure.objects.create(
            user=user, name=f"i-{uuid.uuid4()}", cloud_provider="aws",
            max_cpu=1024, max_memory=512, code="123456789012",
            is_cloud_authenticated=is_cloud_authenticated,
        )

    return _make


@pytest.fixture
def make_env(make_infra):
    from api.models.environment import Environment

    def _make(*, status, locked_at="unset", is_cloud_authenticated=False):
        infra = make_infra(is_cloud_authenticated=is_cloud_authenticated)
        env = Environment.objects.create(infrastructure=infra, status=status)
        if locked_at != "unset":
            env.locked_at = locked_at
            env.locked_by = "old-worker"
            env.save(update_fields=["locked_at", "locked_by"])
        return env

    return _make


# ---- #1 stuck-provision reaper ----

def _reap(threshold, reap_count=1, has_lock=False):
    from api.management.commands.run_worker import reap_stuck_environments

    with patch("api.services.infra_queue.InfraQueue") as Q:
        Q.bump_reap_count.return_value = reap_count
        Q.has_lock.return_value = has_lock
        count = reap_stuck_environments(threshold)
    return count, Q


@pytest.mark.django_db
def test_reaper_reenqueues_stale_provisioning(make_env):
    env = make_env(status="PROVISIONING", locked_at=timezone.now() - timedelta(hours=2))
    count, Q = _reap(3600)
    assert count == 1
    Q.enqueue_provision.assert_called_once_with(str(env.infrastructure_id))


@pytest.mark.django_db
def test_reaper_reenqueues_null_lock_when_dedup_key_gone(make_env):
    env = make_env(status="PROVISIONING", locked_at=None)
    count, Q = _reap(3600, has_lock=False)
    assert count == 1
    Q.enqueue_provision.assert_called_once_with(str(env.infrastructure_id))


@pytest.mark.django_db
def test_reaper_ignores_null_lock_while_dedup_key_present(make_env):
    # Models delete_infrastructure: env set DESTROYING with no DB lock but its dedup key still set.
    # updated_at is NOT aged here (save(update_fields=['status']) never bumps it) — the reaper must
    # rely on the Redis key, not the timestamp, or it would duplicate this queued destroy.
    env = make_env(status="ACTIVE")
    env.status = "DESTROYING"
    env.save(update_fields=["status"])
    count, Q = _reap(3600, has_lock=True)
    assert count == 0
    Q.enqueue_destroy.assert_not_called()
    Q.bump_reap_count.assert_not_called()


@pytest.mark.django_db
def test_reaper_leaves_freshly_locked_provisioning_alone(make_env):
    make_env(status="PROVISIONING", locked_at=timezone.now())
    count, Q = _reap(3600)
    assert count == 0
    Q.enqueue_provision.assert_not_called()


@pytest.mark.django_db
def test_reaper_never_touches_pending(make_env):
    make_env(status="PENDING", locked_at=None)
    count, Q = _reap(3600)
    assert count == 0


@pytest.mark.django_db
def test_reaper_reenqueues_stale_destroying_via_destroy_queue(make_env):
    env = make_env(status="DESTROYING", locked_at=timezone.now() - timedelta(hours=2))
    count, Q = _reap(3600)
    assert count == 1
    Q.enqueue_destroy.assert_called_once_with(str(env.infrastructure_id))
    Q.enqueue_provision.assert_not_called()


@pytest.mark.django_db
def test_reaper_parks_poison_job_in_error_after_max_attempts(make_env):
    env = make_env(status="PROVISIONING", locked_at=timezone.now() - timedelta(hours=2))
    with patch("api.services.notification.NotificationService"):
        count, Q = _reap(3600, reap_count=99)
    assert count == 0
    Q.enqueue_provision.assert_not_called()
    env.refresh_from_db()
    assert env.status == "ERROR"


# ---- DB lock ownership (heartbeat + release) ----

@pytest.mark.django_db
def test_refresh_db_lock_only_when_owner(make_env):
    from api.services.infra_queue import InfraQueue

    env = make_env(status="PROVISIONING")
    infra_id = str(env.infrastructure_id)
    assert InfraQueue.acquire_db_lock(infra_id, "worker-a")
    assert InfraQueue.refresh_db_lock(infra_id, "worker-b") is False
    assert InfraQueue.refresh_db_lock(infra_id, "worker-a") is True
    env.refresh_from_db()
    assert env.locked_by == "worker-a"


@pytest.mark.django_db
def test_release_db_lock_is_owner_scoped(make_env):
    from api.services.infra_queue import InfraQueue

    env = make_env(status="PROVISIONING")
    infra_id = str(env.infrastructure_id)
    InfraQueue.acquire_db_lock(infra_id, "worker-a")

    InfraQueue.release_db_lock(infra_id, "worker-b")
    env.refresh_from_db()
    assert env.locked_by == "worker-a"

    InfraQueue.release_db_lock(infra_id, "worker-a")
    env.refresh_from_db()
    assert env.locked_by is None


# ---- #3 destroy-enqueue dedup ----

def test_enqueue_destroy_dedups_on_shared_lock():
    from api.services import infra_queue

    r = MagicMock()
    r.exists.side_effect = [False, True]
    with patch.object(infra_queue, "_redis", return_value=r):
        first = infra_queue.InfraQueue.enqueue_destroy("infra-x")
        second = infra_queue.InfraQueue.enqueue_destroy("infra-x")
    assert first is True and second is False
    assert r.rpush.call_count == 1
    r.setex.assert_called_once()


# ---- #4 self-heal infra.created republish ----

@pytest.mark.django_db
def test_republishes_infra_created_when_authenticated(make_infra):
    from api.management.commands.run_worker import ensure_infra_created_published

    infra = make_infra(is_cloud_authenticated=True)
    with patch("api.messaging.producer.producer.infra_producer") as producer:
        ensure_infra_created_published(infra)
    producer.publish_infra_created.assert_called_once()


@pytest.mark.django_db
def test_does_not_republish_when_not_authenticated(make_infra):
    from api.management.commands.run_worker import ensure_infra_created_published

    infra = make_infra(is_cloud_authenticated=False)
    with patch("api.messaging.producer.producer.infra_producer") as producer:
        ensure_infra_created_published(infra)
    producer.publish_infra_created.assert_not_called()


# ---- #2 onboarding-token reissue ----

@pytest.mark.django_db
def test_reissue_token_for_unonboarded_infra(make_infra):
    from api.services.infrastructure import InfrastructureService

    infra = make_infra(is_cloud_authenticated=False)
    result = InfrastructureService().reissue_onboarding_token(user_id=infra.user_id, infra_id=infra.id)
    assert result["onboarding_token"]
    infra.refresh_from_db()
    assert infra.onboarding_token_hash and infra.onboarding_token_used_at is None


@pytest.mark.django_db
def test_reissue_token_rejected_after_onboarding(make_infra):
    from api.services.infrastructure import InfrastructureService

    infra = make_infra(is_cloud_authenticated=True)
    with pytest.raises(ValueError):
        InfrastructureService().reissue_onboarding_token(user_id=infra.user_id, infra_id=infra.id)


@pytest.mark.django_db
def test_reissue_token_denied_for_non_owner(make_infra):
    from api.models.user import User
    from api.services.infrastructure import InfrastructureService

    infra = make_infra(is_cloud_authenticated=False)
    outsider = User.objects.create(
        id=uuid.uuid4(), email=f"x-{uuid.uuid4()}@e.io", user_name="x", role="super_admin"
    )
    infra.invited_users.add(outsider)
    with pytest.raises(PermissionError):
        InfrastructureService().reissue_onboarding_token(user_id=outsider.id, infra_id=infra.id)


# ---- #2 reissue endpoint (HTTP layer) ----

def _reissue(infra, user):
    from rest_framework.test import APIRequestFactory, force_authenticate
    from api.views.infrastructure import infrastructure_reissue_token

    request = APIRequestFactory().post(f"/api/v1/infrastructures/{infra.id}/reissue-token/")
    force_authenticate(request, user=user)
    return infrastructure_reissue_token(request, infra_id=str(infra.id))


@pytest.mark.django_db
def test_reissue_endpoint_returns_200_and_token_for_owner(make_infra):
    infra = make_infra(is_cloud_authenticated=False)
    response = _reissue(infra, infra.user)
    assert response.status_code == 200
    assert response.data["onboarding_token"]


@pytest.mark.django_db
def test_reissue_endpoint_returns_409_when_onboarded(make_infra):
    infra = make_infra(is_cloud_authenticated=True)
    response = _reissue(infra, infra.user)
    assert response.status_code == 409


@pytest.mark.django_db
def test_reissue_endpoint_returns_403_for_invited_member(make_infra):
    from api.models.user import User

    infra = make_infra(is_cloud_authenticated=False)
    member = User.objects.create(
        id=uuid.uuid4(), email=f"m-{uuid.uuid4()}@e.io", user_name="m", role="super_admin"
    )
    infra.invited_users.add(member)
    response = _reissue(infra, member)
    assert response.status_code == 403


@pytest.mark.django_db
def test_reissue_endpoint_returns_404_for_non_member(make_infra):
    from api.models.user import User

    infra = make_infra(is_cloud_authenticated=False)
    outsider = User.objects.create(
        id=uuid.uuid4(), email=f"o-{uuid.uuid4()}@e.io", user_name="o", role="super_admin"
    )
    response = _reissue(infra, outsider)
    assert response.status_code == 404
