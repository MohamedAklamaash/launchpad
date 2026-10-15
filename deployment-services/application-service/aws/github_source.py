"""Source fetching for platform-side builds (F7 ticket 4).

Resolves a branch/ref to a commit SHA and fetches the corresponding GitHub source
tarball, then uploads it to the platform builder's own S3 source bucket for Kaniko to
read as its `--context`. Not yet wired into `application_deployment_service.py` — see
plan/F7-platform-builder.md ("Source checkout") for the design this implements and
ticket 6 for where it gets called.

Security invariants, matching the design doc:
- The GitHub token travels only in an Authorization header, never in a URL, a `.git`
  directory, a log line, or an exception message raised from this module.
- TLS verification is never disabled (the design doc calls out busybox `wget`'s lack of
  TLS validation as exactly the bug this module exists to avoid).
- A commit SHA is validated as 40 lowercase hex characters before it is used in any URL
  or S3 key.
- `upload_source_for_build` always uses the platform's own AWS credentials
  (`app_config.aws_access_key_id`/`aws_secret_access_key` — the same platform IAM user
  `aws/session.py` uses to AssumeRole into customer accounts), never a customer's
  AssumeRole session.
"""
import hashlib
import io
import logging
import os
import re
import tarfile
from pathlib import Path
from urllib.parse import urlparse

import boto3
import requests
from api.common.envs.application import app_config
from shared.mode import is_dev_mode
from shared.resilience.http_client import ResilientHttpClient

from aws.session import get_boto3_config

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"
DEFAULT_MAX_TARBALL_BYTES = 500 * 1024 * 1024

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_OWNER_REPO_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_BUILD_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_SCP_LIKE_RE = re.compile(r"^git@[^:/]+:(.+)$")


class SourceFetchError(Exception):
    """Raised for any resolve/fetch/upload failure in this module. Messages are always
    built from fixed text plus non-secret fields (owner, repo, ref, sha, status code) —
    never from str() of an underlying exception, which is how a token could otherwise
    leak into something a caller logs or displays."""


def _github_client() -> ResilientHttpClient:
    return ResilientHttpClient(name="GitHubSourceClient", base_url=GITHUB_API_BASE)


def _github_api_timeout() -> float:
    return float(os.environ.get("GITHUB_SOURCE_API_TIMEOUT_SECONDS", "15"))


def _tarball_timeout() -> tuple[float, float]:
    connect = float(os.environ.get("GITHUB_SOURCE_TARBALL_CONNECT_TIMEOUT_SECONDS", "10"))
    read = float(os.environ.get("GITHUB_SOURCE_TARBALL_READ_TIMEOUT_SECONDS", "30"))
    return (connect, read)


def _max_tarball_bytes() -> int:
    return int(os.environ.get("PLATFORM_BUILDER_SOURCE_MAX_BYTES", str(DEFAULT_MAX_TARBALL_BYTES)))


def parse_owner_repo(remote_url: str) -> tuple[str, str]:
    """Parses owner/repo out of https, ssh (scp-like or ssh://), or scheme-less GitHub
    remote URLs, with or without a trailing `.git`/slash. GitHub's REST API is
    case-insensitive about owner/repo (unlike codeload.github.com), so case is kept
    exactly as given rather than normalized — api.github.com is used for every call in
    this module, never codeload, so that distinction never matters here."""
    if not remote_url or not remote_url.strip():
        raise SourceFetchError("Application has no project_remote_url configured")

    url = remote_url.strip()
    scp_match = _SCP_LIKE_RE.match(url)
    if scp_match:
        path = scp_match.group(1)
    else:
        normalized = url if "://" in url else f"https://{url}"
        path = urlparse(normalized).path

    path = path.strip("/").removesuffix(".git")

    parts = [p for p in path.split("/") if p]
    if len(parts) < 2:
        raise SourceFetchError("Could not parse a GitHub owner/repo from the configured remote URL")

    owner, repo = parts[-2], parts[-1]
    if not (_OWNER_REPO_SEGMENT_RE.match(owner) and _OWNER_REPO_SEGMENT_RE.match(repo)):
        raise SourceFetchError("Could not parse a GitHub owner/repo from the configured remote URL")
    return owner, repo


