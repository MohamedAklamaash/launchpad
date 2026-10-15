"""F7 ticket 5 — the platform builder client (aws/platform_builder.py).

Covers: RunTask's exact containerOverrides shape (no credential in overrides/env, only
S3 keys), unguessable build ids, S3 cleanup on success/failure/timeout/exception, a
timed-out build being force-stopped, exit-code-to-outcome mapping (including the init
container's dependsOn failure showing up as "kaniko never ran"), digest capture via ECR
rather than log parsing, the Docker Hub rate-limit special case, mock mode, and that no
secret is ever logged or placed in a RunTask override.
"""
import base64
import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import aws.github_source as github_source_mod
import aws.platform_builder as pb_mod
import aws.platform_builder_credentials as pbc_mod
import aws.session as aws_session_mod
import pytest
from aws.platform_builder import (
    BuildError,
    BuildResult,
    start_build,
    wait_for_build,
)

ACCOUNT_ID = "123456789012"
REGION = "us-west-2"
REPO_NAME = "launchpad-my-app-abcdef12"
SHA = "a" * 40

ENV_VARS = {
    "PLATFORM_BUILDER_LAUNCHER_ROLE_ARN": f"arn:aws:iam::{ACCOUNT_ID}:role/launchpad-platform-builder-launcher",
    "PLATFORM_BUILDER_CLUSTER": "launchpad-platform-builder",
    "PLATFORM_BUILDER_TASK_DEFINITION": "launchpad-platform-builder-kaniko",
    "PLATFORM_BUILDER_SUBNET_IDS": "subnet-aaa,subnet-bbb",
    "PLATFORM_BUILDER_SECURITY_GROUP_ID": "sg-ccc",
    "PLATFORM_BUILDER_LOG_GROUP": "/launchpad/platform-builder",
    "PLATFORM_BUILDER_SOURCE_BUCKET": "launchpad-platform-builder-sources",
    "PLATFORM_BUILDER_AWS_REGION": REGION,
}


@pytest.fixture(autouse=True)
def prod_mode(monkeypatch):
    prod = SimpleNamespace(
        mode="prod", aws_access_key_id="PLATFORM_AKID", aws_secret_access_key="PLATFORM_SECRET",
    )
    monkeypatch.setattr(aws_session_mod, "app_config", prod)
    monkeypatch.setattr(pbc_mod, "app_config", prod)
    monkeypatch.setattr(pb_mod, "app_config", prod)
    monkeypatch.setattr(github_source_mod, "app_config", prod)
    for key, value in ENV_VARS.items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def make_app(schema_db):
    from api.models.application import Application
    from api.models.environment import Environment
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    def _make(*, is_mock=False, dockerfile_path="Dockerfile", build_context=""):
        user = User.objects.create(id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="t")
        infra = Infrastructure.objects.create(
            id=uuid.uuid4(), user=user, name=f"infra-{uuid.uuid4()}", cloud_provider="aws",
            max_cpu=1024, max_memory=2048, is_mock=is_mock, code=ACCOUNT_ID,
            is_cloud_authenticated=True, metadata={"aws_region": REGION},
        )
        Environment.objects.create(
            id=uuid.uuid4(), infrastructure=infra,
            ecr_repository_url=f"{ACCOUNT_ID}.dkr.ecr.{REGION}.amazonaws.com/{REPO_NAME}",
        )
        app = Application.objects.create(
            name="my-app", user=user, infrastructure=infra,
            project_remote_url="https://github.com/acme/widgets", project_branch="main",
            project_commit_hash="", dockerfile_path=dockerfile_path, build_context=build_context,
        )
        return app, infra
    return _make


def _sts_response():
    return {
        "Credentials": {
            "AccessKeyId": "SCOPED_AK",
            "SecretAccessKey": "SCOPED_SK",
            "SessionToken": "SCOPED_TOKEN",
            "Expiration": datetime.now(timezone.utc) + timedelta(minutes=15),
        }
    }


def _ecr_token_response():
    password = f"fake-ecr-password-{uuid.uuid4().hex}"
    token = base64.b64encode(f"AWS:{password}".encode()).decode()
    return {
        "authorizationData": [
            {
                "authorizationToken": token,
                "expiresAt": datetime.now(timezone.utc) + timedelta(hours=12),
                "proxyEndpoint": f"https://{ACCOUNT_ID}.dkr.ecr.{REGION}.amazonaws.com",
            }
        ]
    }, password


def _mock_tarball_names():
    return ["acme-widgets-deadbee/README.md", "acme-widgets-deadbee/Dockerfile"]


