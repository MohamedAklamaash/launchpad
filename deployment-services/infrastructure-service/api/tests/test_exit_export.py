"""F6 exit export: the owner-only authz ladder, re-authentication, the two throttles, the
audit trail, and the credential scan the plan requires — a seeded secret in every content
type this archive can carry (ECS task-def env, k8s Secret.data, k8s Deployment env, the
buildspec's GitHub token, and anything from a terraform output) must never appear in the
zip bytes."""
import json
import time
import uuid
import zipfile
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate
from shared.utils.jwt import JWTUser

SEEDED_TASKDEF_ENV_SECRET = "SEEDED-TASKDEF-ENV-SECRET"
SEEDED_K8S_SECRET_VALUE = "SEEDED-K8S-SECRET-DATA"
SEEDED_K8S_DEPLOYMENT_ENV = "SEEDED-K8S-DEPLOYMENT-ENV-SECRET"
SEEDED_BUILDSPEC_TOKEN = "SEEDED-BUILDSPEC-GITHUB-TOKEN"


@pytest.fixture(autouse=True)
def _fake_redis(monkeypatch):
    """No fakeredis dependency in this codebase — mock the bound name, matching
    test_evidence_pack.py / test_rate_budget.py's convention."""
    class _Pipe:
        def __init__(self, store):
            self.store, self.ops = store, []
        def incr(self, key):
            self.ops.append(("incr", key)); return self
        def ttl(self, key):
            self.ops.append(("ttl", key)); return self
        def execute(self):
            results = []
            for op, key in self.ops:
                if op == "incr":
                    self.store.counts[key] = self.store.counts.get(key, 0) + 1
                    results.append(self.store.counts[key])
                else:
                    results.append(self.store.expiry.get(key, -1))
            return results
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class _Redis:
        def __init__(self):
            self.counts, self.expiry = {}, {}
        def pipeline(self):
            return _Pipe(self)
        def expire(self, key, seconds):
            self.expiry[key] = seconds

    fake = _Redis()
    monkeypatch.setattr("shared.ratelimit.budget._redis", lambda: fake)
    return fake


@pytest.fixture
def factory():
    return APIRequestFactory()


@pytest.fixture
def make_user(db):
    from api.models.user import User

    def _make():
        return User.objects.create(
            id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="t", role="super_admin",
        )
    return _make


@pytest.fixture
def make_infra(db, make_user):
    from api.models.infrastructure import Infrastructure

    def _make(*, owner=None, compute_type="ecs_fargate", is_mock=True):
        owner = owner or make_user()
        infra = Infrastructure.objects.create(
            user=owner, name=f"infra-{uuid.uuid4()}", cloud_provider="aws", compute_type=compute_type,
            max_cpu=1024, max_memory=512, code="123456789012", metadata={"aws_region": "us-east-1"},
            dns_label=uuid.uuid4().hex[:16],
        )
        if is_mock:
            Infrastructure.objects.filter(id=infra.id).update(is_mock=True)
            infra.refresh_from_db()
        return owner, infra
    return _make


def _jwt_user(user, *, auth_time=None):
    payload = {"sub": str(user.id)}
    if auth_time is not None:
        payload["auth_time"] = auth_time
    return JWTUser(**payload)


def _fresh_auth_time():
    return int(time.time())


