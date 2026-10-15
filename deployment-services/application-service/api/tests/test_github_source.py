"""F7: source fetching for platform-side builds (aws/github_source.py).

Not yet wired into deploys — these tests exercise the module directly: owner/repo
parsing, SHA resolution (branch and explicit ref), that a private repo's token only ever
travels in the Authorization header (never the URL, a log line, or an exception
message), size-capped tarball streaming, non-200 handling, SHA validation, S3 upload
key shape/credential source, and the MODE=dev mock path.
"""
import tarfile
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests
from aws import github_source
from aws.github_source import (
    SourceFetchError,
    docker_config_object_key,
    fetch_source_tarball,
    parse_owner_repo,
    resolve_commit_sha,
    source_object_key,
    upload_source_for_build,
)

VALID_SHA = "a" * 40
SECRET_TOKEN = "ghp_super_secret_token"


@pytest.fixture(autouse=True)
def prod_mode(monkeypatch):
    """Default every test to the real (non-mock) path; test_dev_mode_* overrides."""
    monkeypatch.setattr(
        github_source,
        "app_config",
        SimpleNamespace(mode="prod", aws_access_key_id="AKIA_TEST", aws_secret_access_key="secret"),
    )


@pytest.fixture
def dev_mode(monkeypatch):
    monkeypatch.setattr(
        github_source,
        "app_config",
        SimpleNamespace(mode="dev", aws_access_key_id="x", aws_secret_access_key="y"),
    )


@pytest.fixture
def app_row(schema_db):
    from api.models.application import Application
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    u = User.objects.create(
        id=uuid.uuid4(),
        email=f"u-{uuid.uuid4()}@e.io",
        user_name="u",
        metadata={"github": {"token": SECRET_TOKEN}},
    )
    infra = Infrastructure.objects.create(
        user=u, name="i", cloud_provider="aws", max_cpu=1, max_memory=1, code="123456789012"
    )
    app = Application.objects.create(
        name="a",
        user=u,
        infrastructure=infra,
        project_remote_url="https://github.com/acme/widgets",
        project_branch="main",
        project_commit_hash="",
    )
    return app


@pytest.fixture
def public_app_row(schema_db):
    """Same shape as app_row but the owner has no GitHub token at all."""
    from api.models.application import Application
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    u = User.objects.create(id=uuid.uuid4(), email=f"u2-{uuid.uuid4()}@e.io", user_name="u2")
    infra = Infrastructure.objects.create(
        user=u, name="i2", cloud_provider="aws", max_cpu=1, max_memory=1, code="123456789012"
    )
    app = Application.objects.create(
        name="a2",
        user=u,
        infrastructure=infra,
        project_remote_url="https://github.com/acme/public",
        project_branch="main",
        project_commit_hash="",
    )
    return app


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None, chunks=None):
        self.status_code = status_code
        self._json = json_data or {}
        self._chunks = chunks or []
        self.closed = False

    def json(self):
        return self._json

    def iter_content(self, chunk_size=1):
        yield from self._chunks

    def close(self):
        self.closed = True


class _FakeGithubClient:
    def __init__(self, response=None, raise_exc=None):
        self._response = response
        self._raise_exc = raise_exc
        self.calls = []

    def get(self, path, **kwargs):
        self.calls.append((path, kwargs))
        if self._raise_exc:
            raise self._raise_exc
        return self._response


# ── owner/repo parsing ───────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/acme/widgets", ("acme", "widgets")),
        ("https://github.com/acme/widgets.git", ("acme", "widgets")),
        ("https://github.com/acme/widgets/", ("acme", "widgets")),
        ("http://github.com/acme/widgets.git", ("acme", "widgets")),
        ("github.com/acme/widgets", ("acme", "widgets")),
        ("git@github.com:acme/widgets.git", ("acme", "widgets")),
        ("git@github.com:acme/widgets", ("acme", "widgets")),
        ("ssh://git@github.com/acme/widgets.git", ("acme", "widgets")),
    ],
)
def test_parse_owner_repo_variants(url, expected):
    assert parse_owner_repo(url) == expected


def test_parse_owner_repo_rejects_unparseable_url():
    with pytest.raises(SourceFetchError):
        parse_owner_repo("not a url")


def test_parse_owner_repo_rejects_empty():
    with pytest.raises(SourceFetchError):
        parse_owner_repo("")


# ── resolve_commit_sha ───────────────────────────────────────────────────────

