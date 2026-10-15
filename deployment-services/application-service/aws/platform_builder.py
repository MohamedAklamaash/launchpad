"""F7 ticket 5 — the platform-side builder client.

Launches one Fargate task per build against `infra/platform-builder/`'s Kaniko task
definition, waits for it, and summarizes a failure. Not yet wired into
`application_deployment_service.py` (ticket 6) — this module only answers "given an
application, its infrastructure, and a resolved commit sha, build and push an image, or
fail clearly," mirroring `aws/codebuild.py`'s `CodeBuildClient.start_build`/
`wait_for_build`/`_get_build_error` shape so ticket 6 can swap builders behind a flag.

Design choices this module makes that aren't dictated by the terraform skeleton alone:

- **The worker never touches the sources bucket or the builder cluster with the platform
  IAM user's own keys.** That user stays assume-only (no policy attached to it directly,
  per `infra/platform-builder/iam.tf`'s `builder_launcher` role) — every S3/ECS/CloudWatch
  call in this module goes through a session assumed into
  `PLATFORM_BUILDER_LAUNCHER_ROLE_ARN` first (`_launcher_session`, mirroring
  `aws/eks.py:assume_deploy_role`'s shape for the analogous "chain into a narrower role
  before touching anything" pattern).
- **Kaniko's `command` override is a plain argv list, never a shell string.** No
  `sh -c "... && echo ..."` wrapping — the mirrored Kaniko image may be distroless (the
  spike used `executor:debug` specifically because it isn't), and a user-controlled
  `Application.dockerfile_path`/`build_context` has no business being interpolated into
  anything a shell parses. ECS `containerOverrides.command` entries are passed to the
  container's entrypoint as exact argv elements regardless, so this costs nothing.
- **Digest capture goes through `ecr:DescribeImages` against the customer's own
  AssumeRole session, not a log line.** `--digest-file` still gets passed to Kaniko (it's
  free, and a real-AWS pass may still want the file), but with no shell to echo it into
  the log stream, parsing it back out isn't simpler than describing the image that was
  just pushed under a tag this module already knows. A miss here is a soft failure — the
  caller's existing `ECRClient.get_image_digest` fallback (already used by
  `_record_deployment` for every image the old CodeBuild path pushes) tries again later.
- **`BuildHandle` carries no credential, ever** — only names/keys/ids needed to poll,
  tail logs, and clean up. The scoped push credential this module mints
  (`mint_push_credential`) is written straight to S3 and never held as Python state past
  that call.

REAL-AWS-UNVERIFIED (plan/F7-platform-builder.md, ticket 9's checklist): the
`--context-sub-path`/`--dockerfile` mapping for a non-default `build_context` (see
`_kaniko_context_and_dockerfile`), whether `logs:GetLogEvents` sees a container's output
before the task fully stops, and the whole digest-lookup-after-push race.
"""
import hashlib
import json
import logging
import os
import re
import tarfile
import tempfile
import time
import uuid
from dataclasses import dataclass

import boto3
from api.common.envs.application import app_config
from api.common.naming import app_slug
from botocore.config import Config
from botocore.exceptions import ClientError
from shared.mode import is_dev_mode

from aws.ecr import ECRClient
from aws.github_source import (
    docker_config_object_key,
    fetch_source_tarball,
    source_bucket,
    source_object_key,
    upload_source_for_build,
)
from aws.platform_builder_credentials import mint_push_credential
from aws.session import gate_mismatch, get_boto3_config

logger = logging.getLogger(__name__)

KANIKO_CONTAINER_NAME = "kaniko"
INIT_CONTAINER_NAME = "init"
# Must match infra/platform-builder/ecs.tf's `awslogs-stream-prefix`.
LOG_STREAM_PREFIX = "build"
# Ticket 8's build-logs service checks for this prefix to short-circuit a mock build's
# log tail without any AWS call — there is no real ECS task or CloudWatch stream behind it.
MOCK_TASK_ID_PREFIX = "mock-task-"