def _fake_inventory(app_id=None, compute_type="ecs_fargate", with_secrets=False):
    app_id = app_id or str(uuid.uuid4())
    task_def = {
        "family": "my-app-task",
        "containerDefinitions": [{
            "name": "my-app-task",
            "environment": [
                {"name": "DATABASE_URL", "value": SEEDED_TASKDEF_ENV_SECRET if with_secrets else "<redacted>"},
            ],
        }],
    }
    k8s_docs = [
        {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "my-app-env"},
         "stringData": {"DATABASE_URL": SEEDED_K8S_SECRET_VALUE if with_secrets else "<redacted>"}},
        {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "my-app"},
         "spec": {"template": {"spec": {"containers": [
             {"name": "my-app-app", "env": [
                 {"name": "DATABASE_URL", "value": SEEDED_K8S_DEPLOYMENT_ENV if with_secrets else "<redacted>"},
             ]},
         ]}}}},
    ]
    buildspec = (
        "version: 0.2\n"
        + ("if [ -n \"$GITHUB_TOKEN\" ]; then git clone \"https://" + SEEDED_BUILDSPEC_TOKEN + "@x\" repo; fi\n" if with_secrets else "if [ -n \"$GITHUB_TOKEN\" ]; then git clone \"https://$GITHUB_TOKEN@x\" repo; fi\n")
    )
    app = {
        "id": app_id, "name": "my-app", "slug": "my-app",
        "repo_url": "https://github.com/acme/my-app", "branch": "main", "port": 8080,
        "dockerfile_path": "Dockerfile", "build_context": "",
        "cpu": 0.5, "memory": 1, "env_keys": ["DATABASE_URL"],
        "image_tag": "my-app-abc123", "commit_sha": "abc123", "tag_source": "resolved_sha",
        "status": "ACTIVE", "auto_deploy_paused": False,
        "task_definition_arn": "arn:aws:ecs:us-east-1:123456789012:task-definition/my-app-task:3",
        "service_arn": "arn:aws:ecs:us-east-1:123456789012:service/cluster/my-app",
        "target_group_arn": "arn:aws:elasticloadbalancing:us-east-1:123456789012:targetgroup/my-app/abc",
        "listener_rule_arn": "arn:aws:elasticloadbalancing:us-east-1:123456789012:listener-rule/abc",
        "runtime_refs": {"namespace": "app-my-app", "deployment": "my-app"} if compute_type == "eks" else None,
        "has_webhook_secret": True,
        "webhook_url": f"http://localhost:8000/api/webhooks/github/{app_id}",
        "task_definition_json": None if compute_type == "eks" else json.dumps(task_def),
        "k8s_manifest_json": json.dumps(k8s_docs) if compute_type == "eks" else None,
    }
    return {
        "infrastructure_id": str(uuid.uuid4()),
        "apps": [app],
        "codebuild": {"project_name": "launchpad-build-x", "role_name": "launchpad-codebuild-role-x"},
        "buildspec": buildspec,
    }


def _get(factory, user, infra_id):
    from api.views.exit_export import exit_export

    request = factory.get(f"/api/v1/infrastructures/{infra_id}/exit-export/")
    force_authenticate(request, user=user)
    return exit_export(request, infra_id=infra_id)


def _namelist(zip_bytes: bytes) -> list[str]:
    with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
        return zf.namelist()


def _read_all(zip_bytes: bytes) -> str:
    with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
        return "\n".join(zf.read(n).decode(errors="replace") for n in zf.namelist())


# ── authz ladder ─────────────────────────────────────────────────────────────

@patch("api.services.exit_export.ExitExportService.fetch_app_inventory")
def test_owner_gets_the_archive(mock_fetch, factory, make_infra):
    owner, infra = make_infra()
    mock_fetch.return_value = _fake_inventory()

    resp = _get(factory, _jwt_user(owner, auth_time=_fresh_auth_time()), str(infra.id))

    assert resp.status_code == 200
    assert resp["Content-Type"] == "application/zip"
    assert str(infra.id) in resp["Content-Disposition"]
    names = _namelist(resp.content)
    assert "README.md" in names
    assert "REVOCATION.md" in names
    assert "terraform/main.tf" in names
    assert "terraform/backend.hcl" in names


def test_invited_user_gets_403(factory, make_infra, make_user):
    _owner, infra = make_infra()
    invited = make_user()
    infra.invited_users.add(invited)

    resp = _get(factory, _jwt_user(invited, auth_time=_fresh_auth_time()), str(infra.id))
    assert resp.status_code == 403


