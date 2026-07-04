import boto3
from botocore.config import Config
from botocore.credentials import RefreshableCredentials
from botocore.session import get_session as _get_botocore_session
import logging
import os
from datetime import datetime, timezone
import threading
from collections import OrderedDict
from api.common.envs.application import app_config
from api.mock.mock_session import MockSession, DEFAULT_REGION, MOCK_ACCOUNT_ID
from shared.mode import is_dev_mode

logger = logging.getLogger(__name__)


def _is_mock_infrastructure(infrastructure) -> bool:
    return bool(getattr(infrastructure, "is_mock", False))


def _build_mock_session(infrastructure) -> MockSession:
    metadata = infrastructure.metadata or {}
    region = metadata.get("aws_region", DEFAULT_REGION)
    account_id = infrastructure.code or MOCK_ACCOUNT_ID
    logger.warning(
        "MOCK boto3 session created in dev mode (no AWS calls)",
        extra={"infra_id": str(infrastructure.id), "is_mock": True},
    )
    return MockSession(region=region, account_id=account_id, infra_id=str(infrastructure.id))

BOTO3_CONFIG = Config(
    retries={
        'max_attempts': int(os.environ.get('AWS_MAX_RETRY_ATTEMPTS', '10')),
        'mode': os.environ.get('AWS_RETRY_MODE', 'adaptive')
    },
    connect_timeout=int(os.environ.get('AWS_CONNECT_TIMEOUT', '10')),
    read_timeout=int(os.environ.get('AWS_READ_TIMEOUT', '60'))
)

# Per-infra last-refresh timestamps to rate-limit STS calls (max once per 5 min).
_last_refresh: OrderedDict = OrderedDict()
_last_refresh_lock = threading.Lock()
_MAX_REFRESH_ENTRIES = 500
_REFRESH_RATE_LIMIT_SECONDS = 60


def create_boto3_session(infrastructure):
    dev_mode = is_dev_mode(app_config.mode)
    if _is_mock_infrastructure(infrastructure) and not dev_mode:
        raise ValueError("Refusing real AWS session against a mock infrastructure")
    if dev_mode and not _is_mock_infrastructure(infrastructure):
        raise ValueError("Refusing mock AWS session against a real infrastructure")
    if _is_mock_infrastructure(infrastructure):
        return _build_mock_session(infrastructure)

    metadata = infrastructure.metadata or {}
    if not metadata.get('aws_access_key_id') and not infrastructure.code:
        raise ValueError("Infrastructure not authenticated with AWS")

    # Refreshable credentials: every client built from this session transparently re-assumes
    # the role as the STS token nears expiry, so a long deploy (30-min build + ECS/ALB calls)
    # can't die on a 1-hour token mid-flight.
    return _build_real_session(infrastructure)


def get_boto3_config():
    return BOTO3_CONFIG


def _refresh_credentials(infrastructure):
    """Refresh AWS STS credentials with rate limiting and timeout."""
    if _is_mock_infrastructure(infrastructure):
        logger.warning(
            "MOCK credential refresh skipped in dev mode (no STS)",
            extra={"infra_id": str(infrastructure.id), "is_mock": True},
        )
        return

    infra_id = str(infrastructure.id)
    now = datetime.now(timezone.utc).timestamp()

    with _last_refresh_lock:
        last = _last_refresh.get(infra_id, 0)
        if now - last < _REFRESH_RATE_LIMIT_SECONDS:
            logger.info(f"Skipping credential refresh for {infra_id} — rate limited (last refresh {int(now - last)}s ago)")
            return
        _last_refresh[infra_id] = now
        _last_refresh.move_to_end(infra_id)
        if len(_last_refresh) > _MAX_REFRESH_ENTRIES:
            _last_refresh.popitem(last=False)

    _assume_role_raw(infrastructure)
    logger.info(f"Refreshed credentials for infrastructure {infra_id}")


def _assume_role_raw(infrastructure) -> dict:
    """Assume LaunchpadDeploymentRole, persist creds to metadata, and return a botocore
    refresh dict (access_key/secret_key/token/expiry_time). No rate-limiting — callers that
    need it wrap this; RefreshableCredentials calls it only when a token nears expiry."""
    target_account_id = infrastructure.code
    sts_client = boto3.client(
        "sts",
        aws_access_key_id=app_config.aws_access_key_id,
        aws_secret_access_key=app_config.aws_secret_access_key,
        config=Config(connect_timeout=5, read_timeout=10, retries={'max_attempts': 2}),
    )
    response = sts_client.assume_role(
        RoleArn=f"arn:aws:iam::{target_account_id}:role/LaunchpadDeploymentRole",
        RoleSessionName=f"launchpad-{infrastructure.id}",
        ExternalId=str(infrastructure.id),
    )
    creds = response["Credentials"]
    expiry = creds["Expiration"]
    expiry_iso = expiry.isoformat() if hasattr(expiry, "isoformat") else str(expiry)
    infrastructure.metadata = {
        **(infrastructure.metadata or {}),
        "aws_access_key_id": creds["AccessKeyId"],
        "aws_secret_access_key": creds["SecretAccessKey"],
        "aws_session_token": creds["SessionToken"],
        "expiration": expiry_iso,
    }
    infrastructure.save()
    return {
        "access_key": creds["AccessKeyId"],
        "secret_key": creds["SecretAccessKey"],
        "token": creds["SessionToken"],
        "expiry_time": expiry_iso,
    }


def _build_real_session(infrastructure):
    """Real boto3 session backed by auto-refreshing STS credentials (re-assumes near expiry)."""
    metadata = infrastructure.metadata or {}
    region = metadata.get("aws_region", "us-west-2")

    def _refresh():
        return _assume_role_raw(infrastructure)

    if metadata.get("aws_access_key_id") and metadata.get("expiration"):
        seed = {
            "access_key": metadata["aws_access_key_id"],
            "secret_key": metadata["aws_secret_access_key"],
            "token": metadata.get("aws_session_token"),
            "expiry_time": metadata["expiration"],
        }
    else:
        seed = _refresh()

    creds = RefreshableCredentials.create_from_metadata(
        metadata=seed, refresh_using=_refresh, method="sts-assume-role",
    )
    botocore_session = _get_botocore_session()
    botocore_session._credentials = creds
    botocore_session.set_config_variable("region", region)
    return boto3.Session(botocore_session=botocore_session)