DEFAULT_BUILD_TIMEOUT_SECONDS = 1800
POLL_INTERVAL_SECONDS = 10
# Long enough to cover launch + the full build timeout + cleanup; re-assumed fresh by
# every top-level call rather than cached, so a slow build never runs on a session that
# expired mid-poll.
LAUNCHER_SESSION_DURATION_SECONDS = 3600

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class BuildError(Exception):
    """Raised for any start/wait/launch failure in this module. `Application.error_message`
    is written from this (via `sanitize_deploy_error`'s plain `str(exc)` branch), so a
    message here is always fixed text plus non-secret fields — never a raw boto3
    payload, a credential, or `str()` of some other caught exception."""


@dataclass(frozen=True)
class BuildHandle:
    build_id: str
    infrastructure: object
    application_id: str
    repository_name: str
    image_tag: str
    sha: str
    destination: str
    is_mock: bool
    cluster: str | None = None
    task_arn: str | None = None
    bucket: str | None = None
    source_key: str | None = None
    docker_config_key: str | None = None


@dataclass(frozen=True)
class BuildResult:
    image_tag: str
    resolved_sha: str
    image_digest: str | None


def _env(name: str, *, required: bool = True, default: str = "") -> str:
    value = os.environ.get(name, default).strip()
    if required and not value:
        raise BuildError(f"{name} is not configured")
    return value


def _launcher_role_arn() -> str:
    return _env("PLATFORM_BUILDER_LAUNCHER_ROLE_ARN")


def _cluster() -> str:
    return _env("PLATFORM_BUILDER_CLUSTER")


def _task_definition() -> str:
    return _env("PLATFORM_BUILDER_TASK_DEFINITION")


def _subnet_ids() -> list[str]:
    raw = os.environ.get("PLATFORM_BUILDER_SUBNET_IDS", "")
    subnets = [s.strip() for s in raw.split(",") if s.strip()]
    if not subnets:
        raise BuildError("PLATFORM_BUILDER_SUBNET_IDS is not configured")
    return subnets


def _security_group_id() -> str:
    return _env("PLATFORM_BUILDER_SECURITY_GROUP_ID")


def _log_group() -> str:
    return _env("PLATFORM_BUILDER_LOG_GROUP")


def _region() -> str:
    return _env("PLATFORM_BUILDER_AWS_REGION", required=False, default="us-east-1") or "us-east-1"


def _build_timeout() -> int:
    return int(os.environ.get("PLATFORM_BUILDER_BUILD_TIMEOUT_SECONDS", str(DEFAULT_BUILD_TIMEOUT_SECONDS)))


def _launcher_session(*, duration_seconds: int = LAUNCHER_SESSION_DURATION_SECONDS, sts_config: Config | None = None):
    """Assumes `PLATFORM_BUILDER_LAUNCHER_ROLE_ARN` from the platform IAM user's own keys
    and returns a session scoped to exactly that role's policy — RunTask/DescribeTasks/
    StopTask on the builder cluster, PutObject/DeleteObject under the sources bucket's
    prefix, GetLogEvents on the build log group, PassRole for the task's own two roles.
    Never the worker's `LaunchpadDeploymentRole` session (that one must never reach a
    build container) and never the bare platform user keys (which carry no policy of
    their own by design — see `infra/platform-builder/README.md`).

    `duration_seconds`/`sts_config` default to the build flow's own values (a full hour,
    a fixed 5s/10s STS config) so every existing caller is unaffected; ticket 8's log-tail
    read path passes shorter values of both, since that path runs on every poll of an
    in-progress build and sits behind the gateway's own request timeout."""
    sts_client = boto3.client(
        "sts",
        aws_access_key_id=app_config.aws_access_key_id,
        aws_secret_access_key=app_config.aws_secret_access_key,
        config=sts_config or Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 2}),
    )
    response = sts_client.assume_role(
        RoleArn=_launcher_role_arn(),
        RoleSessionName="launchpad-builder-launcher"[:64],
        DurationSeconds=duration_seconds,
    )
    creds = response["Credentials"]
    return boto3.Session(
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
        region_name=_region(),
    )