def test_cross_tenant_stranger_gets_404(factory, make_infra, make_user):
    _owner, infra = make_infra()
    resp = _get(factory, _jwt_user(make_user(), auth_time=_fresh_auth_time()), str(infra.id))
    assert resp.status_code == 404


def test_malformed_infra_id_gets_404_not_500(factory, make_user):
    resp = _get(factory, _jwt_user(make_user(), auth_time=_fresh_auth_time()), "not-a-uuid")
    assert resp.status_code == 404


# ── re-authentication ────────────────────────────────────────────────────────

def test_stale_auth_time_gets_401_reauth_required(factory, make_infra):
    owner, infra = make_infra()
    stale_auth_time = int(time.time()) - 900  # 15 minutes old, over the 600s default
    resp = _get(factory, _jwt_user(owner, auth_time=stale_auth_time), str(infra.id))
    assert resp.status_code == 401
    assert resp.data["code"] == "reauth_required"


def test_missing_auth_time_gets_401_reauth_required(factory, make_infra):
    owner, infra = make_infra()
    resp = _get(factory, _jwt_user(owner), str(infra.id))
    assert resp.status_code == 401
    assert resp.data["code"] == "reauth_required"


def test_fresh_iat_alone_does_not_satisfy_reauth(factory, make_infra):
    """The whole point of auth_time: a token with a brand-new `iat` (as a refreshed
    access token always has) but a stale or absent `auth_time` must still be refused."""
    owner, infra = make_infra()
    user = JWTUser(sub=str(owner.id), iat=int(time.time()))  # fresh iat, no auth_time at all
    resp = _get(factory, user, str(infra.id))
    assert resp.status_code == 401
    assert resp.data["code"] == "reauth_required"


@patch("api.services.exit_export.ExitExportService.fetch_app_inventory")
def test_fresh_auth_time_passes_reauth(mock_fetch, factory, make_infra):
    owner, infra = make_infra()
    mock_fetch.return_value = _fake_inventory()
    resp = _get(factory, _jwt_user(owner, auth_time=_fresh_auth_time()), str(infra.id))
    assert resp.status_code == 200


# ── throttling ───────────────────────────────────────────────────────────────

@patch("api.services.exit_export.ExitExportService.fetch_app_inventory")
def test_per_user_budget_returns_429(mock_fetch, factory, make_infra, settings):
    settings.RATE_BUDGET_EXIT_EXPORT_LIMIT = 1
    mock_fetch.return_value = _fake_inventory()
    owner, infra = make_infra()

    first = _get(factory, _jwt_user(owner, auth_time=_fresh_auth_time()), str(infra.id))
    assert first.status_code == 200

    _owner2, infra2 = make_infra(owner=owner)
    second = _get(factory, _jwt_user(owner, auth_time=_fresh_auth_time()), str(infra2.id))
    assert second.status_code == 429
    assert "Retry-After" in second


@patch("api.services.exit_export.ExitExportService.fetch_app_inventory")
def test_per_infrastructure_hourly_throttle(mock_fetch, factory, make_infra):
    mock_fetch.return_value = _fake_inventory()
    owner, infra = make_infra()

    first = _get(factory, _jwt_user(owner, auth_time=_fresh_auth_time()), str(infra.id))
    assert first.status_code == 200

    second = _get(factory, _jwt_user(owner, auth_time=_fresh_auth_time()), str(infra.id))
    assert second.status_code == 429


def test_redis_unavailable_fails_closed(factory, make_infra, monkeypatch):
    owner, infra = make_infra()

    def _boom():
        raise __import__("redis").RedisError("down")

    monkeypatch.setattr("shared.ratelimit.budget._redis", _boom)
    resp = _get(factory, _jwt_user(owner, auth_time=_fresh_auth_time()), str(infra.id))
    assert resp.status_code == 503


# ── audit ────────────────────────────────────────────────────────────────────

