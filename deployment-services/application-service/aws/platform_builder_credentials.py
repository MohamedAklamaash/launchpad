"""F7 ticket 3 — scoped ECR push-credential minting for the (future) platform-side
builder. Isolated from the deploy pipeline on purpose: nothing in
`application_deployment_service.py` calls this yet (see plan/F7-platform-builder.md,
"Credential flow"). This module only answers one question — "given an infrastructure and
one of its own ECR repositories, mint a short-lived, push-only, single-repository
credential" — so it can be security-reviewed and tested on its own before anything wires
a build container to it.

The worker already holds a full `LaunchpadDeploymentRole` session (`aws/session.py`),
scoped account-wide by `LaunchpadDeploymentPolicy` (today `ecr:*`). That session must
never reach a build container. Instead, the platform's own IAM user keys assume the same
role a second time with a `Policy=` session policy that narrows the result to exactly
`ecr:GetAuthorizationToken` (no resource-level permission exists for this action) plus the
push actions on exactly this infra's one repository ARN — never `*`, never another infra's
repository even in the same customer AWS account. This is a session policy narrowing an
already-granted permission, not a new grant, so no `policy.json` version bump is needed.
"""
import base64
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import boto3
from api.common.envs.application import app_config
from api.services.policy_errors import PolicyRefreshRequiredError
from botocore.config import Config
from botocore.exceptions import ClientError
from shared.mode import is_dev_mode

from aws.ecr import ECRClient
from aws.session import gate_mismatch

logger = logging.getLogger(__name__)

# STS minimum; re-minted per build, never reused across builds or infras.
SESSION_DURATION_SECONDS = 900

_ACCESS_DENIED_CODES = {"AccessDenied", "AccessDeniedException"}

# https://docs.aws.amazon.com/AmazonECR/latest/APIReference/API_CreateRepository.html
_ECR_REPO_NAME_RE = re.compile(r"^(?:[a-z0-9]+(?:[._-][a-z0-9]+)*/)*[a-z0-9]+(?:[._-][a-z0-9]+)*$")
_ECR_REPO_NAME_MAX_LENGTH = 256

_PUSH_ACTIONS = (
    "ecr:BatchCheckLayerAvailability",
    "ecr:InitiateLayerUpload",
    "ecr:UploadLayerPart",
    "ecr:CompleteLayerUpload",
    "ecr:PutImage",
    "ecr:BatchGetImage",
)


class InvalidPushRepositoryError(ValueError):
    """`repository_name` failed ECR's own naming rules, or isn't the one ECR repository
    provisioned for this infrastructure. Raised before any AWS call — the session
    policy's Resource ARN is built from this value, so a caller-supplied name must never
    reach AWS widened or pointed at a repository that isn't this infra's own."""


@dataclass(frozen=True, repr=False)
class PushCredential:
    registry: str
    username: str
    password: str
    expires_at: datetime

    def docker_config_json(self) -> dict:
        """`~/.docker/config.json`-shaped payload a build container can write to
        authenticate `docker push` without ever seeing this as a separate secret."""
        auth = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
        return {"auths": {self.registry: {"auth": auth}}}

    def __repr__(self) -> str:
        return (
            f"PushCredential(registry={self.registry!r}, username={self.username!r}, "
            f"password='***', expires_at={self.expires_at!r})"
        )


def _session_policy(region: str, account_id: str, repository_name: str) -> dict:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                # ecr:GetAuthorizationToken has no resource-level permissions in AWS's
                # model — unavoidable, matches the ce:* precedent already noted in
                # policy.json's own comments for Cost Explorer.
                "Effect": "Allow",
                "Action": "ecr:GetAuthorizationToken",
                "Resource": "*",
            },
            {
                "Effect": "Allow",
                "Action": list(_PUSH_ACTIONS),
                "Resource": f"arn:aws:ecr:{region}:{account_id}:repository/{repository_name}",
            },
        ],
    }


def _validate_repository_name(infrastructure, repository_name: str) -> None:
    if (
        not repository_name
        or len(repository_name) > _ECR_REPO_NAME_MAX_LENGTH
        or not _ECR_REPO_NAME_RE.match(repository_name)
    ):
        raise InvalidPushRepositoryError(f"'{repository_name}' is not a valid ECR repository name")

    from api.models.environment import Environment

    environment = Environment.objects.filter(infrastructure_id=infrastructure.id).first()
    expected = environment and ECRClient.repository_name_from_url(environment.ecr_repository_url)
    if not expected or repository_name != expected:
        raise InvalidPushRepositoryError(
            "repository_name does not match this infrastructure's provisioned ECR repository"
        )


def _abort_if_exited(infrastructure) -> None:
    """Mirrors `ApplicationDeploymentService._abort_if_exited` (H2): a fresh, minimal read
    of just `exited_at`, not the possibly-stale `infrastructure` object the caller passed
    in — pushing into the customer's ECR is a mutation into their account and must not
    proceed past an exit that landed since that object was read."""
    from api.models.infrastructure import Infrastructure
    from api.services.exit_enforcement import InfrastructureExitedError

    exited_at = (
        Infrastructure.objects.filter(id=infrastructure.id).values_list("exited_at", flat=True).first()
    )
    if exited_at is not None:
        raise InfrastructureExitedError(
            "Infrastructure exited — refusing to mint an ECR push credential."
        )


