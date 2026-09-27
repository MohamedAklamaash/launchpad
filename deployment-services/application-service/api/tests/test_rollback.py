"""Append-only deploy history + rollback.

`Application.envs` is plaintext, so the Deployment snapshot stores the env's SHAPE (key
names + a keyed hash of the values), never the values themselves. A rollback always
re-reads the application's current env values — rolling back code must not roll back a
rotated credential — and skips CodeBuild entirely by pinning to an image already in ECR.
"""
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from aws.ecr import ECRClient
from botocore.exceptions import ClientError
from shared.enums.user_role import UserRole

from api.mock.mock_session import _MOCK_RESOLVED_SHA, MockSession
from api.models.deployment import Deployment
from api.services.application_deployment_service import ApplicationDeploymentService
from api.services.application_service import DeploymentInProgressError
from api.services.deployment_snapshot import snapshot_env
from api.services.rollback_service import RollbackService

ACCOUNT_ID = "123456789012"
SECRET_VALUE = "sk-live-REDACTME-" + uuid.uuid4().hex


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def ecs_app(schema_db):
    from api.models.application import Application
    from api.models.environment import Environment
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    user = User.objects.create(id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@x.com", user_name="t")
    infra = Infrastructure.objects.create(
        user=user, name=f"infra-{uuid.uuid4()}", cloud_provider="aws",
        max_cpu=1024, max_memory=2048, is_mock=True, code=ACCOUNT_ID,
        is_cloud_authenticated=True,
    )
    env = Environment.objects.create(
        infrastructure=infra, status="ACTIVE", vpc_id="vpc-1",
        cluster_arn=f"arn:aws:ecs:us-east-1:{ACCOUNT_ID}:cluster/c",
        alb_arn=f"arn:aws:elasticloadbalancing:us-east-1:{ACCOUNT_ID}:loadbalancer/app/a/1",
        alb_dns="alb.example.com", alb_security_group_id="sg-alb",
        ecr_repository_url=f"{ACCOUNT_ID}.dkr.ecr.us-east-1.amazonaws.com/launchpad-abc",
        ecs_task_execution_role_arn=f"arn:aws:iam::{ACCOUNT_ID}:role/exec",
    )
    app = Application.objects.create(
        user=user, infrastructure=infra, name="my-app",
        project_remote_url="https://github.com/o/r", project_branch="main",
        project_commit_hash="a" * 40, envs={"NODE_ENV": "production", "API_KEY": SECRET_VALUE},
        port=8080, alloted_cpu=256, alloted_memory=512, attached_database_ids=[],
        status="ACTIVE",
        service_arn=f"arn:aws:ecs:us-east-1:{ACCOUNT_ID}:service/c/my-app-service",
        target_group_arn=f"arn:aws:elasticloadbalancing:us-east-1:{ACCOUNT_ID}:targetgroup/my-app-tg/abc",
        task_definition_arn=f"arn:aws:ecs:us-east-1:{ACCOUNT_ID}:task-definition/my-app-task:1",
        deployment_url="http://alb.example.com/my-app",
    )
    return app, env


@pytest.fixture
def old_deployment(ecs_app):
    """An earlier, smaller deploy with a different env shape — the rollback target."""
    app, _env = ecs_app
    keys, digest = snapshot_env(app.id, {"NODE_ENV": "staging"})
    return Deployment.objects.create(
        application=app, image_tag="my-app-" + "b" * 40, commit_sha="b" * 40,
        tag_source=Deployment.TAG_SOURCE_RESOLVED_SHA, compute_type="ecs_fargate",
        env_keys=keys, env_values_hash=digest, attached_database_ids=[],
        cpu=128, memory=256, port=3000,
        status=Deployment.STATUS_SUCCEEDED, triggered_by=Deployment.TRIGGERED_BY_DEPLOY,
    )


@pytest.fixture
def mock_service(monkeypatch):
    service = ApplicationDeploymentService()
    session = MockSession(region="us-east-1", account_id=ACCOUNT_ID)
    monkeypatch.setattr(service, "_create_aws_session", lambda _infra: session)
    return service


def _req(user_id):
    return SimpleNamespace(user=SimpleNamespace(id=user_id))


# ── snapshot the shape, not the values ──────────────────────────────────────────

_DUMMY_APP_ID = uuid.uuid4()
_OTHER_APP_ID = uuid.uuid4()


def test_snapshot_env_returns_keys_and_a_keyed_hash_not_values():
    keys, digest = snapshot_env(_DUMMY_APP_ID, {"B": "1", "A": SECRET_VALUE})
    assert keys == ["A", "B"]  # sorted, so key order never leaks insertion order
    assert SECRET_VALUE not in digest
    assert len(digest) == 64  # hex sha256


def test_snapshot_hash_is_stable_regardless_of_dict_order():
    _, d1 = snapshot_env(_DUMMY_APP_ID, {"A": "1", "B": "2"})
    _, d2 = snapshot_env(_DUMMY_APP_ID, {"B": "2", "A": "1"})
    assert d1 == d2


def test_snapshot_hash_changes_when_a_value_changes():
    _, d1 = snapshot_env(_DUMMY_APP_ID, {"A": "1"})
    _, d2 = snapshot_env(_DUMMY_APP_ID, {"A": "2"})
    assert d1 != d2


def test_snapshot_hash_is_domain_separated_per_app():
    """Two different apps with byte-identical envs must never compare equal — otherwise the
    'values changed' indicator on one app's rollback preview would leak information about
    an unrelated app's env values matching or not."""
    _, d1 = snapshot_env(_DUMMY_APP_ID, {"A": "1"})
    _, d2 = snapshot_env(_OTHER_APP_ID, {"A": "1"})
    assert d1 != d2


@pytest.mark.django_db
def test_deployment_row_never_stores_the_secret_value(ecs_app, mock_service):
    app, _env = ecs_app
    mock_service._record_deployment(
        app, image_tag="my-app-" + "c" * 40, resolved_sha="c" * 40,
        status="SUCCEEDED", triggered_by="DEPLOY",
    )
    row = Deployment.objects.get(application=app)
    assert SECRET_VALUE not in str(list(row.__dict__.values()))
    assert set(row.env_keys) == {"NODE_ENV", "API_KEY"}
    assert row.env_values_hash != ""


# ── ECR is tag-MUTABLE: pin by content digest when one is known ─────────────

@pytest.mark.django_db
def test_record_deployment_resolves_and_stores_the_image_digest(ecs_app, mock_service):
    import hashlib

    app, env = ecs_app
    tag = "my-app-" + "c" * 40
    mock_service._record_deployment(
        app, image_tag=tag, resolved_sha="c" * 40, status="SUCCEEDED", triggered_by="DEPLOY",
        session=mock_service._create_aws_session(app.infrastructure), environment=env,
    )
    row = Deployment.objects.get(application=app)
    assert row.image_digest == f"sha256:{hashlib.sha256(tag.encode()).hexdigest()}"


@pytest.fixture
def old_deployment_with_digest(ecs_app):
    """A rollback target recorded with a digest — the tag-mutation-proof pinning path."""
    app, _env = ecs_app
    keys, digest = snapshot_env(app.id, {"NODE_ENV": "staging"})
    return Deployment.objects.create(
        application=app, image_tag="my-app-" + "f" * 40, commit_sha="f" * 40,
        image_digest="sha256:" + "1" * 64,
        tag_source=Deployment.TAG_SOURCE_RESOLVED_SHA, compute_type="ecs_fargate",
        env_keys=keys, env_values_hash=digest, attached_database_ids=[],
        cpu=64, memory=128, port=4000,
        status=Deployment.STATUS_SUCCEEDED, triggered_by=Deployment.TRIGGERED_BY_DEPLOY,
    )


@pytest.mark.django_db
def test_rollback_pins_by_digest_when_the_target_row_has_one(ecs_app, old_deployment_with_digest, mock_service):
    app, _env = ecs_app

    with patch("aws.ecs.ECSClient.create_task_definition", side_effect=Exception("stop-here")) as create_td, \
            pytest.raises(Exception, match="stop-here"):
        mock_service.rollback_application(app, old_deployment_with_digest)

    image = create_td.call_args.kwargs["image"]
    assert image.endswith(f"@{old_deployment_with_digest.image_digest}")


@pytest.mark.django_db
def test_rollback_falls_back_to_tag_when_no_digest_is_recorded(ecs_app, old_deployment, mock_service):
    """Rows written before digest tracking existed have no image_digest and must still be
    addressable — pinned by tag, same as before this feature."""
    app, _env = ecs_app
    assert old_deployment.image_digest is None

    with patch("aws.ecs.ECSClient.create_task_definition", side_effect=Exception("stop-here")) as create_td, \
            pytest.raises(Exception, match="stop-here"):
        mock_service.rollback_application(app, old_deployment)

    image = create_td.call_args.kwargs["image"]
    assert image.endswith(f":{old_deployment.image_tag}")
    assert "@" not in image


@pytest.mark.django_db
def test_expired_digest_is_rejected_the_same_way_as_an_expired_tag(ecs_app, old_deployment_with_digest, mock_service):
    app, _env = ecs_app

    with patch.object(ECRClient, "image_exists", return_value=False) as image_exists, \
            pytest.raises(ValueError, match="no longer available"):
        mock_service.rollback_application(app, old_deployment_with_digest)

    image_exists.assert_called_once_with(
        "launchpad-abc", old_deployment_with_digest.image_tag, digest=old_deployment_with_digest.image_digest,
    )


@pytest.mark.django_db
def test_secret_value_absent_from_preview_and_list_responses(ecs_app, old_deployment, mock_service):
    app, _env = ecs_app
    mock_service._record_deployment(
        app, image_tag="my-app-" + "c" * 40, resolved_sha="c" * 40,
        status="SUCCEEDED", triggered_by="DEPLOY",
    )
    service = RollbackService()
    deployments = list(service.list_deployments(app.user_id, str(app.id)))
    preview = service.get_rollback_preview(app.user_id, str(app.id), str(old_deployment.id))

    assert SECRET_VALUE not in str([d.__dict__ for d in deployments])
    assert SECRET_VALUE not in str(preview)
    assert preview["values_changed"] is True
    assert preview["added_keys"] == ["API_KEY"]  # current has it, the old snapshot did not


# ── EKS tag pinning ──────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_eks_deploy_pins_to_the_resolved_sha_not_the_requested_commit(monkeypatch):
    """Before this fix, EKS pinned to the *requested* commit (chosen pre-build) while ECS
    pinned to what the build actually resolved. Both must agree so Deployment.commit_sha
    means the same thing on either compute type."""
    from types import SimpleNamespace as NS

    from api.k8s import deployer as deployer_mod
    from api.mock import mock_k8s
    from api.models.application import Application
    from api.models.environment import Environment
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    monkeypatch.setattr(deployer_mod, "app_config", NS(mode="dev"))
    monkeypatch.setattr("time.sleep", lambda *_a, **_k: None)
    mock_k8s.reset()

    user = User.objects.create(id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@x.com", user_name="t")
    infra = Infrastructure.objects.create(
        user=user, name="infra-eks", cloud_provider="aws", compute_type="eks",
        max_cpu=4.0, max_memory=8.0, is_mock=True, code=ACCOUNT_ID,
        is_cloud_authenticated=True, metadata={"aws_region": "us-west-2"},
    )
    Environment.objects.create(
        infrastructure=infra, status="ACTIVE", vpc_id="vpc-1",
        cluster_arn=f"arn:aws:eks:us-west-2:{ACCOUNT_ID}:cluster/c",
        alb_dns="alb.example.com",
        ecr_repository_url=f"{ACCOUNT_ID}.dkr.ecr.us-west-2.amazonaws.com/repo",
    )
    app = Application.objects.create(
        user=user, infrastructure=infra, name="myapp", project_remote_url="https://github.com/o/r",
        project_branch="main", project_commit_hash="abcdef0123456789", port=8080,
        alloted_cpu=0.5, alloted_memory=1.0, envs={},
    )

    session = MockSession(region="us-west-2", account_id=ACCOUNT_ID, infra_id=str(infra.id))
    service = ApplicationDeploymentService()
    monkeypatch.setattr(service, "_create_aws_session", lambda _infra: session)

    service.deploy_application(app)

    row = Deployment.objects.get(application=app)
    assert row.commit_sha == _MOCK_RESOLVED_SHA
    assert row.tag_source == Deployment.TAG_SOURCE_RESOLVED_SHA
    assert row.image_tag == f"myapp-{_MOCK_RESOLVED_SHA}"
    # The requested (pre-build) commit must NOT be what got pinned.
    assert "abcdef012345" not in row.image_tag


# ── rollback: restores the old image, re-reads current env, no build call ──────

@pytest.mark.django_db
def test_rollback_pins_old_image_restores_resources_and_never_touches_codebuild(
    ecs_app, old_deployment, mock_service,
):
    app, env = ecs_app

    with patch("api.services.application_deployment_service.CodeBuildClient") as cb_cls:
        url = mock_service.rollback_application(app, old_deployment)

    cb_cls.assert_not_called()
    app.refresh_from_db()
    assert app.status == "ACTIVE"
    assert app.alloted_cpu == old_deployment.cpu == 128
    assert app.alloted_memory == old_deployment.memory == 256
    assert app.port == old_deployment.port == 3000
    assert url == f"http://{env.alb_dns}/my-app"

    new_row = Deployment.objects.exclude(id=old_deployment.id).get(application=app)
    assert new_row.triggered_by == Deployment.TRIGGERED_BY_ROLLBACK
    assert new_row.status == Deployment.STATUS_SUCCEEDED
    assert new_row.image_tag == old_deployment.image_tag
    assert new_row.rolled_back_from_id == old_deployment.id
    # The row recorded by *this* rollback reflects envs re-read at rollback time
    # (current app.envs), not the target's stale snapshot.
    current_keys, current_hash = snapshot_env(app.id, app.envs)
    assert set(new_row.env_keys) == set(current_keys)
    assert new_row.env_values_hash == current_hash


@pytest.mark.django_db
def test_rollback_reads_current_env_values_at_rollback_time(ecs_app, old_deployment, mock_service):
    """Rolling back code must not roll back a rotated credential: the deployed task
    definition uses whatever is in Application.envs right now, not the target's snapshot."""
    app, _env = ecs_app
    rotated = "sk-live-ROTATED-" + uuid.uuid4().hex
    app.envs = {"NODE_ENV": "production", "API_KEY": rotated}
    app.save(update_fields=["envs"])

    with patch("aws.ecs.ECSClient.create_task_definition", side_effect=Exception("stop-here")) as create_td, \
            pytest.raises(Exception, match="stop-here"):
        mock_service.rollback_application(app, old_deployment)

    sent_envs = create_td.call_args.kwargs["envs"]
    assert sent_envs["API_KEY"] == rotated
    assert sent_envs["PORT"] == str(old_deployment.port)  # PORT reflects the restored config
    assert create_td.call_args.kwargs["image"].endswith(f":{old_deployment.image_tag}")


# ── all-or-nothing on config ─────────────────────────────────────────────────

@pytest.mark.django_db
def test_rollback_is_all_or_nothing_on_config_failure(ecs_app, old_deployment, mock_service):
    app, _env = ecs_app
    original_cpu, original_memory, original_port = app.alloted_cpu, app.alloted_memory, app.port
    original_task_def = app.task_definition_arn

    with patch("aws.ecs.ECSClient.create_task_definition", side_effect=RuntimeError("boom")), \
            pytest.raises(RuntimeError):
        mock_service.rollback_application(app, old_deployment)

    app.refresh_from_db()
    assert app.alloted_cpu == original_cpu
    assert app.alloted_memory == original_memory
    assert app.port == original_port
    assert app.task_definition_arn == original_task_def
    assert app.status == "FAILED"  # the outer failure handler still records the attempt
    assert Deployment.objects.filter(application=app, status=Deployment.STATUS_FAILED).exists()


# ── expired ECR tag ──────────────────────────────────────────────────────────

class _FakeEcrClient:
    def __init__(self, found: bool):
        self._found = found

    def describe_images(self, **kwargs):
        if self._found:
            return {"imageDetails": [{}]}
        raise ClientError(
            {"Error": {"Code": "ImageNotFoundException", "Message": "not found"}}, "DescribeImages",
        )


class _FakeSession:
    def __init__(self, client):
        self._client = client

    def client(self, name, **kwargs):
        return self._client


def test_image_exists_true_when_ecr_finds_the_tag():
    assert ECRClient(_FakeSession(_FakeEcrClient(True))).image_exists("repo", "tag") is True


def test_image_exists_false_on_image_not_found():
    assert ECRClient(_FakeSession(_FakeEcrClient(False))).image_exists("repo", "tag") is False


def test_image_exists_reraises_other_client_errors():
    boom = ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, "DescribeImages")

    class _Fake:
        def describe_images(self, **kwargs):
            raise boom

    with pytest.raises(ClientError):
        ECRClient(_FakeSession(_Fake())).image_exists("repo", "tag")


def test_image_exists_checks_by_digest_when_one_is_given():
    class _Fake:
        def describe_images(self, **kwargs):
            assert kwargs["imageIds"] == [{"imageDigest": "sha256:abc"}]
            return {"imageDetails": [{}]}

    assert ECRClient(_FakeSession(_Fake())).image_exists("repo", "tag", digest="sha256:abc") is True


def test_get_image_digest_returns_none_on_failure_rather_than_raising():
    class _Fake:
        def describe_images(self, **kwargs):
            raise ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, "DescribeImages")

    assert ECRClient(_FakeSession(_Fake())).get_image_digest("repo", "tag") is None