@patch("api.services.exit_export.ExitExportService.fetch_app_inventory")
def test_audit_row_written_on_success(mock_fetch, factory, make_infra):
    from api.models.exit_export_access import ExitExportAccess

    mock_fetch.return_value = _fake_inventory()
    owner, infra = make_infra()
    _get(factory, _jwt_user(owner, auth_time=_fresh_auth_time()), str(infra.id))

    row = ExitExportAccess.objects.get(infrastructure_id=infra.id)
    assert row.status_code == 200
    assert row.app_count == 1
    assert row.bytes > 0


def test_audit_row_written_on_denial(factory, make_infra, make_user):
    from api.models.exit_export_access import ExitExportAccess

    _owner, infra = make_infra()
    _get(factory, _jwt_user(make_user(), auth_time=_fresh_auth_time()), str(infra.id))

    row = ExitExportAccess.objects.filter(status_code=404).first()
    assert row is not None


# ── credential scan (H6) ─────────────────────────────────────────────────────

@patch("api.services.exit_export.ExitExportService.fetch_app_inventory")
def test_upstream_content_types_pass_through_only_when_already_redacted(mock_fetch, factory, make_infra):
    """application-service is the layer responsible for redacting task-def env / k8s
    Secret.data / k8s Deployment env / the buildspec token before this module ever sees
    them (see test_exit_inventory.py's own credential scan there). This confirms
    exit_export.py's assembly step is a faithful pass-through in the normal case — the
    already-redacted placeholder survives into the zip unchanged, proving nothing here
    re-expands or re-derives a value application-service withheld."""
    mock_fetch.return_value = _fake_inventory(with_secrets=False)
    owner, infra = make_infra()

    resp = _get(factory, _jwt_user(owner, auth_time=_fresh_auth_time()), str(infra.id))
    assert resp.status_code == 200
    blob = _read_all(resp.content)
    assert blob.count("<redacted>") >= 3


@patch("api.services.exit_export.ExitExportService.fetch_app_inventory")
def test_infra_services_own_secret_bearing_fields_never_reach_the_archive(mock_fetch, factory, make_infra):
    """The credential scan for this module's own responsibility boundary: seed a secret
    into every infrastructure-service model field this code touches or is adjacent to
    (Environment.logs/error_message, Infrastructure.metadata, onboarding_token_hash) and
    assert none of them appear — this module must describe ARNs and Terraform shape only,
    never provisioning-log or credential-metadata content."""
    from api.models.environment import Environment

    seeded_log = "SEEDED-ENVIRONMENT-LOGS-SECRET"
    seeded_error = "SEEDED-ENVIRONMENT-ERROR-SECRET"
    seeded_metadata = "SEEDED-INFRA-METADATA-SECRET"
    seeded_token_hash = "SEEDED-ONBOARDING-TOKEN-HASH"

    mock_fetch.return_value = _fake_inventory()
    owner, infra = make_infra()
    infra.metadata = {"aws_region": "us-east-1", "internal_note": seeded_metadata}
    infra.onboarding_token_hash = seeded_token_hash
    infra.save()
    Environment.objects.create(
        infrastructure=infra, vpc_id="vpc-1", cluster_arn="arn:aws:ecs:...:cluster/x",
        alb_arn="arn:aws:elasticloadbalancing:...:loadbalancer/x", alb_dns="x.elb.amazonaws.com",
        ecr_repository_url="123456789012.dkr.ecr.us-east-1.amazonaws.com/x",
        ecs_task_execution_role_arn="arn:aws:iam::123456789012:role/x", status="ACTIVE",
        logs=seeded_log, error_message=seeded_error,
    )

    resp = _get(factory, _jwt_user(owner, auth_time=_fresh_auth_time()), str(infra.id))
    assert resp.status_code == 200
    blob = _read_all(resp.content)

    assert seeded_log not in blob
    assert seeded_error not in blob
    assert seeded_metadata not in blob
    assert seeded_token_hash not in blob