def get_github_token_for_application(application) -> str | None:
    """Mirrors the lookup `application_deployment_service.py::_trigger_build` already
    does (`application.user.metadata['github']['token']`) — duplicated here rather than
    imported to avoid a circular import between this module and the deploy service;
    ticket 6 (wiring) is the natural place to fold both call sites onto one helper."""
    try:
        user = application.user
    except Exception as e:
        logger.warning(f"Could not load application owner for GitHub token lookup: {e}")
        return None

    metadata = getattr(user, "metadata", None)
    if metadata and "github" in metadata:
        return metadata["github"].get("token")
    return None


def _auth_headers(token: str | None) -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _validate_sha(sha: str) -> None:
    if not isinstance(sha, str) or not _SHA_RE.fullmatch(sha):
        raise SourceFetchError("Invalid commit SHA: must be exactly 40 lowercase hex characters")


def _mock_sha(application, ref: str | None) -> str:
    """Deterministic fake SHA for MODE=dev — never makes an HTTP call. Derived from the
    full application id (never a slice, per CLAUDE.md) plus the requested ref, so the
    same app+ref always resolves to the same fake commit."""
    seed = f"{application.id}:{ref or getattr(application, 'project_branch', '') or ''}"
    return hashlib.sha256(seed.encode()).hexdigest()[:40]


def resolve_commit_sha(application, ref: str | None = None) -> str:
    """Resolves `ref` (or, if omitted, `application.project_branch`) to a full 40-hex
    commit SHA via `GET /repos/{owner}/{repo}/commits/{ref}`. Uses the app owner's
    GitHub token (if any) so private repos resolve the same way a public one does."""
    if is_dev_mode(app_config.mode):
        return _mock_sha(application, ref)

    owner, repo = parse_owner_repo(application.project_remote_url)
    target_ref = ref or getattr(application, "project_branch", None)
    if not target_ref:
        raise SourceFetchError("No ref or branch configured to resolve a commit SHA")

    token = get_github_token_for_application(application)

    try:
        response = _github_client().get(
            f"/repos/{owner}/{repo}/commits/{target_ref}",
            headers=_auth_headers(token),
            timeout=_github_api_timeout(),
            verify=True,
        )
    except requests.exceptions.RequestException:
        raise SourceFetchError(f"Could not reach GitHub to resolve {owner}/{repo}@{target_ref}") from None

    if response.status_code == 404:
        raise SourceFetchError(f"Repository {owner}/{repo} not found or no access")
    if response.status_code != 200:
        raise SourceFetchError(
            f"GitHub API returned status {response.status_code} resolving {owner}/{repo}@{target_ref}"
        )

    sha = (response.json() or {}).get("sha") or ""
    if not _SHA_RE.fullmatch(sha):
        raise SourceFetchError(f"GitHub API did not return a valid commit SHA for {owner}/{repo}@{target_ref}")
    return sha