def _raise_mapped_error(exc: ClientError, action: str):
    code = exc.response.get("Error", {}).get("Code", "Unknown")
    if code in _ACCESS_DENIED_CODES:
        raise PolicyRefreshRequiredError(
            "Launchpad's IAM role in your AWS account is missing the ECR push "
            "permissions. Re-run the refresh policy script and try again.",
            denied_actions=[action],
        ) from exc
    raise exc


def _assume_scoped_role(infrastructure, repository_name: str, region: str) -> dict:
    """Assume LaunchpadDeploymentRole a second time, from the platform's own IAM user
    keys (never from the worker's already-assumed, account-wide session), with a
    session Policy that narrows the result to exactly the push credential this build
    needs. Returns the raw STS credential triple — the caller must never expose it; only
    `get_authorization_token()`'s {username, password, registry} ever leaves this module.
    """
    sts_client = boto3.client(
        "sts",
        aws_access_key_id=app_config.aws_access_key_id,
        aws_secret_access_key=app_config.aws_secret_access_key,
        config=Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 2}),
    )
    try:
        response = sts_client.assume_role(
            RoleArn=f"arn:aws:iam::{infrastructure.code}:role/LaunchpadDeploymentRole",
            # Matches _assume_role_raw's launchpad-{infra.id} convention — ties a
            # CloudTrail entry for this narrowed session straight back to the infra it
            # built for, the same way the wide worker session's own name does.
            RoleSessionName=f"launchpad-build-{infrastructure.id}",
            ExternalId=str(infrastructure.id),
            DurationSeconds=SESSION_DURATION_SECONDS,
            Policy=json.dumps(_session_policy(region, infrastructure.code, repository_name)),
        )
    except ClientError as e:
        _raise_mapped_error(e, "sts:AssumeRole")
    return response["Credentials"]


def _get_authorization_token(credentials: dict, region: str) -> dict:
    ecr_client = boto3.client(
        "ecr",
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
        region_name=region,
        config=Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 2}),
    )
    try:
        response = ecr_client.get_authorization_token()
    except ClientError as e:
        _raise_mapped_error(e, "ecr:GetAuthorizationToken")
    auth_data = response["authorizationData"][0]
    return auth_data


def _mock_push_credential(infrastructure, repository_name: str, region: str) -> PushCredential:
    """Deterministic fake credential for is_mock infras / MODE=dev — never touches AWS,
    mirroring aws/session.py's `_build_mock_session`."""
    logger.warning(
        "MOCK ECR push credential minted in dev mode (no AWS calls)",
        extra={"infra_id": str(infrastructure.id), "is_mock": True},
    )
    account_id = infrastructure.code or "000000000000"
    seed = hashlib.md5(f"{infrastructure.id}:{repository_name}".encode()).hexdigest()[:12]
    return PushCredential(
        registry=f"{account_id}.dkr.ecr.{region}.amazonaws.com",
        username="AWS",
        password=f"mock-ecr-token-{seed}",
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=SESSION_DURATION_SECONDS),
    )


def _mint_real_push_credential(infrastructure, repository_name: str, region: str) -> PushCredential:
    credentials = _assume_scoped_role(infrastructure, repository_name, region)
    auth_data = _get_authorization_token(credentials, region)

    token = base64.b64decode(auth_data["authorizationToken"]).decode()
    username, _, password = token.partition(":")
    registry = auth_data.get(
        "proxyEndpoint", f"https://{infrastructure.code}.dkr.ecr.{region}.amazonaws.com"
    ).removeprefix("https://")

    ecr_expires_at = auth_data["expiresAt"]
    if not isinstance(ecr_expires_at, datetime):
        ecr_expires_at = datetime.fromisoformat(str(ecr_expires_at))
    # Open question in plan/F7-platform-builder.md's Credential flow #6, not yet verified
    # against real AWS: whether an ECR auth token minted from a 900s STS session stays
    # valid for the token's own nominal ~12h, or dies with the narrowed session. The plan
    # says to assume the pessimistic case until that's confirmed — clamp to whichever
    # expiry is sooner rather than report a 12h validity window that's actually ~15min.
    expires_at = min(ecr_expires_at, credentials["Expiration"])

    logger.info(
        "Minted scoped ECR push credential",
        extra={
            "infra_id": str(infrastructure.id),
            "repository_name": repository_name,
            "expires_at": expires_at.isoformat(),
        },
    )
    return PushCredential(registry=registry, username=username, password=password, expires_at=expires_at)


def mint_push_credential(infrastructure, repository_name: str) -> PushCredential:
    """Mint a push-only, single-repository, 900-second ECR credential for `infrastructure`.

    Refuses: a `repository_name` that isn't valid ECR naming or isn't this infra's one
    provisioned repository; a mock/real MODE mismatch (`gate_mismatch`); an infrastructure
    whose read-model mirror already shows `exited_at` set (H2). On AccessDenied from AWS,
    raises `PolicyRefreshRequiredError` so the deploy pipeline can map it to the same
    policy_refresh_required shape every other AWS-permission gap uses.
    """
    _validate_repository_name(infrastructure, repository_name)

    if gate_mismatch(infrastructure):
        raise ValueError("AWS access is misconfigured for this infrastructure")

    _abort_if_exited(infrastructure)

    region = (infrastructure.metadata or {}).get("aws_region", "us-west-2")

    if is_dev_mode(app_config.mode) or bool(getattr(infrastructure, "is_mock", False)):
        return _mock_push_credential(infrastructure, repository_name, region)

    return _mint_real_push_credential(infrastructure, repository_name, region)