# Short-lived: a log-tail read is a single request-scoped call, never held across polls,
# so it doesn't need the hour-long session a build launch mints for itself.
READ_SESSION_DURATION_SECONDS = 900


def launcher_session_for_read(*, config: Config | None = None):
    """Public entrypoint for ticket 8's build-log-tail endpoint: the same launcher role
    every build assumes, sized for a single bounded read rather than a build's full
    lifetime. `config` should be deadline-aware (see `_budget_config`-style callers) so a
    slow AssumeRole can't by itself blow the request past the gateway's proxy timeout."""
    return _launcher_session(duration_seconds=READ_SESSION_DURATION_SECONDS, sts_config=config)


def task_id_from_arn(task_arn: str) -> str:
    """The ECS task id is the ARN's final path segment — the only part the awslogs
    stream-name convention (`<prefix>/<container>/<task id>`) actually uses."""
    return task_arn.rsplit("/", 1)[-1]


def build_log_group() -> str:
    return _log_group()


def _tarball_top_dir(tarball_path) -> str:
    """GitHub's tarball API wraps every file under a single `{owner}-{repo}-{sha7}/`
    directory — read it from the tarball actually fetched rather than reconstructing it
    from owner/repo/sha, which GitHub does not document as a stable naming contract."""
    with tarfile.open(tarball_path, "r:gz") as tar:
        names = tar.getnames()
    if not names:
        raise BuildError("Fetched source tarball is empty")
    top_dir = names[0].split("/", 1)[0]
    if not top_dir or top_dir in (".", ".."):
        raise BuildError("Fetched source tarball has no identifiable top-level directory")
    return top_dir


def _kaniko_context_and_dockerfile(top_dir: str, build_context: str | None, dockerfile_path: str | None) -> tuple[str, str]:
    """Maps `Application.build_context`/`dockerfile_path` onto Kaniko's flags.

    Today's buildspec (`aws/codebuild.py:_get_buildspec`) runs `docker build -f
    FULL_DOCKERFILE CTX`, where `FULL_DOCKERFILE` is `BUILD_CONTEXT/DOCKERFILE_PATH`
    unless `DOCKERFILE_PATH` already starts with `BUILD_CONTEXT` — `docker build -f`
    resolves against the working directory (the repo root), not `CTX`. Kaniko's
    `--dockerfile` resolves against `--context-sub-path` instead (the context root
    *after* narrowing), so the composition has to run the other way: narrow the context
    to `top_dir/build_context`, then strip that same prefix back off the dockerfile path
    rather than adding it.
    """
    build_context = (build_context or "").strip().strip("/")
    dockerfile_path = (dockerfile_path or "Dockerfile").strip().strip("/")

    context_sub_path = f"{top_dir}/{build_context}" if build_context else top_dir

    if not build_context:
        return context_sub_path, dockerfile_path

    if dockerfile_path == build_context:
        raise BuildError("dockerfile_path must name a file, not the build context directory")
    if dockerfile_path.startswith(f"{build_context}/"):
        return context_sub_path, dockerfile_path[len(build_context) + 1:]
    return context_sub_path, dockerfile_path


def _run_build_task(session, *, bucket: str, source_key: str, docker_config_key: str,
                     context_sub_path: str, dockerfile_arg: str, destination: str) -> str:
    ecs = session.client("ecs", config=get_boto3_config())
    response = ecs.run_task(
        cluster=_cluster(),
        taskDefinition=_task_definition(),
        launchType="FARGATE",
        count=1,
        networkConfiguration={
            "awsvpcConfiguration": {
                "subnets": _subnet_ids(),
                "securityGroups": [_security_group_id()],
                "assignPublicIp": "DISABLED",
            }
        },
        overrides={
            "containerOverrides": [
                {
                    "name": INIT_CONTAINER_NAME,
                    # Plain argv — the aws-cli image's own entrypoint is `aws`.
                    "command": ["s3", "cp", f"s3://{bucket}/{docker_config_key}", "/kaniko/.docker/config.json"],
                },
                {
                    "name": KANIKO_CONTAINER_NAME,
                    "command": [
                        "--context", f"s3://{bucket}/{source_key}",
                        "--context-sub-path", context_sub_path,
                        "--dockerfile", dockerfile_arg,
                        "--destination", destination,
                        "--digest-file", "/tmp/image-digest",
                    ],
                },
            ]
        },
    )
    failures = response.get("failures") or []
    tasks = response.get("tasks") or []
    if failures or not tasks:
        reason = "; ".join(f"{f.get('arn', '?')}: {f.get('reason', 'unknown')}" for f in failures) or "no task returned"
        raise BuildError(f"Failed to launch build task: {reason}")
    return tasks[0]["taskArn"]