class _FakeTarfile:
    """Stands in for `tarfile.open(...).getnames()` without writing a real tarball."""

    def __init__(self, names):
        self._names = names

    def getnames(self):
        return self._names

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def harness(monkeypatch):
    """Patches every AWS seam `aws/platform_builder.py` touches: `boto3.client` (sts/ecr
    only — s3/ecs/logs must come from the assumed launcher session, enforced below),
    `boto3.Session` (the launcher session), and the GitHub source fetch (never actually
    hits GitHub)."""
    sts_client = MagicMock()
    sts_client.assume_role.return_value = _sts_response()

    ecr_token, ecr_password = _ecr_token_response()
    ecr_client = MagicMock()
    ecr_client.get_authorization_token.return_value = ecr_token
    ecr_client.describe_images.return_value = {
        "imageDetails": [{"imageDigest": "sha256:" + "b" * 64}]
    }

    ecs_client = MagicMock()
    ecs_client.run_task.return_value = {
        "tasks": [{"taskArn": f"arn:aws:ecs:{REGION}:{ACCOUNT_ID}:task/launchpad-platform-builder/task-1"}],
        "failures": [],
    }
    s3_client = MagicMock()
    logs_client = MagicMock()

    def _boto3_client(service_name, **kwargs):
        if service_name == "sts":
            return sts_client
        if service_name == "ecr":
            return ecr_client
        raise AssertionError(
            f"s3/ecs/logs must come from the assumed launcher session, not boto3.client({service_name!r})"
        )

    clients = {"ecs": ecs_client, "s3": s3_client, "logs": logs_client, "ecr": ecr_client}

    class _FakeSession:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def client(self, service_name, **kwargs):
            return clients[service_name]

    monkeypatch.setattr(pb_mod.boto3, "client", _boto3_client)
    monkeypatch.setattr(pb_mod.boto3, "Session", _FakeSession)
    monkeypatch.setattr(pbc_mod.boto3, "client", _boto3_client)

    def _fake_fetch(application, sha, dest_path):
        with open(dest_path, "wb") as f:
            f.write(b"not a real tarball, only getnames() is stubbed")
        return str(dest_path)

    monkeypatch.setattr(pb_mod, "fetch_source_tarball", _fake_fetch)
    monkeypatch.setattr(pb_mod.tarfile, "open", lambda *a, **k: _FakeTarfile(_mock_tarball_names()))

    return SimpleNamespace(
        sts=sts_client, ecr=ecr_client, ecs=ecs_client, s3=s3_client, logs=logs_client,
        ecr_password=ecr_password,
    )


def _stopped_task(exit_code=0, init_exit_code=0, stopped_reason=""):
    return {
        "lastStatus": "STOPPED",
        "stoppedReason": stopped_reason,
        "containers": [
            {"name": "init", "exitCode": init_exit_code},
            {"name": "kaniko", "exitCode": exit_code},
        ],
    }


# ── start_build: RunTask shape ──────────────────────────────────────────────────────

def test_run_task_overrides_carry_only_s3_keys_never_a_credential(make_app, harness):
    app, infra = make_app()

    handle = start_build(app, infra, SHA)

    kwargs = harness.ecs.run_task.call_args.kwargs
    assert kwargs["cluster"] == "launchpad-platform-builder"
    assert kwargs["taskDefinition"] == "launchpad-platform-builder-kaniko"
    assert kwargs["launchType"] == "FARGATE"
    net = kwargs["networkConfiguration"]["awsvpcConfiguration"]
    assert net["subnets"] == ["subnet-aaa", "subnet-bbb"]
    assert net["securityGroups"] == ["sg-ccc"]
    assert net["assignPublicIp"] == "DISABLED"

    overrides = {o["name"]: o for o in kwargs["overrides"]["containerOverrides"]}
    init_cmd = overrides["init"]["command"]
    kaniko_cmd = overrides["kaniko"]["command"]

    assert init_cmd[:2] == ["s3", "cp"]
    assert init_cmd[2] == f"s3://launchpad-platform-builder-sources/{handle.docker_config_key}"
    assert init_cmd[3] == "/kaniko/.docker/config.json"

    assert "--context" in kaniko_cmd
    assert kaniko_cmd[kaniko_cmd.index("--context") + 1] == f"s3://launchpad-platform-builder-sources/{handle.source_key}"
    assert kaniko_cmd[kaniko_cmd.index("--destination") + 1] == f"{ACCOUNT_ID}.dkr.ecr.{REGION}.amazonaws.com/{REPO_NAME}:my-app-{SHA}"

    serialized = json.dumps(kwargs)
    assert harness.ecr_password not in serialized
    assert "SCOPED_SK" not in serialized
    assert "SCOPED_TOKEN" not in serialized