def _write_mock_tarball(dest: Path, application, sha: str) -> str:
    """Deterministic fake tarball for MODE=dev — never makes an HTTP call. Still a real
    gzip tar so callers that extract it behave the same as against a real download."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    content = f"mock source for application {application.id} @ {sha}\n".encode()
    with tarfile.open(dest, "w:gz") as tar:
        info = tarfile.TarInfo(name="mock-source/README.mock")
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))
    return str(dest)


def fetch_source_tarball(application, sha: str, dest_path) -> str:
    """Downloads `GET /repos/{owner}/{repo}/tarball/{sha}` to `dest_path`, streaming to
    disk with a size cap (`PLATFORM_BUILDER_SOURCE_MAX_BYTES`, default 500 MB). TLS
    verification is always on; the token (if any) travels only in the Authorization
    header, never the URL — so nothing written to `dest_path` or logged can contain it.
    Returns `dest_path` (as str) on success; partial downloads that exceed the cap are
    removed before raising."""
    _validate_sha(sha)
    dest = Path(dest_path)

    if is_dev_mode(app_config.mode):
        return _write_mock_tarball(dest, application, sha)

    owner, repo = parse_owner_repo(application.project_remote_url)
    token = get_github_token_for_application(application)

    try:
        response = _github_client().get(
            f"/repos/{owner}/{repo}/tarball/{sha}",
            headers=_auth_headers(token),
            stream=True,
            timeout=_tarball_timeout(),
            verify=True,
            allow_redirects=True,
        )
    except requests.exceptions.RequestException:
        raise SourceFetchError(f"Could not reach GitHub to download {owner}/{repo}@{sha}") from None

    if response.status_code == 404:
        response.close()
        raise SourceFetchError(f"Repository {owner}/{repo} not found or no access")
    if response.status_code != 200:
        status = response.status_code
        response.close()
        raise SourceFetchError(f"GitHub tarball download for {owner}/{repo}@{sha} failed (status {status})")

    max_bytes = _max_tarball_bytes()
    dest.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    try:
        with open(dest, "wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                written += len(chunk)
                if written > max_bytes:
                    raise SourceFetchError(
                        f"Source tarball for {owner}/{repo}@{sha} exceeded the {max_bytes}-byte cap"
                    )
                f.write(chunk)
    except SourceFetchError:
        dest.unlink(missing_ok=True)
        raise
    finally:
        response.close()

    return str(dest)


def _validate_build_id(build_id) -> str:
    safe = str(build_id).strip()
    if not _BUILD_ID_RE.fullmatch(safe):
        raise SourceFetchError("Invalid build id for source upload key")
    return safe


def _source_bucket() -> str:
    bucket = os.environ.get("PLATFORM_BUILDER_SOURCE_BUCKET", "").strip()
    if not bucket:
        raise SourceFetchError("PLATFORM_BUILDER_SOURCE_BUCKET is not configured")
    return bucket


def _build_platform_s3_client():
    """Platform-account S3 client built from the platform's own IAM user keys — the same
    credential `aws/session.py::_assume_role_raw` uses to call `sts.assume_role` into
    customer accounts, used here directly with no AssumeRole. Explicit keys only, never
    boto3's default credential chain, mirroring infrastructure-service's
    `platform_dns/route53_client.py::build_real_route53_client`: this process must use
    exactly the one platform identity it was given."""
    return boto3.client(
        "s3",
        region_name=os.environ.get("PLATFORM_BUILDER_AWS_REGION", "us-west-2"),
        aws_access_key_id=app_config.aws_access_key_id,
        aws_secret_access_key=app_config.aws_secret_access_key,
        config=get_boto3_config(),
    )


def upload_source_for_build(tarball_path, build_id, s3_client=None) -> str:
    """Uploads the tarball at `tarball_path` to the platform builder source bucket at
    `sources/{build_id}.tar.gz`, returning the `s3://` URI Kaniko reads as `--context`.
    Always uses the platform's own AWS credentials, never a customer's AssumeRole
    session. `s3_client` is accepted for dependency injection in tests; real callers
    omit it."""
    safe_build_id = _validate_build_id(build_id)
    bucket = _source_bucket()
    key = f"sources/{safe_build_id}.tar.gz"
    s3_uri = f"s3://{bucket}/{key}"

    if is_dev_mode(app_config.mode):
        logger.info(f"MOCK source upload for build {safe_build_id} (no S3 call)")
        return s3_uri

    client = s3_client or _build_platform_s3_client()
    client.upload_file(str(tarball_path), bucket, key)
    return s3_uri