@patch("api.services.terraform_worker.TerraformWorker._exec_tf")
@patch("api.services.exit_export.ExitExportService.fetch_app_inventory")
def test_never_calls_terraform_or_reads_remote_state(mock_fetch, mock_exec_tf, factory, make_infra):
    """The archive's terraform/main.tf is generated text, never `terraform output` or a
    read of the state object — no AWS call happens at all building this archive."""
    mock_fetch.return_value = _fake_inventory()
    owner, infra = make_infra()

    resp = _get(factory, _jwt_user(owner, auth_time=_fresh_auth_time()), str(infra.id))
    assert resp.status_code == 200
    mock_exec_tf.assert_not_called()


# ── path safety ──────────────────────────────────────────────────────────────

@patch("api.services.exit_export.ExitExportService.fetch_app_inventory")
def test_no_request_supplied_path_component_reaches_the_archive(mock_fetch, factory, make_infra):
    """A malicious app name/slug in the upstream payload (application-service already
    sanitizes this, but exit_export must not trust it either) never becomes a zip path —
    only the app's UUID does."""
    evil_id = str(uuid.uuid4())
    inventory = _fake_inventory(app_id=evil_id)
    inventory["apps"][0]["name"] = "../../etc/passwd"
    inventory["apps"][0]["slug"] = "../../etc/passwd"
    mock_fetch.return_value = inventory
    owner, infra = make_infra()

    resp = _get(factory, _jwt_user(owner, auth_time=_fresh_auth_time()), str(infra.id))
    assert resp.status_code == 200
    names = _namelist(resp.content)
    for name in names:
        assert not name.startswith("/")
        assert ".." not in name
    assert f"apps/{evil_id}/task-definition.json" in names


@patch("api.services.exit_export.ExitExportService.fetch_app_inventory")
def test_non_uuid_app_id_from_upstream_is_rejected(mock_fetch, factory, make_infra):
    inventory = _fake_inventory()
    inventory["apps"][0]["id"] = "../../evil"
    mock_fetch.return_value = inventory
    owner, infra = make_infra()

    resp = _get(factory, _jwt_user(owner, auth_time=_fresh_auth_time()), str(infra.id))
    assert resp.status_code == 500


# ── temp-file hygiene ────────────────────────────────────────────────────────

@patch("api.services.exit_export.ExitExportService.fetch_app_inventory")
def test_archive_is_built_without_ever_opening_a_temp_file(mock_fetch, factory, make_infra):
    """The whole archive is assembled in an in-memory BytesIO — never a temp file to
    leave behind on either a success or a failure path. Patching tempfile's constructors
    to explode proves nothing in the build path depends on them."""
    mock_fetch.return_value = _fake_inventory()
    owner, infra = make_infra()

    with patch("tempfile.NamedTemporaryFile", side_effect=AssertionError("must not use a temp file")), \
         patch("tempfile.mkstemp", side_effect=AssertionError("must not use a temp file")):
        resp = _get(factory, _jwt_user(owner, auth_time=_fresh_auth_time()), str(infra.id))
    assert resp.status_code == 200


@patch("api.services.exit_export.ExitExportService.fetch_app_inventory")
def test_upstream_failure_leaves_nothing_behind(mock_fetch, factory, make_infra):
    from api.services.exit_export import ExitExportUpstreamError

    mock_fetch.side_effect = ExitExportUpstreamError("boom")
    owner, infra = make_infra()

    resp = _get(factory, _jwt_user(owner, auth_time=_fresh_auth_time()), str(infra.id))
    assert resp.status_code == 502


# ── terraform bundle ─────────────────────────────────────────────────────────

@patch("api.services.exit_export.ExitExportService.fetch_app_inventory")
def test_terraform_modules_are_bundled(mock_fetch, factory, make_infra):
    mock_fetch.return_value = _fake_inventory()
    owner, infra = make_infra()

    resp = _get(factory, _jwt_user(owner, auth_time=_fresh_auth_time()), str(infra.id))
    names = _namelist(resp.content)
    module_files = [n for n in names if n.startswith("terraform/modules/")]
    assert any("vpc" in n for n in module_files)
    assert any("ecs" in n for n in module_files)