def test_get_image_digest_returns_the_first_matching_digest():
    class _Fake:
        def describe_images(self, **kwargs):
            return {"imageDetails": [{"imageDigest": "sha256:xyz"}]}

    assert ECRClient(_FakeSession(_Fake())).get_image_digest("repo", "tag") == "sha256:xyz"


def test_get_image_ref_prefers_digest_over_tag():
    ecr = ECRClient(_FakeSession(_FakeEcrClient(True)))
    assert ecr.get_image_ref("repo-url", "mytag", "sha256:abc") == "repo-url@sha256:abc"
    assert ecr.get_image_ref("repo-url", "mytag", None) == "repo-url:mytag"


@pytest.mark.django_db
def test_rollback_on_an_expired_tag_fails_with_a_usable_message_before_touching_ecs(
    ecs_app, old_deployment, mock_service,
):
    app, _env = ecs_app

    with patch.object(ECRClient, "image_exists", return_value=False), \
            patch("api.services.application_deployment_service.ECSClient") as ecs_cls, \
            patch("api.services.application_deployment_service.CodeBuildClient") as cb_cls, \
            pytest.raises(ValueError, match="no longer available"):
        mock_service.rollback_application(app, old_deployment)

    ecs_cls.return_value.create_task_definition.assert_not_called()
    cb_cls.assert_not_called()
    app.refresh_from_db()
    assert app.status == "FAILED"
    assert "no longer available" in app.error_message