def test_docker_config_credential_written_to_s3_not_to_run_task(make_app, harness):
    app, infra = make_app()

    handle = start_build(app, infra, SHA)

    put_kwargs = harness.s3.put_object.call_args.kwargs
    assert put_kwargs["Bucket"] == "launchpad-platform-builder-sources"
    assert put_kwargs["Key"] == handle.docker_config_key
    assert put_kwargs["ServerSideEncryption"] == "AES256"
    body = json.loads(put_kwargs["Body"])
    registry = f"{ACCOUNT_ID}.dkr.ecr.{REGION}.amazonaws.com"
    decoded_auth = base64.b64decode(body["auths"][registry]["auth"]).decode()
    assert harness.ecr_password in decoded_auth  # the one place it's allowed to live
    run_task_kwargs = json.dumps(harness.ecs.run_task.call_args.kwargs)
    assert harness.ecr_password not in run_task_kwargs


def test_no_secret_ever_logged(make_app, harness, caplog):
    app, infra = make_app()

    with caplog.at_level("DEBUG"):
        start_build(app, infra, SHA)

    assert harness.ecr_password not in caplog.text
    assert "SCOPED_SK" not in caplog.text
    assert "SCOPED_TOKEN" not in caplog.text


def test_only_sts_and_ecr_use_raw_platform_keys(make_app, harness):
    """s3/ecs must come from the assumed launcher session — enforced by `harness`
    raising if boto3.client() is called for either directly."""
    app, infra = make_app()

    start_build(app, infra, SHA)  # would raise via harness's _boto3_client otherwise


# ── unguessable build ids ───────────────────────────────────────────────────────────

def test_build_ids_are_distinct_uuid4(make_app, harness):
    app, infra = make_app()

    handle1 = start_build(app, infra, SHA)
    handle2 = start_build(app, infra, SHA)

    assert uuid.UUID(handle1.build_id, version=4).hex == handle1.build_id
    assert uuid.UUID(handle2.build_id, version=4).hex == handle2.build_id
    assert handle1.build_id != handle2.build_id


def test_source_and_docker_config_keys_are_nested_under_the_build_id(make_app, harness):
    app, infra = make_app()

    handle = start_build(app, infra, SHA)

    assert handle.source_key == f"sources/{handle.build_id}/source.tar.gz"
    assert handle.docker_config_key == f"sources/{handle.build_id}/docker-config.json"


# ── S3 cleanup ───────────────────────────────────────────────────────────────────────

def test_cleanup_on_successful_wait(make_app, harness):
    app, infra = make_app()
    handle = start_build(app, infra, SHA)
    harness.ecs.describe_tasks.return_value = {"tasks": [_stopped_task(exit_code=0)], "failures": []}

    wait_for_build(handle)

    harness.s3.delete_object.assert_any_call(Bucket=handle.bucket, Key=handle.source_key)
    harness.s3.delete_object.assert_any_call(Bucket=handle.bucket, Key=handle.docker_config_key)


def test_cleanup_on_failed_wait(make_app, harness):
    app, infra = make_app()
    handle = start_build(app, infra, SHA)
    harness.ecs.describe_tasks.return_value = {"tasks": [_stopped_task(exit_code=1)], "failures": []}
    harness.logs.get_log_events.return_value = {"events": [{"message": "build failed"}]}

    with pytest.raises(BuildError):
        wait_for_build(handle)

    harness.s3.delete_object.assert_any_call(Bucket=handle.bucket, Key=handle.source_key)
    harness.s3.delete_object.assert_any_call(Bucket=handle.bucket, Key=handle.docker_config_key)


def test_cleanup_on_timeout(make_app, harness):
    app, infra = make_app()
    handle = start_build(app, infra, SHA)
    harness.ecs.describe_tasks.return_value = {
        "tasks": [{"lastStatus": "RUNNING", "containers": []}], "failures": [],
    }

    with pytest.raises(BuildError, match="timed out"):
        wait_for_build(handle, timeout=0)

    harness.ecs.stop_task.assert_called_once()
    assert harness.ecs.stop_task.call_args.kwargs["task"] == handle.task_arn
    harness.s3.delete_object.assert_any_call(Bucket=handle.bucket, Key=handle.source_key)
    harness.s3.delete_object.assert_any_call(Bucket=handle.bucket, Key=handle.docker_config_key)