def _cleanup_source_objects(session, bucket: str | None, source_key: str | None, docker_config_key: str | None) -> None:
    """Best-effort, always called — a failed delete must never mask the build's real
    outcome, and a key that was never written (bucket/source_key/docker_config_key still
    None because an earlier step failed) is skipped rather than attempted."""
    if not bucket:
        return
    s3 = session.client("s3", config=get_boto3_config())
    for key in (source_key, docker_config_key):
        if not key:
            continue
        try:
            s3.delete_object(Bucket=bucket, Key=key)
        except ClientError as e:
            logger.warning(f"Could not delete build source object {key}: {e}")


def _mock_sha_guard(sha: str) -> None:
    if not isinstance(sha, str) or not _SHA_RE.fullmatch(sha):
        raise BuildError("Invalid commit sha: must be exactly 40 lowercase hex characters")


def _resolve_repository(infrastructure):
    from api.models.environment import Environment

    environment = Environment.objects.filter(infrastructure_id=infrastructure.id).first()
    if not environment or not environment.ecr_repository_url:
        raise BuildError("Infrastructure has no provisioned ECR repository")
    repository_name = ECRClient.repository_name_from_url(environment.ecr_repository_url)
    return environment, repository_name


def _mock_build_handle(application, infrastructure, sha: str, repository_name: str, image_tag: str, destination: str) -> BuildHandle:
    build_id = uuid.uuid4().hex
    logger.warning(
        "MOCK platform build started in dev mode (no AWS calls)",
        extra={"infra_id": str(infrastructure.id), "build_id": build_id, "is_mock": True},
    )
    # Exercise the real fetch/upload/credential code paths — each already no-ops its own
    # AWS call in mock mode, so this still proves the plumbing between them works without
    # a second, divergent "pretend" branch here.
    with tempfile.TemporaryDirectory(prefix="lp-build-mock-") as tmp_dir:
        tarball_path = os.path.join(tmp_dir, "source.tar.gz")
        fetch_source_tarball(application, sha, tarball_path)
        upload_source_for_build(tarball_path, build_id)
    mint_push_credential(infrastructure, repository_name)

    return BuildHandle(
        build_id=build_id,
        infrastructure=infrastructure,
        application_id=str(application.id),
        repository_name=repository_name,
        image_tag=image_tag,
        sha=sha,
        destination=destination,
        is_mock=True,
        task_arn=f"{MOCK_TASK_ID_PREFIX}{build_id}",
        bucket=None,
        source_key=source_object_key(build_id),
        docker_config_key=docker_config_object_key(build_id),
    )