# ── 409 while a deploy is in flight ──────────────────────────────────────────

@pytest.mark.django_db
def test_trigger_rollback_rejects_409_while_locked(ecs_app, old_deployment, monkeypatch):
    app, _env = ecs_app
    monkeypatch.setattr(
        "api.services.rollback_service.DeploymentLock.is_locked", lambda self, _id: True,
    )
    monkeypatch.setattr(
        "api.services.rollback_service.DeploymentQueue.enqueue_rollback", MagicMock(),
    )

    with pytest.raises(DeploymentInProgressError):
        RollbackService().trigger_rollback(app.user_id, str(app.id), str(old_deployment.id))


@pytest.mark.django_db
def test_rollback_view_returns_409_when_locked(ecs_app, old_deployment, monkeypatch):
    from api.views.application import ApplicationRollbackView

    app, _env = ecs_app
    monkeypatch.setattr(
        "api.services.rollback_service.DeploymentLock.is_locked", lambda self, _id: True,
    )

    response = ApplicationRollbackView().post(_req(app.user_id), pk=str(app.id), deployment_id=str(old_deployment.id))
    assert response.status_code == 409


@pytest.mark.django_db
def test_trigger_rollback_enqueues_and_pauses_auto_deploy(ecs_app, old_deployment, monkeypatch):
    app, _env = ecs_app
    enqueue = MagicMock()
    monkeypatch.setattr("api.services.rollback_service.DeploymentQueue.enqueue_rollback", enqueue)
    monkeypatch.setattr("api.services.rollback_service.DeploymentLock.is_locked", lambda self, _id: False)

    RollbackService().trigger_rollback(app.user_id, str(app.id), str(old_deployment.id))

    app.refresh_from_db()
    assert app.auto_deploy_paused is True
    enqueue.assert_called_once_with(str(app.id), str(app.infrastructure_id), str(old_deployment.id))