def test_cleanup_on_exception_during_start_build(make_app, harness):
    app, infra = make_app()
    harness.ecs.run_task.side_effect = RuntimeError("ECS is down")

    with pytest.raises(RuntimeError):
        start_build(app, infra, SHA)

    assert harness.s3.delete_object.call_count == 2
    deleted_keys = {call.kwargs["Key"] for call in harness.s3.delete_object.call_args_list}
    assert any(k.endswith("/source.tar.gz") for k in deleted_keys)
    assert any(k.endswith("/docker-config.json") for k in deleted_keys)


def test_no_cleanup_attempted_when_nothing_was_ever_uploaded(make_app, harness, monkeypatch):
    app, infra = make_app()

    def _boom(*a, **k):
        raise RuntimeError("source fetch failed before any S3 write")

    monkeypatch.setattr(pb_mod, "fetch_source_tarball", _boom)

    with pytest.raises(RuntimeError):
        start_build(app, infra, SHA)

    harness.s3.delete_object.assert_not_called()


# ── timeout / exit-code mapping ──────────────────────────────────────────────────────

def test_timeout_force_stops_the_task(make_app, harness):
    app, infra = make_app()
    handle = start_build(app, infra, SHA)
    harness.ecs.describe_tasks.return_value = {
        "tasks": [{"lastStatus": "RUNNING", "containers": []}], "failures": [],
    }

    with pytest.raises(BuildError, match="timed out"):
        wait_for_build(handle, timeout=0)

    harness.ecs.stop_task.assert_called_once_with(
        cluster=handle.cluster, task=handle.task_arn, reason="Launchpad build timeout",
    )


def test_kaniko_exit_zero_is_success(make_app, harness):
    app, infra = make_app()
    handle = start_build(app, infra, SHA)
    harness.ecs.describe_tasks.return_value = {"tasks": [_stopped_task(exit_code=0)], "failures": []}

    result = wait_for_build(handle)

    assert isinstance(result, BuildResult)
    assert result.image_tag == handle.image_tag
    assert result.resolved_sha == SHA


def test_kaniko_nonzero_exit_is_failure(make_app, harness):
    app, infra = make_app()
    handle = start_build(app, infra, SHA)
    harness.ecs.describe_tasks.return_value = {"tasks": [_stopped_task(exit_code=1)], "failures": []}
    harness.logs.get_log_events.return_value = {"events": [{"message": "ERROR: build step failed"}]}

    with pytest.raises(BuildError):
        wait_for_build(handle)


def test_init_container_failure_means_kaniko_never_ran(make_app, harness):
    """The init container's dependsOn condition SUCCESS means Kaniko never starts if init
    fails — exitCode comes back None/missing, which must map to failure, not success."""
    app, infra = make_app()
    handle = start_build(app, infra, SHA)
    task = {
        "lastStatus": "STOPPED",
        "stoppedReason": "Essential container in task exited",
        "containers": [{"name": "init", "exitCode": 1}, {"name": "kaniko", "exitCode": None}],
    }
    harness.ecs.describe_tasks.return_value = {"tasks": [task], "failures": []}
    harness.logs.get_log_events.return_value = {"events": []}

    with pytest.raises(BuildError):
        wait_for_build(handle)


# ── digest capture ───────────────────────────────────────────────────────────────────

def test_digest_captured_via_ecr_describe_images_not_log_parsing(make_app, harness):
    app, infra = make_app()
    handle = start_build(app, infra, SHA)
    harness.ecs.describe_tasks.return_value = {"tasks": [_stopped_task(exit_code=0)], "failures": []}

    result = wait_for_build(handle)

    assert result.image_digest == "sha256:" + "b" * 64
    harness.ecr.describe_images.assert_called_once()
    harness.logs.get_log_events.assert_not_called()


def test_digest_lookup_failure_is_soft(make_app, harness):
    app, infra = make_app()
    handle = start_build(app, infra, SHA)
    harness.ecs.describe_tasks.return_value = {"tasks": [_stopped_task(exit_code=0)], "failures": []}
    harness.ecr.describe_images.side_effect = RuntimeError("ECR is unreachable")

    result = wait_for_build(handle)

    assert result.image_digest is None


# ── Docker Hub rate limit ───────────────────────────────────────────────────────────

def test_docker_hub_rate_limit_message(make_app, harness):
    app, infra = make_app()
    handle = start_build(app, infra, SHA)
    harness.ecs.describe_tasks.return_value = {"tasks": [_stopped_task(exit_code=1)], "failures": []}
    harness.logs.get_log_events.return_value = {
        "events": [{"message": "toomanyrequests: You have reached your pull rate limit"}]
    }

    with pytest.raises(BuildError, match="Docker Hub rate limit"):
        wait_for_build(handle)