def start_build(application, infrastructure, sha: str) -> BuildHandle:
    """Fetches `application`'s source at `sha`, uploads it alongside a scoped,
    single-repository ECR push credential to the platform's sources bucket, and launches
    one Fargate task to build and push it. Build ids are `uuid4().hex` — unguessable by
    construction, since the build task role can read but never list the sources bucket
    (`infra/platform-builder/iam.tf`), so a predictable id would let one build's task
    reach another build's source or credential.

    Refuses a mock/real `MODE` mismatch (`gate_mismatch`, same guard every other AWS
    client in this codebase uses) and an invalid `sha`."""
    _mock_sha_guard(sha)

    if gate_mismatch(infrastructure):
        raise ValueError("AWS access is misconfigured for this infrastructure")

    _environment, repository_name = _resolve_repository(infrastructure)
    image_tag = f"{app_slug(application.name)}-{sha}"
    destination = f"{_environment.ecr_repository_url}:{image_tag}"

    if is_dev_mode(app_config.mode) or bool(getattr(infrastructure, "is_mock", False)):
        return _mock_build_handle(application, infrastructure, sha, repository_name, image_tag, destination)

    build_id = uuid.uuid4().hex
    session = _launcher_session()
    bucket = source_bucket()
    source_key: str | None = None
    docker_config_key: str | None = None

    with tempfile.TemporaryDirectory(prefix="lp-build-") as tmp_dir:
        tarball_path = os.path.join(tmp_dir, "source.tar.gz")
        try:
            fetch_source_tarball(application, sha, tarball_path)
            top_dir = _tarball_top_dir(tarball_path)

            source_key = source_object_key(build_id)
            upload_source_for_build(tarball_path, build_id, s3_client=session.client("s3", config=get_boto3_config()))

            credential = mint_push_credential(infrastructure, repository_name)
            docker_config_key = docker_config_object_key(build_id)
            session.client("s3", config=get_boto3_config()).put_object(
                Bucket=bucket,
                Key=docker_config_key,
                Body=json.dumps(credential.docker_config_json()).encode("utf-8"),
                ServerSideEncryption="AES256",
                ContentType="application/json",
            )

            context_sub_path, dockerfile_arg = _kaniko_context_and_dockerfile(
                top_dir, application.build_context, application.dockerfile_path,
            )
            task_arn = _run_build_task(
                session,
                bucket=bucket,
                source_key=source_key,
                docker_config_key=docker_config_key,
                context_sub_path=context_sub_path,
                dockerfile_arg=dockerfile_arg,
                destination=destination,
            )
        except Exception:
            _cleanup_source_objects(session, bucket, source_key, docker_config_key)
            raise

    logger.info(
        "Started platform build",
        extra={"infra_id": str(infrastructure.id), "app_id": str(application.id), "build_id": build_id},
    )
    return BuildHandle(
        build_id=build_id,
        infrastructure=infrastructure,
        application_id=str(application.id),
        repository_name=repository_name,
        image_tag=image_tag,
        sha=sha,
        destination=destination,
        is_mock=False,
        cluster=_cluster(),
        task_arn=task_arn,
        bucket=bucket,
        source_key=source_key,
        docker_config_key=docker_config_key,
    )


def _lookup_pushed_digest(handle: BuildHandle) -> str | None:
    try:
        from aws.session import create_boto3_session

        session = create_boto3_session(handle.infrastructure)
        return ECRClient(session).get_image_digest(handle.repository_name, handle.image_tag)
    except Exception as e:
        logger.warning(f"Could not resolve pushed image digest for build {handle.build_id}: {e}")
        return None


def _mock_build_result(handle: BuildHandle) -> BuildResult:
    # Full application id, never a slice (CLAUDE.md) — deterministic so repeated test
    # runs against the same mock app/build agree, but not forceable from a short prefix.
    seed = f"{handle.application_id}:{handle.build_id}"
    digest = "sha256:" + hashlib.sha256(seed.encode()).hexdigest()
    return BuildResult(image_tag=handle.image_tag, resolved_sha=handle.sha, image_digest=digest)


def _poll_until_stopped(ecs, handle: BuildHandle, timeout: int) -> dict:
    start = time.time()
    while True:
        response = ecs.describe_tasks(cluster=handle.cluster, tasks=[handle.task_arn])
        failures = response.get("failures") or []
        tasks = response.get("tasks") or []
        if not tasks:
            reason = failures[0].get("reason", "unknown") if failures else "unknown"
            raise BuildError(f"Build task for build {handle.build_id} not found: {reason}")

        task = tasks[0]
        if task.get("lastStatus") == "STOPPED":
            return task

        if time.time() - start > timeout:
            try:
                ecs.stop_task(cluster=handle.cluster, task=handle.task_arn, reason="Launchpad build timeout")
            except ClientError as e:
                logger.warning(f"Could not stop timed-out build task {handle.build_id}: {e}")
            raise BuildError(f"Build {handle.build_id} timed out after {timeout} seconds")

        time.sleep(POLL_INTERVAL_SECONDS)