@pytest.mark.django_db
def test_trigger_rollback_does_not_pause_when_enqueue_fails(ecs_app, old_deployment, monkeypatch):
    app, _env = ecs_app
    monkeypatch.setattr("api.services.rollback_service.DeploymentLock.is_locked", lambda self, _id: False)
    monkeypatch.setattr(
        "api.services.rollback_service.DeploymentQueue.enqueue_rollback",
        MagicMock(side_effect=RuntimeError("redis unavailable")),
    )

    with pytest.raises(RuntimeError):
        RollbackService().trigger_rollback(app.user_id, str(app.id), str(old_deployment.id))

    app.refresh_from_db()
    assert app.auto_deploy_paused is False


# ── cross-tenant authz: 404 stranger / 403 invited ──────────────────────────

@pytest.mark.django_db
def test_stranger_gets_404_not_403(ecs_app, old_deployment):
    from api.models.user import User
    from api.views.application import ApplicationDeploymentsView

    app, _env = ecs_app
    stranger = User.objects.create(id=uuid.uuid4(), email=f"s-{uuid.uuid4()}@x.com", user_name="s")

    response = ApplicationDeploymentsView().get(_req(stranger.id), pk=str(app.id))
    assert response.status_code == 404


@pytest.mark.django_db
def test_invited_admin_gets_403_not_404(ecs_app, old_deployment):
    from api.models.infrastructure_user_role import InfrastructureUserRole
    from api.models.user import User
    from api.views.application import (
        ApplicationDeploymentsView,
        ApplicationRollbackView,
    )

    app, _env = ecs_app
    admin = User.objects.create(id=uuid.uuid4(), email=f"a-{uuid.uuid4()}@x.com", user_name="a")
    InfrastructureUserRole.objects.create(infrastructure=app.infrastructure, user=admin, role=UserRole.ADMIN)

    list_resp = ApplicationDeploymentsView().get(_req(admin.id), pk=str(app.id))
    assert list_resp.status_code == 403

    rollback_resp = ApplicationRollbackView().post(_req(admin.id), pk=str(app.id), deployment_id=str(old_deployment.id))
    assert rollback_resp.status_code == 403