def test_failure_without_logs_falls_back_to_stopped_reason(make_app, harness):
    app, infra = make_app()
    handle = start_build(app, infra, SHA)
    harness.ecs.describe_tasks.return_value = {
        "tasks": [_stopped_task(exit_code=1, stopped_reason="OutOfMemoryError: Container killed")],
        "failures": [],
    }
    harness.logs.get_log_events.return_value = {"events": []}

    with pytest.raises(BuildError, match="OutOfMemoryError"):
        wait_for_build(handle)


# ── mock mode ────────────────────────────────────────────────────────────────────────

def test_mock_infra_never_touches_real_aws(make_app, harness, monkeypatch):
    app, infra = make_app(is_mock=True)
    dev = SimpleNamespace(mode="dev", aws_access_key_id="x", aws_secret_access_key="y")
    monkeypatch.setattr(aws_session_mod, "app_config", dev)
    monkeypatch.setattr(pbc_mod, "app_config", dev)
    monkeypatch.setattr(pb_mod, "app_config", dev)
    monkeypatch.setattr(github_source_mod, "app_config", dev)

    def _boom(*a, **k):
        raise AssertionError("mock infra must never call a real AWS client")

    monkeypatch.setattr(pb_mod.boto3, "client", _boom)
    monkeypatch.setattr(pb_mod.boto3, "Session", _boom)

    handle = start_build(app, infra, SHA)
    result = wait_for_build(handle)

    assert handle.is_mock is True
    assert isinstance(result, BuildResult)
    assert result.image_digest and result.image_digest.startswith("sha256:")
    assert result.resolved_sha == SHA


def test_mock_digest_is_deterministic_for_same_app_and_build(make_app, monkeypatch):
    app, infra = make_app(is_mock=True)
    dev = SimpleNamespace(mode="dev", aws_access_key_id="x", aws_secret_access_key="y")
    monkeypatch.setattr(aws_session_mod, "app_config", dev)
    monkeypatch.setattr(pbc_mod, "app_config", dev)
    monkeypatch.setattr(pb_mod, "app_config", dev)
    monkeypatch.setattr(github_source_mod, "app_config", dev)

    handle = start_build(app, infra, SHA)
    result1 = wait_for_build(handle)
    result2 = wait_for_build(handle)

    assert result1.image_digest == result2.image_digest


# ── refusals ─────────────────────────────────────────────────────────────────────────

def test_refuses_mock_infra_in_real_mode(make_app, harness):
    app, infra = make_app(is_mock=True)

    with pytest.raises(ValueError, match="misconfigured"):
        start_build(app, infra, SHA)


def test_refuses_invalid_sha(make_app, harness):
    app, infra = make_app()

    with pytest.raises(BuildError, match="Invalid commit sha"):
        start_build(app, infra, "not-a-sha")


def test_refuses_infra_with_no_ecr_repository(make_app, harness):
    from api.models.environment import Environment

    app, infra = make_app()
    Environment.objects.filter(infrastructure_id=infra.id).update(ecr_repository_url=None)

    with pytest.raises(BuildError, match="no provisioned ECR repository"):
        start_build(app, infra, SHA)


# ── Dockerfile / build_context mapping ──────────────────────────────────────────────

def test_default_build_context_uses_tarball_top_dir_only(make_app, harness):
    app, infra = make_app(dockerfile_path="Dockerfile", build_context="")

    start_build(app, infra, SHA)

    overrides = {o["name"]: o for o in harness.ecs.run_task.call_args.kwargs["overrides"]["containerOverrides"]}
    kaniko_cmd = overrides["kaniko"]["command"]
    top_dir = _mock_tarball_names()[0].split("/", 1)[0]
    assert kaniko_cmd[kaniko_cmd.index("--context-sub-path") + 1] == top_dir
    assert kaniko_cmd[kaniko_cmd.index("--dockerfile") + 1] == "Dockerfile"


def test_build_context_narrows_context_sub_path_and_strips_dockerfile_prefix(make_app, harness):
    app, infra = make_app(dockerfile_path="backend/Dockerfile", build_context="backend")

    start_build(app, infra, SHA)

    overrides = {o["name"]: o for o in harness.ecs.run_task.call_args.kwargs["overrides"]["containerOverrides"]}
    kaniko_cmd = overrides["kaniko"]["command"]
    top_dir = _mock_tarball_names()[0].split("/", 1)[0]
    assert kaniko_cmd[kaniko_cmd.index("--context-sub-path") + 1] == f"{top_dir}/backend"
    assert kaniko_cmd[kaniko_cmd.index("--dockerfile") + 1] == "Dockerfile"