@patch("api.services.exit_export.ExitExportService.fetch_app_inventory")
def test_backend_hcl_matches_main_tf_backend_block(mock_fetch, factory, make_infra):
    mock_fetch.return_value = _fake_inventory()
    owner, infra = make_infra()

    resp = _get(factory, _jwt_user(owner, auth_time=_fresh_auth_time()), str(infra.id))
    with zipfile.ZipFile(BytesIO(resp.content)) as zf:
        main_tf = zf.read("terraform/main.tf").decode()
        backend_hcl = zf.read("terraform/backend.hcl").decode()

    assert "launchpad-tf-state-123456789012-us-east-1" in main_tf
    assert "launchpad-tf-state-123456789012-us-east-1" in backend_hcl
    assert f"infra/{infra.id}/terraform.tfstate" in backend_hcl


# ── module copy safety (R2) ──────────────────────────────────────────────────

def test_module_copy_follows_neither_symlinks_nor_non_tf_files(tmp_path, monkeypatch):
    from api.services import exit_export

    modules_dir = tmp_path / "modules"
    real_module = modules_dir / "realmod"
    real_module.mkdir(parents=True)
    (real_module / "main.tf").write_text('resource "x" "y" {}\n')
    (real_module / "secrets.tfvars").write_text("db_password = \"SEEDED-TFVARS-SECRET\"\n")

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "leaked.tf").write_text('# SEEDED-SYMLINK-SECRET\n')
    (real_module / "evil_link.tf").symlink_to(outside / "leaked.tf")

    evil_dir_target = tmp_path / "outside_dir"
    evil_dir_target.mkdir()
    (evil_dir_target / "reached.tf").write_text("# SEEDED-SYMLINKED-DIR-SECRET\n")
    (real_module / "evil_subdir").symlink_to(evil_dir_target, target_is_directory=True)

    monkeypatch.setattr(exit_export, "TF_MODULES_DIR", tmp_path)
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        exit_export._copy_terraform_modules(zf)

    names = _namelist(buf.getvalue())
    assert names == ["terraform/modules/realmod/main.tf"]
    blob = _read_all(buf.getvalue())
    assert "SEEDED-TFVARS-SECRET" not in blob
    assert "SEEDED-SYMLINK-SECRET" not in blob
    assert "SEEDED-SYMLINKED-DIR-SECRET" not in blob


# ── internal call hardening ──────────────────────────────────────────────────

def _fake_response(*, status_code=200, json_data=None, content=b"{}"):
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = content if json_data is None else json.dumps(json_data).encode()
    resp.json.return_value = json_data or {}
    return resp


def test_fetch_app_inventory_forbids_redirects(make_infra):
    from api.services.exit_export import ExitExportService

    _owner, infra = make_infra()
    service = ExitExportService()
    service._app_client.get = MagicMock(return_value=_fake_response(json_data=_fake_inventory()))

    service.fetch_app_inventory(infra, "Bearer x")

    _args, kwargs = service._app_client.get.call_args
    assert kwargs["allow_redirects"] is False


def test_fetch_app_inventory_clears_cookies_after_every_call(make_infra):
    from api.services.exit_export import ExitExportService

    _owner, infra = make_infra()
    service = ExitExportService()
    service._app_client.get = MagicMock(return_value=_fake_response(json_data=_fake_inventory()))
    service._app_client.session.cookies.set("sessionid", "leaked-value")

    service.fetch_app_inventory(infra, "Bearer x")

    assert len(service._app_client.session.cookies) == 0


def test_fetch_app_inventory_rejects_an_oversized_response(make_infra):
    from api.services.exit_export import ExitExportService, ExitExportUpstreamError

    _owner, infra = make_infra()
    service = ExitExportService()
    huge = b"x" * (10 * 1024 * 1024 + 1)
    service._app_client.get = MagicMock(return_value=_fake_response(content=huge))

    with pytest.raises(ExitExportUpstreamError):
        service.fetch_app_inventory(infra, "Bearer x")