def wait_for_build(handle: BuildHandle, timeout: int | None = None) -> BuildResult:
    """Blocks until the build task stops. Success iff the `kaniko` container's own
    exit code is 0 — if the `init` container failed, Kaniko's `dependsOn` condition means
    it never started at all, which reports here as a non-zero/missing exit code the same
    as any other build failure, not a separate case. Always deletes both per-build S3
    objects before returning or raising, whether the build succeeded, failed, or timed
    out."""
    if handle.is_mock:
        return _mock_build_result(handle)

    timeout = timeout if timeout is not None else _build_timeout()
    session = _launcher_session()
    ecs = session.client("ecs", config=get_boto3_config())

    try:
        task = _poll_until_stopped(ecs, handle, timeout)
        containers = {c.get("name"): c for c in task.get("containers", [])}
        kaniko = containers.get(KANIKO_CONTAINER_NAME) or {}
        if kaniko.get("exitCode") == 0:
            return BuildResult(
                image_tag=handle.image_tag,
                resolved_sha=handle.sha,
                image_digest=_lookup_pushed_digest(handle),
            )

        raise BuildError(get_build_error(handle, task=task, session=session) or f"Build {handle.build_id} failed")
    finally:
        _cleanup_source_objects(session, handle.bucket, handle.source_key, handle.docker_config_key)


def _tail_build_logs(session, handle: BuildHandle, container_name: str) -> str | None:
    """The launcher role grants `logs:GetLogEvents` only — not the broader
    `FilterLogEvents` `aws/codebuild.py` uses — so this reads the one stream the named
    container's own awslogs driver wrote to."""
    if not handle.task_arn:
        return None
    task_id = task_id_from_arn(handle.task_arn)
    stream_name = f"{LOG_STREAM_PREFIX}/{container_name}/{task_id}"

    logs_client = session.client("logs", config=get_boto3_config())
    try:
        response = logs_client.get_log_events(
            logGroupName=_log_group(), logStreamName=stream_name, startFromHead=False, limit=200,
        )
    except ClientError as e:
        logger.debug(f"Could not fetch build logs for stream {stream_name}: {e}")
        return None

    messages = [event["message"] for event in response.get("events", [])]
    return "\n".join(messages) if messages else None


def get_build_error(handle: BuildHandle, *, task: dict | None = None, session=None) -> str | None:
    """Tails whichever container actually failed and returns a sanitized summary — fixed
    text plus log lines or AWS's own `stoppedReason`, never `str()` of a caught
    exception. Carries over the CodeBuild path's Docker Hub rate-limit special case
    (`aws/codebuild.py:wait_for_build`) verbatim, since a customer `FROM` line hitting it
    looks identical regardless of which builder pulled the image."""
    if handle.is_mock:
        return None

    session = session or _launcher_session()

    if task is None:
        ecs = session.client("ecs", config=get_boto3_config())
        try:
            response = ecs.describe_tasks(cluster=handle.cluster, tasks=[handle.task_arn])
            tasks = response.get("tasks") or []
            task = tasks[0] if tasks else None
        except ClientError as e:
            logger.warning(f"Could not describe build task {handle.build_id}: {e}")
            task = None

    containers = {c.get("name"): c for c in (task or {}).get("containers", [])}
    failing_container = KANIKO_CONTAINER_NAME
    for name, info in containers.items():
        if info.get("exitCode") not in (0, None):
            failing_container = name
            break

    log_tail = _tail_build_logs(session, handle, failing_container)
    if log_tail:
        if "toomanyrequests" in log_tail.lower() or "429" in log_tail:
            return (
                "Docker Hub rate limit exceeded. "
                "Update your Dockerfile to use ECR Public Gallery images "
                "(e.g. public.ecr.aws/docker/library/python:3.11-slim)."
            )
        lines = [line for line in log_tail.splitlines() if line.strip()]
        return "\n".join(lines[-10:])

    stopped_reason = (task or {}).get("stoppedReason")
    if stopped_reason:
        return f"Build failed: {stopped_reason}"
    return f"Build {handle.build_id} failed with no further detail available"