@pytest.mark.django_db
def test_owner_can_list_and_preview(ecs_app, old_deployment):
    from api.views.application import (
        ApplicationDeploymentsView,
        ApplicationRollbackPreviewView,
    )

    app, _env = ecs_app
    list_resp = ApplicationDeploymentsView().get(_req(app.user_id), pk=str(app.id))
    assert list_resp.status_code == 200
    assert len(list_resp.data) == 1
    assert list_resp.data[0]["rollback_addressable"] is True
    assert SECRET_VALUE not in str(list_resp.data)

    preview_resp = ApplicationRollbackPreviewView().get(
        _req(app.user_id), pk=str(app.id), deployment_id=str(old_deployment.id),
    )
    assert preview_resp.status_code == 200
    assert preview_resp.data["deployment_id"] == str(old_deployment.id)
    assert SECRET_VALUE not in str(preview_resp.data)


@pytest.mark.django_db
def test_list_flags_a_latest_tagged_row_as_not_addressable(ecs_app):
    """A `latest`-tag row (a CodeBuild project predating the two-tag buildspec) shows up in
    history but the frontend must be able to tell it apart from a real rollback target."""
    from api.views.application import ApplicationDeploymentsView

    app, _env = ecs_app
    keys, digest = snapshot_env(app.id, {})
    Deployment.objects.create(
        application=app, image_tag="my-app-latest", commit_sha=None,
        tag_source=Deployment.TAG_SOURCE_LATEST, compute_type="ecs_fargate",
        env_keys=keys, env_values_hash=digest, attached_database_ids=[],
        cpu=256, memory=512, port=8080,
        status=Deployment.STATUS_SUCCEEDED, triggered_by=Deployment.TRIGGERED_BY_DEPLOY,
    )

    resp = ApplicationDeploymentsView().get(_req(app.user_id), pk=str(app.id))

    assert resp.status_code == 200
    row = next(d for d in resp.data if d["image_tag"] == "my-app-latest")
    assert row["rollback_addressable"] is False