def test_resolve_commit_sha_for_branch(app_row, monkeypatch):
    fake = _FakeGithubClient(_FakeResponse(200, {"sha": VALID_SHA}))
    monkeypatch.setattr(github_source, "_github_client", lambda: fake)

    sha = resolve_commit_sha(app_row)

    assert sha == VALID_SHA
    path, kwargs = fake.calls[0]
    assert path == "/repos/acme/widgets/commits/main"
    assert kwargs["headers"]["Authorization"] == f"token {SECRET_TOKEN}"
    assert kwargs["verify"] is True


def test_resolve_commit_sha_for_explicit_ref(app_row, monkeypatch):
    fake = _FakeGithubClient(_FakeResponse(200, {"sha": VALID_SHA}))
    monkeypatch.setattr(github_source, "_github_client", lambda: fake)

    sha = resolve_commit_sha(app_row, ref="deadbeef")

    assert sha == VALID_SHA
    path, _ = fake.calls[0]
    assert path == "/repos/acme/widgets/commits/deadbeef"


def test_private_repo_token_travels_in_header_never_in_url(app_row, monkeypatch):
    fake = _FakeGithubClient(_FakeResponse(200, {"sha": VALID_SHA}))
    monkeypatch.setattr(github_source, "_github_client", lambda: fake)

    resolve_commit_sha(app_row)

    path, kwargs = fake.calls[0]
    assert SECRET_TOKEN not in path
    assert kwargs["headers"]["Authorization"] == f"token {SECRET_TOKEN}"


def test_public_repo_without_token_sends_no_auth_header(public_app_row, monkeypatch):
    fake = _FakeGithubClient(_FakeResponse(200, {"sha": VALID_SHA}))
    monkeypatch.setattr(github_source, "_github_client", lambda: fake)

    resolve_commit_sha(public_app_row)

    _, kwargs = fake.calls[0]
    assert "Authorization" not in kwargs["headers"]


def test_resolve_commit_sha_404_is_clear_access_error(app_row, monkeypatch):
    fake = _FakeGithubClient(_FakeResponse(404))
    monkeypatch.setattr(github_source, "_github_client", lambda: fake)

    with pytest.raises(SourceFetchError, match="not found or no access"):
        resolve_commit_sha(app_row)


def test_resolve_commit_sha_non_200_non_404_raises(app_row, monkeypatch):
    """A fake client that returns (rather than raises) a 5xx-shaped response — exercises
    the generic status-code branch. See test_real_http_client_5xx_reports_status_not_
    network_error for how a real 5xx actually arrives (as an HTTPError, per
    ResilientHttpClient._execute)."""
    fake = _FakeGithubClient(_FakeResponse(500))
    monkeypatch.setattr(github_source, "_github_client", lambda: fake)

    with pytest.raises(SourceFetchError):
        resolve_commit_sha(app_row)


class _RealFlowResponse:
    """Stands in for requests.Response against the REAL ResilientHttpClient (not the
    _FakeGithubClient double) — proves headers/URL/status actually flow through
    ResilientHttpClient._execute's raise_for_status()/HTTPError path, not just this
    module's own fake."""

    def __init__(self, status_code, json_data=None):
        self.status_code = status_code
        self._json = json_data or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code} error", response=self)

    def json(self):
        return self._json

    def close(self):
        pass


def test_real_http_client_5xx_reports_status_not_network_error(app_row, monkeypatch, caplog):
    """Against the real ResilientHttpClient, a 5xx is raised as an HTTPError (see
    ResilientHttpClient._execute) — this must surface as "status 500", not the generic
    "could not reach GitHub" message a plain network error gets."""
    captured = {}

    def fake_request(self, method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = kwargs.get("headers")
        return _RealFlowResponse(500)

    monkeypatch.setattr(requests.Session, "request", fake_request)

    with caplog.at_level("DEBUG"), pytest.raises(SourceFetchError, match="status 500"):
        resolve_commit_sha(app_row)

    assert captured["url"] == "https://api.github.com/repos/acme/widgets/commits/main"
    assert captured["headers"]["Authorization"] == f"token {SECRET_TOKEN}"
    assert SECRET_TOKEN not in captured["url"]
    assert SECRET_TOKEN not in caplog.text


def test_resolve_commit_sha_rejects_invalid_sha_from_github(app_row, monkeypatch):
    fake = _FakeGithubClient(_FakeResponse(200, {"sha": "not-a-sha"}))
    monkeypatch.setattr(github_source, "_github_client", lambda: fake)

    with pytest.raises(SourceFetchError):
        resolve_commit_sha(app_row)


def test_network_failure_never_leaks_token_in_error(app_row, monkeypatch):
    fake = _FakeGithubClient(raise_exc=requests.exceptions.ConnectionError("boom"))
    monkeypatch.setattr(github_source, "_github_client", lambda: fake)

    with pytest.raises(SourceFetchError) as exc_info:
        resolve_commit_sha(app_row)

    assert SECRET_TOKEN not in str(exc_info.value)


def test_token_never_appears_in_logs(app_row, monkeypatch, caplog):
    fake = _FakeGithubClient(_FakeResponse(200, {"sha": VALID_SHA}))
    monkeypatch.setattr(github_source, "_github_client", lambda: fake)

    with caplog.at_level("DEBUG"):
        resolve_commit_sha(app_row)

    assert SECRET_TOKEN not in caplog.text


# ── fetch_source_tarball ─────────────────────────────────────────────────────

def test_fetch_source_tarball_downloads_and_streams_to_disk(app_row, monkeypatch, tmp_path):
    fake = _FakeGithubClient(_FakeResponse(200, chunks=[b"hello ", b"world"]))
    monkeypatch.setattr(github_source, "_github_client", lambda: fake)
    dest = tmp_path / "src.tar.gz"

    result = fetch_source_tarball(app_row, VALID_SHA, dest)

    assert result == str(dest)
    assert dest.read_bytes() == b"hello world"
    path, kwargs = fake.calls[0]
    assert path == f"/repos/acme/widgets/tarball/{VALID_SHA}"
    assert kwargs["stream"] is True
    assert kwargs["verify"] is True
    assert SECRET_TOKEN not in path


def test_fetch_source_tarball_rejects_invalid_sha_before_any_call(app_row, monkeypatch, tmp_path):
    fake = _FakeGithubClient(_FakeResponse(200))
    monkeypatch.setattr(github_source, "_github_client", lambda: fake)

    with pytest.raises(SourceFetchError):
        fetch_source_tarball(app_row, "not-a-sha", tmp_path / "out.tar.gz")

    assert fake.calls == []


def test_fetch_source_tarball_enforces_size_cap(app_row, monkeypatch, tmp_path):
    monkeypatch.setenv("PLATFORM_BUILDER_SOURCE_MAX_BYTES", "5")
    fake = _FakeGithubClient(_FakeResponse(200, chunks=[b"0123456789"]))
    monkeypatch.setattr(github_source, "_github_client", lambda: fake)
    dest = tmp_path / "src.tar.gz"

    with pytest.raises(SourceFetchError, match="exceeded"):
        fetch_source_tarball(app_row, VALID_SHA, dest)

    assert not dest.exists()


def test_fetch_source_tarball_404_is_clear_access_error(app_row, monkeypatch, tmp_path):
    fake = _FakeGithubClient(_FakeResponse(404))
    monkeypatch.setattr(github_source, "_github_client", lambda: fake)

    with pytest.raises(SourceFetchError, match="not found or no access"):
        fetch_source_tarball(app_row, VALID_SHA, tmp_path / "out.tar.gz")


def test_fetch_source_tarball_non_200_raises(app_row, monkeypatch, tmp_path):
    fake = _FakeGithubClient(_FakeResponse(403))
    monkeypatch.setattr(github_source, "_github_client", lambda: fake)

    with pytest.raises(SourceFetchError):
        fetch_source_tarball(app_row, VALID_SHA, tmp_path / "out.tar.gz")


class _InterruptedResponse(_FakeResponse):
    """A connection that drops mid-stream, after the 200 has already arrived."""

    def iter_content(self, chunk_size=1):
        yield b"partial-bytes"
        raise requests.exceptions.ConnectionError("dropped")


def test_fetch_source_tarball_mid_stream_failure_is_wrapped_and_cleaned_up(app_row, monkeypatch, tmp_path):
    fake = _FakeGithubClient(_InterruptedResponse(200))
    monkeypatch.setattr(github_source, "_github_client", lambda: fake)
    dest = tmp_path / "src.tar.gz"

    with pytest.raises(SourceFetchError):
        fetch_source_tarball(app_row, VALID_SHA, dest)

    assert not dest.exists()


# ── MODE=dev mock path ───────────────────────────────────────────────────────

def test_dev_mode_resolve_commit_sha_is_deterministic_without_http(app_row, dev_mode, monkeypatch):
    fake = _FakeGithubClient(_FakeResponse(200, {"sha": VALID_SHA}))
    monkeypatch.setattr(github_source, "_github_client", lambda: fake)

    sha1 = resolve_commit_sha(app_row)
    sha2 = resolve_commit_sha(app_row)

    assert sha1 == sha2
    assert github_source._SHA_RE.fullmatch(sha1)
    assert fake.calls == []


def test_dev_mode_fetch_source_tarball_writes_valid_tar_without_http(app_row, dev_mode, monkeypatch, tmp_path):
    fake = _FakeGithubClient(_FakeResponse(200))
    monkeypatch.setattr(github_source, "_github_client", lambda: fake)
    dest = tmp_path / "mock.tar.gz"

    result = fetch_source_tarball(app_row, VALID_SHA, dest)

    assert result == str(dest)
    assert fake.calls == []
    with tarfile.open(dest, "r:gz") as tar:
        names = tar.getnames()
    assert names
    assert not any(n == ".git" or n.startswith(".git/") or "/.git/" in n for n in names)


# ── upload_source_for_build ──────────────────────────────────────────────────

def test_upload_source_for_build_key_shape_and_injected_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_BUILDER_SOURCE_BUCKET", "lp-builder-sources")
    tarball = tmp_path / "t.tar.gz"
    tarball.write_bytes(b"data")
    fake_s3 = MagicMock()

    uri = upload_source_for_build(tarball, "build-123", s3_client=fake_s3)

    assert uri == "s3://lp-builder-sources/sources/build-123/source.tar.gz"
    fake_s3.upload_file.assert_called_once_with(
        str(tarball), "lp-builder-sources", "sources/build-123/source.tar.gz"
    )


def test_upload_source_for_build_uses_platform_client_when_none_injected(tmp_path, monkeypatch):
    """No s3_client passed -> the module must build its own PLATFORM-credentialed
    client, never reach for a customer AssumeRole session (which this module never even
    imports)."""
    monkeypatch.setenv("PLATFORM_BUILDER_SOURCE_BUCKET", "lp-builder-sources")
    tarball = tmp_path / "t.tar.gz"
    tarball.write_bytes(b"data")
    fake_s3 = MagicMock()
    monkeypatch.setattr(github_source, "_build_platform_s3_client", lambda: fake_s3)

    upload_source_for_build(tarball, "build-456")

    fake_s3.upload_file.assert_called_once()


def test_upload_source_for_build_rejects_unconfigured_bucket(tmp_path, monkeypatch):
    monkeypatch.delenv("PLATFORM_BUILDER_SOURCE_BUCKET", raising=False)

    with pytest.raises(SourceFetchError):
        upload_source_for_build(tmp_path / "t.tar.gz", "build-1", s3_client=MagicMock())


def test_upload_source_for_build_rejects_path_traversal_build_id(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_BUILDER_SOURCE_BUCKET", "lp-builder-sources")

    with pytest.raises(SourceFetchError):
        upload_source_for_build(tmp_path / "t.tar.gz", "../../etc/passwd", s3_client=MagicMock())


# ── per-build key shape ──────────────────────────────────────────────────────

def test_source_and_docker_config_keys_share_build_id_prefix():
    assert source_object_key("build-1") == "sources/build-1/source.tar.gz"
    assert docker_config_object_key("build-1") == "sources/build-1/docker-config.json"


def test_source_prefix_is_configurable_and_shared_by_both_keys(monkeypatch):
    monkeypatch.setenv("PLATFORM_BUILDER_SOURCE_PREFIX", "custom-prefix")

    assert source_object_key("build-1") == "custom-prefix/build-1/source.tar.gz"
    assert docker_config_object_key("build-1") == "custom-prefix/build-1/docker-config.json"


def test_source_object_key_rejects_path_traversal_build_id():
    with pytest.raises(SourceFetchError):
        source_object_key("../../etc/passwd")


def test_upload_source_for_build_mock_mode_skips_s3(tmp_path, dev_mode, monkeypatch):
    monkeypatch.setenv("PLATFORM_BUILDER_SOURCE_BUCKET", "lp-builder-sources")
    fake_s3 = MagicMock()

    uri = upload_source_for_build(tmp_path / "t.tar.gz", "build-789", s3_client=fake_s3)

    assert uri == "s3://lp-builder-sources/sources/build-789/source.tar.gz"
    fake_s3.upload_file.assert_not_called()