@pytest.mark.django_db
def test_malformed_uuid_is_404_not_500(ecs_app):
    from api.views.application import ApplicationDeploymentsView

    app, _env = ecs_app
    response = ApplicationDeploymentsView().get(_req(app.user_id), pk="not-a-uuid")
    assert response.status_code == 404


# ── webhook: paused apps ignore pushes but still track the pointer ──────────

@pytest.mark.django_db
def test_webhook_ignores_push_while_paused_but_advances_commit(ecs_app, monkeypatch):
    import hashlib
    import hmac
    import json

    from rest_framework.test import APIRequestFactory

    from api.views.application import application_github_webhook

    app, _env = ecs_app
    app.github_webhook_secret = "whsec-" + uuid.uuid4().hex
    app.auto_deploy_paused = True
    app.save(update_fields=["github_webhook_secret", "auto_deploy_paused"])

    enqueue = MagicMock()
    monkeypatch.setattr("api.views.application.DeploymentQueue.enqueue_deployment", enqueue)

    sha = "d" * 40
    body = json.dumps({"ref": "refs/heads/main", "after": sha}).encode()
    sig = "sha256=" + hmac.new(app.github_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    request = APIRequestFactory().post(
        f"/api/v1/webhooks/github/{app.id}/", data=body, content_type="application/json",
        HTTP_X_GITHUB_EVENT="push", HTTP_X_HUB_SIGNATURE_256=sig,
    )

    response = application_github_webhook(request, app_id=str(app.id))

    assert response.status_code == 200
    assert response.data["reason"] == "auto-deploy paused"
    enqueue.assert_not_called()
    app.refresh_from_db()
    assert app.project_commit_hash == sha  # pointer still advances so resume deploys the latest push


@pytest.mark.django_db
def test_webhook_deploy_is_tagged_with_its_source(ecs_app, monkeypatch):
    """The worker re-checks the pause at dequeue time for webhook jobs only — it has to be
    able to tell a webhook-triggered job apart from a manual one."""
    import hashlib
    import hmac
    import json

    from rest_framework.test import APIRequestFactory

    from api.views.application import application_github_webhook

    app, _env = ecs_app
    app.github_webhook_secret = "whsec-" + uuid.uuid4().hex
    app.save(update_fields=["github_webhook_secret"])

    enqueue = MagicMock()
    monkeypatch.setattr("api.views.application.DeploymentQueue.enqueue_deployment", enqueue)

    body = json.dumps({"ref": "refs/heads/main", "after": "e" * 40}).encode()
    sig = "sha256=" + hmac.new(app.github_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    request = APIRequestFactory().post(
        f"/api/v1/webhooks/github/{app.id}/", data=body, content_type="application/json",
        HTTP_X_GITHUB_EVENT="push", HTTP_X_HUB_SIGNATURE_256=sig,
    )

    response = application_github_webhook(request, app_id=str(app.id))

    assert response.status_code == 202
    enqueue.assert_called_once_with(str(app.id), str(app.infrastructure_id), source="webhook")


# ── worker: closing the race between a queued webhook deploy and a rollback pin ─

@pytest.mark.django_db
def test_worker_skips_a_webhook_job_when_the_app_is_paused_at_dequeue_time(ecs_app, monkeypatch):
    """The webhook already checks auto_deploy_paused before enqueueing, but a job already
    sitting in the queue when a rollback pins the app must not still run once the worker
    picks it up — the worker re-reads the row and checks again."""
    from api.management.commands.run_worker import execute_deploy_job

    app, _env = ecs_app
    app.auto_deploy_paused = True
    app.save(update_fields=["auto_deploy_paused"])

    ack = MagicMock()
    deploy = MagicMock()
    monkeypatch.setattr("api.services.deployment_queue.DeploymentQueue.ack_job", ack)
    monkeypatch.setattr(
        "api.services.application_deployment_service.ApplicationDeploymentService.deploy_application", deploy,
    )

    execute_deploy_job(str(app.id), {"app_id": str(app.id), "action": "deploy", "source": "webhook"})

    deploy.assert_not_called()
    ack.assert_called_once()


@pytest.mark.django_db
def test_worker_still_deploys_a_manual_job_even_if_somehow_paused(ecs_app, monkeypatch):
    """The source tag is what gates the re-check — a manual deploy job (no `source`) must
    never be silently skipped, since the view that enqueues it already cleared the pause."""
    from api.management.commands.run_worker import execute_deploy_job

    app, _env = ecs_app
    app.auto_deploy_paused = True
    app.save(update_fields=["auto_deploy_paused"])

    ack = MagicMock()
    deploy = MagicMock(return_value="http://example.com")
    monkeypatch.setattr("api.services.deployment_queue.DeploymentQueue.ack_job", ack)
    monkeypatch.setattr(
        "api.services.application_deployment_service.ApplicationDeploymentService.deploy_application", deploy,
    )

    execute_deploy_job(str(app.id), {"app_id": str(app.id), "action": "deploy"})

    deploy.assert_called_once()
    ack.assert_called_once()


@pytest.mark.django_db
def test_manual_deploy_clears_the_pause(ecs_app, monkeypatch):
    from api.views.application import ApplicationDeployView

    app, _env = ecs_app
    app.auto_deploy_paused = True
    app.save(update_fields=["auto_deploy_paused"])
    monkeypatch.setattr("api.views.application.DeploymentQueue.enqueue_deployment", MagicMock())
    monkeypatch.setattr(
        "api.services.infrastructure_permissions.InfrastructurePermissions.can_update_application",
        staticmethod(lambda *_a: True),
    )

    response = ApplicationDeployView().post(_req(app.user_id), pk=str(app.id))

    assert response.status_code == 202
    app.refresh_from_db()
    assert app.auto_deploy_paused is False


@pytest.mark.django_db
def test_resume_auto_deploy_clears_the_pause_without_deploying(ecs_app):
    app, _env = ecs_app
    app.auto_deploy_paused = True
    app.save(update_fields=["auto_deploy_paused"])

    RollbackService().resume_auto_deploy(app.user_id, str(app.id))

    app.refresh_from_db()
    assert app.auto_deploy_paused is False


# ── EKS rollback: patch image + env, restore resources ──────────────────────

@pytest.fixture
def eks_app(schema_db):
    from api.models.application import Application
    from api.models.environment import Environment
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    user = User.objects.create(id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@x.com", user_name="t")
    infra = Infrastructure.objects.create(
        user=user, name="infra-eks-rb", cloud_provider="aws", compute_type="eks",
        max_cpu=4.0, max_memory=8.0, is_mock=True, code=ACCOUNT_ID,
        is_cloud_authenticated=True, metadata={"aws_region": "us-west-2"},
    )
    env = Environment.objects.create(
        infrastructure=infra, status="ACTIVE", vpc_id="vpc-1",
        cluster_arn=f"arn:aws:eks:us-west-2:{ACCOUNT_ID}:cluster/c",
        alb_dns="alb.example.com",
        ecr_repository_url=f"{ACCOUNT_ID}.dkr.ecr.us-west-2.amazonaws.com/repo",
    )
    app = Application.objects.create(
        user=user, infrastructure=infra, name="myapp", project_remote_url="https://github.com/o/r",
        project_branch="main", project_commit_hash="a" * 40, port=8080,
        alloted_cpu=0.5, alloted_memory=1.0, envs={"FOO": "bar"},
    )
    return app, env


@pytest.fixture
def eks_old_deployment(eks_app):
    app, _env = eks_app
    keys, digest = snapshot_env(app.id, {"FOO": "old-value"})
    return Deployment.objects.create(
        application=app, image_tag="myapp-" + "e" * 40, commit_sha="e" * 40,
        tag_source=Deployment.TAG_SOURCE_RESOLVED_SHA, compute_type="eks",
        env_keys=keys, env_values_hash=digest, attached_database_ids=[],
        cpu=0.25, memory=0.5, port=3000,
        status=Deployment.STATUS_SUCCEEDED, triggered_by=Deployment.TRIGGERED_BY_DEPLOY,
    )


@pytest.fixture
def eks_dev_mode(monkeypatch):
    from types import SimpleNamespace as NS

    from api.k8s import deployer as deployer_mod
    from api.mock import mock_k8s

    monkeypatch.setattr(deployer_mod, "app_config", NS(mode="dev"))
    monkeypatch.setattr("time.sleep", lambda *_a, **_k: None)
    mock_k8s.reset()
    yield
    mock_k8s.reset()


@pytest.mark.django_db
def test_eks_rollback_patches_image_and_env_and_restores_resources(
    eks_app, eks_old_deployment, eks_dev_mode, monkeypatch,
):
    from api.mock import mock_k8s

    app, env = eks_app
    session = MockSession(region="us-west-2", account_id=ACCOUNT_ID, infra_id=str(app.infrastructure_id))
    service = ApplicationDeploymentService()
    monkeypatch.setattr(service, "_create_aws_session", lambda _infra: session)

    with patch("api.services.application_deployment_service.CodeBuildClient") as cb_cls:
        url = service.rollback_application(app, eks_old_deployment)

    cb_cls.assert_not_called()
    app.refresh_from_db()
    assert app.status == "ACTIVE"
    assert app.alloted_cpu == 0.25
    assert app.alloted_memory == 0.5
    assert app.port == 3000
    assert url == f"http://{env.alb_dns}/myapp"

    deployment = mock_k8s.get_mock_apis(str(app.infrastructure_id)).state.objects[("deployment", "app-myapp", "myapp")]
    image = deployment.spec.template.spec.containers[0].image
    assert image.endswith(f":{eks_old_deployment.image_tag}")
    env_dict = {e.name: e.value for e in deployment.spec.template.spec.containers[0].env}
    assert env_dict["FOO"] == "bar"  # current env value, never the target snapshot's
    assert env_dict["PORT"] == "3000"  # restored port

    new_row = Deployment.objects.exclude(id=eks_old_deployment.id).get(application=app)
    assert new_row.triggered_by == Deployment.TRIGGERED_BY_ROLLBACK
    assert new_row.status == Deployment.STATUS_SUCCEEDED
