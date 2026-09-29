"""Nuke infrastructure: typed-name confirmation, ownership, 409 on an in-flight run,
step ordering/idempotent resume, the last-infra-vs-shared decision for the deployment
role/state backend, database snapshot cleanup, verify->leftovers->failed, AccessDenied
mapping to policy_refresh_required, and mock mode."""
import uuid
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError
from rest_framework.test import APIRequestFactory, force_authenticate


@pytest.fixture(autouse=True)
def _stub_infra_queue(monkeypatch):
    fake = MagicMock()
    monkeypatch.setattr("api.services.nuke_service.InfraQueue", fake)
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
def make_infra_env(db, make_user):
    from api.models.environment import Environment
    from api.models.infrastructure import Infrastructure

    def _make(*, owner=None, env_status="ACTIVE", is_mock=False, name=None):
        owner = owner or make_user()
        infra = Infrastructure.objects.create(
            user=owner, name=name or f"infra-{uuid.uuid4()}", cloud_provider="aws",
            max_cpu=1024, max_memory=512, code="123456789012",
            metadata={"aws_region": "us-east-1"}, is_mock=is_mock, is_cloud_authenticated=True,
        )
        env = Environment.objects.create(infrastructure=infra, status=env_status, vpc_id="vpc-abc123")
        return owner, infra, env
    return _make


def _start(factory, user, infra_id, confirm_name):
    from api.views.nuke import infrastructure_nuke

    request = factory.post(f"/api/v1/infrastructures/{infra_id}/nuke/", {"confirm_name": confirm_name}, format="json")
    force_authenticate(request, user=user)
    return infrastructure_nuke(request, infra_id=infra_id)


def _status(factory, user, infra_id):
    from api.views.nuke import infrastructure_nuke

    request = factory.get(f"/api/v1/infrastructures/{infra_id}/nuke/")
    force_authenticate(request, user=user)
    return infrastructure_nuke(request, infra_id=infra_id)


# ── start_nuke: typed-name, ownership, conflicts ────────────────────────────────

def test_start_rejects_mismatched_confirm_name(factory, make_infra_env):
    owner, infra, _env = make_infra_env(name="prod-infra")
    resp = _start(factory, owner, str(infra.id), "wrong-name")
    assert resp.status_code == 409
    assert "confirm_name" in resp.data["error"]


def test_start_rejects_invited_non_owner(factory, make_infra_env, make_user):
    """An invited (view-only) member can see the infra (so get_by_id resolves it) but
    isn't its owner — 403, not 404. A wholly unrelated user gets 404 instead (repo
    scoping never resolves the row), matching the existing DELETE endpoint's behavior."""
    _owner, infra, _env = make_infra_env(name="prod-infra")
    other = make_user()
    infra.invited_users.add(other)
    resp = _start(factory, other, str(infra.id), "prod-infra")
    assert resp.status_code == 403


def test_start_returns_404_for_an_unrelated_user(factory, make_infra_env, make_user):
    _owner, infra, _env = make_infra_env(name="prod-infra")
    other = make_user()
    resp = _start(factory, other, str(infra.id), "prod-infra")
    assert resp.status_code == 404


def test_start_succeeds_for_owner_with_matching_name(factory, make_infra_env):
    owner, infra, _env = make_infra_env(name="prod-infra")
    resp = _start(factory, owner, str(infra.id), "prod-infra")
    assert resp.status_code == 202
    assert resp.data["status"] == "PENDING"
    assert [s["key"] for s in resp.data["steps"]] == [
        "apps", "databases", "terraform_teardown", "leftovers", "state_backend", "verify", "deployment_role",
    ]


def test_start_returns_404_for_unknown_infra(factory, make_user):
    resp = _start(factory, make_user(), str(uuid.uuid4()), "anything")
    assert resp.status_code == 404


def test_start_409s_while_an_operation_is_in_flight(factory, make_infra_env):
    owner, infra, _env = make_infra_env(name="prod-infra", env_status="PROVISIONING")
    resp = _start(factory, owner, str(infra.id), "prod-infra")
    assert resp.status_code == 409


def test_start_409s_when_already_running(factory, make_infra_env):
    from api.models.nuke_run import NukeRun, initial_steps

    owner, infra, _env = make_infra_env(name="prod-infra")
    NukeRun.objects.create(
        infrastructure_id=infra.id, infrastructure_name=infra.name, user_id=owner.id,
        confirm_name="prod-infra", status="RUNNING", steps=initial_steps(),
    )
    resp = _start(factory, owner, str(infra.id), "prod-infra")
    assert resp.status_code == 409


def test_start_resumes_a_failed_run_instead_of_creating_a_new_one(factory, make_infra_env):
    """A real failed nuke run parks Environment at ERROR (see
    nuke_worker._park_environment_error), not DESTROYING — this reproduces that state
    directly rather than the unrealistic ACTIVE a failed run never actually leaves
    behind."""
    from api.models.nuke_run import NukeRun, initial_steps

    owner, infra, _env = make_infra_env(name="prod-infra", env_status="ERROR")
    steps = initial_steps()
    steps[0]["status"] = "success"
    steps[0]["detail"] = {"deleted_count": 2}
    run = NukeRun.objects.create(
        infrastructure_id=infra.id, infrastructure_name=infra.name, user_id=owner.id,
        confirm_name="prod-infra", status="FAILED", steps=steps, leftovers=[{"type": "x", "id": "y", "reason": "z"}],
    )
    resp = _start(factory, owner, str(infra.id), "prod-infra")
    assert resp.status_code == 202
    assert NukeRun.objects.filter(infrastructure_id=infra.id).count() == 1
    resumed = NukeRun.objects.get(id=run.id)
    assert resumed.status == "PENDING"
    assert resumed.leftovers == []

    assert resumed.steps[0]["status"] == "success"  # apps step's prior success is preserved


def test_start_rejects_exited_infrastructure(factory, make_infra_env):
    from django.utils import timezone

    owner, infra, _env = make_infra_env(name="prod-infra")
    infra.exited_at = timezone.now()
    infra.save(update_fields=["exited_at"])
    resp = _start(factory, owner, str(infra.id), "prod-infra")
    assert resp.status_code == 409


# ── status endpoint ──────────────────────────────────────────────────────────────

def test_status_answers_after_infrastructure_row_is_deleted(factory, make_infra_env):
    from api.models.nuke_run import NukeRun, initial_steps

    owner, infra, _env = make_infra_env(name="prod-infra")
    infra_id = infra.id
    NukeRun.objects.create(
        infrastructure_id=infra_id, infrastructure_name="prod-infra", user_id=owner.id,
        confirm_name="prod-infra", status="COMPLETED", steps=initial_steps(),
    )
    infra.delete()

    resp = _status(factory, owner, str(infra_id))
    assert resp.status_code == 200
    assert resp.data["status"] == "COMPLETED"


def test_status_denies_a_different_user_after_the_infra_row_is_gone(factory, make_infra_env, make_user):
    from api.models.nuke_run import NukeRun, initial_steps

    owner, infra, _env = make_infra_env(name="prod-infra")
    infra_id = infra.id
    NukeRun.objects.create(
        infrastructure_id=infra_id, infrastructure_name="prod-infra", user_id=owner.id,
        confirm_name="prod-infra", status="COMPLETED", steps=initial_steps(),
    )
    infra.delete()

    other = make_user()
    resp = _status(factory, other, str(infra_id))
    assert resp.status_code == 404


# ── worker: step ordering + idempotent resume ───────────────────────────────────

@pytest.fixture
def make_nuke_run(db, make_infra_env):
    from api.models.nuke_run import NukeRun, initial_steps

    def _make(**kwargs):
        owner, infra, env = make_infra_env(**kwargs)
        run = NukeRun.objects.create(
            infrastructure_id=infra.id, infrastructure_name=infra.name, user_id=owner.id,
            confirm_name=infra.name, status="PENDING", steps=initial_steps(),
        )
        return owner, infra, env, run
    return _make


def _patch_all_steps_success(monkeypatch, calls):
    import api.services.nuke_worker as nw

    def _record(name, retval=None):
        def _fn(ctx):
            calls.append(name)
            return retval or {}
        return _fn

    monkeypatch.setattr(nw._NukeContext, "step_apps", lambda self: _record("apps")(self))
    monkeypatch.setattr(nw._NukeContext, "step_databases", lambda self: _record("databases")(self))
    monkeypatch.setattr(nw._NukeContext, "step_terraform_teardown", lambda self: _record("terraform_teardown")(self))
    monkeypatch.setattr(nw._NukeContext, "step_leftovers", lambda self: _record("leftovers")(self))
    monkeypatch.setattr(nw._NukeContext, "step_state_backend", lambda self: _record("state_backend")(self))
    monkeypatch.setattr(nw._NukeContext, "step_verify", lambda self: _record("verify")(self))
    monkeypatch.setattr(nw._NukeContext, "step_deployment_role", lambda self: _record("deployment_role")(self))


def test_worker_runs_steps_in_order_and_completes(make_nuke_run, monkeypatch):
    from api.models.infrastructure import Infrastructure
    from api.models.nuke_run import NukeRun
    from api.services.nuke_worker import NukeWorker

    _owner, infra, _env, run = make_nuke_run(name="prod-infra")
    calls = []
    _patch_all_steps_success(monkeypatch, calls)
    with patch("api.services.nuke_worker.NotificationService"):
        NukeWorker.run(str(infra.id))

    assert calls == [
        "apps", "databases", "terraform_teardown", "leftovers", "state_backend", "verify", "deployment_role",
    ]
    run.refresh_from_db()
    assert run.status == "COMPLETED"
    assert all(s["status"] == "success" for s in run.steps)
    assert not Infrastructure.objects.filter(id=infra.id).exists()
    assert NukeRun.objects.filter(id=run.id).exists()  # survives the infra row's deletion


def test_worker_refuses_to_delete_rows_while_platform_dns_is_still_live(make_nuke_run, monkeypatch):
    """Mirrors run_worker.py's run_destroy: live platform DNS state is the point past
    which the Infrastructure row must not be deleted, or a Route53 record could be left
    pointing at nothing."""
    from api.models.infrastructure import Infrastructure
    from api.services.nuke_worker import NukeWorker

    _owner, infra, _env, run = make_nuke_run(name="prod-infra")
    calls = []
    _patch_all_steps_success(monkeypatch, calls)

    with patch("api.services.platform_dns.teardown.has_live_dns_state", return_value=True), \
         patch("api.services.nuke_worker.NotificationService") as notify:
        NukeWorker.run(str(infra.id))

    run.refresh_from_db()
    assert run.status == "FAILED"
    assert any(l["type"] == "platform_dns" for l in run.leftovers)
    assert Infrastructure.objects.filter(id=infra.id).exists()
    notify.send_destroy_failure.assert_called_once()


def test_worker_resume_skips_only_the_resumable_steps(make_nuke_run, monkeypatch):
    """apps/databases/terraform_teardown/leftovers are skipped once already "success".
    state_backend/verify/deployment_role always re-run, even if a prior attempt already
    marked them "success" — the last-infra decision and the leftover scan must reflect
    the account's current state on every attempt, not the first one."""
    from api.models.nuke_run import NukeRun
    from api.services.nuke_worker import NukeWorker

    _owner, infra, _env, run = make_nuke_run(name="prod-infra")
    run.set_step("apps", "success", {"deleted_count": 1})
    run.set_step("databases", "success", {"snapshots_deleted": []})
    run.set_step("terraform_teardown", "success", {"status": "DESTROYED"})
    run.set_step("leftovers", "success", {})
    run.set_step("state_backend", "success", {"action": "kept"})  # must still re-run
    run.save(update_fields=["steps"])

    calls = []
    _patch_all_steps_success(monkeypatch, calls)
    with patch("api.services.nuke_worker.NotificationService"):
        NukeWorker.run(str(infra.id))

    assert calls == ["state_backend", "verify", "deployment_role"]
    assert NukeRun.objects.get(id=run.id).status == "COMPLETED"


def test_worker_failed_step_stops_the_run_and_notifies_failure(make_nuke_run, monkeypatch):
    import api.services.nuke_worker as nw
    from api.services.nuke_worker import NukeWorker

    _owner, infra, env, run = make_nuke_run(name="prod-infra", env_status="DESTROYING")
    calls = []
    _patch_all_steps_success(monkeypatch, calls)

    def _boom(self):
        calls.append("databases")
        raise RuntimeError("rds is angry")
    monkeypatch.setattr(nw._NukeContext, "step_databases", _boom)

    with patch("api.services.nuke_worker.NotificationService") as notify:
        NukeWorker.run(str(infra.id))

    assert calls == ["apps", "databases"]  # never reaches terraform_teardown
    run.refresh_from_db()
    assert run.status == "FAILED"
    assert run.step("databases")["status"] == "failed"
    assert "rds is angry" in run.step("databases")["detail"]
    notify.send_destroy_failure.assert_called_once()

    env.refresh_from_db()
    assert env.status == "ERROR"  # not left at DESTROYING — see _park_environment_error


def test_worker_failure_parks_environment_error_so_a_retry_is_not_blocked(make_nuke_run, monkeypatch):
    """Regression: before _park_environment_error, a step failure left Environment at
    DESTROYING (set by start_nuke), so start_nuke's own PROVISIONING/UPDATING/DESTROYING
    guard 409'd every retry, and the reaper's DESTROYING filter would silently re-drive
    the stuck row as a plain destroy — no leftovers/state_backend/deployment_role, and it
    deletes the Infrastructure row on success. ERROR sits outside both."""
    import api.services.nuke_worker as nw
    from api.services.nuke_worker import NukeWorker

    owner, infra, _env, _run = make_nuke_run(name="prod-infra")
    monkeypatch.setattr(nw._NukeContext, "step_apps", lambda self: (_ for _ in ()).throw(RuntimeError("boom")))

    with patch("api.services.nuke_worker.NotificationService"):
        NukeWorker.run(str(infra.id))

    resp = _start(APIRequestFactory(), owner, str(infra.id), "prod-infra")
    assert resp.status_code == 202


def test_worker_leftovers_after_verify_marks_run_failed(make_nuke_run, monkeypatch):
    import api.services.nuke_worker as nw
    from api.services.nuke_worker import NukeWorker

    _owner, infra, _env, run = make_nuke_run(name="prod-infra")
    calls = []
    _patch_all_steps_success(monkeypatch, calls)

    def _verify_with_leftover(self):
        self.run.leftovers = [{"type": "ec2", "id": "sg-123", "reason": "still tagged"}]
        return {"leftovers_found": 1}
    monkeypatch.setattr(nw._NukeContext, "step_verify", _verify_with_leftover)

    with patch("api.services.nuke_worker.NotificationService"):
        NukeWorker.run(str(infra.id))

    run.refresh_from_db()
    assert run.status == "FAILED"
    assert run.leftovers == [{"type": "ec2", "id": "sg-123", "reason": "still tagged"}]
    # Real AWS: revoking access here made the retry fail with AssumeRole AccessDenied.
    # With leftovers, the role/trust entry is kept so a retry can still clean up.
    assert "deployment_role" not in calls
    assert run.step("deployment_role")["status"] == "skipped"


def test_worker_access_denied_reports_policy_refresh_required(make_nuke_run, monkeypatch):
    import api.services.nuke_worker as nw
    from api.services.nuke_worker import NukeWorker, PolicyRefreshRequiredForNuke

    _owner, infra, _env, run = make_nuke_run(name="prod-infra")
    calls = []
    _patch_all_steps_success(monkeypatch, calls)

    def _denied(self):
        calls.append("leftovers")
        raise PolicyRefreshRequiredForNuke("AWS denied codebuild:DeleteProject")
    monkeypatch.setattr(nw._NukeContext, "step_leftovers", _denied)

    with patch("api.services.nuke_worker.NotificationService"):
        NukeWorker.run(str(infra.id))

    run.refresh_from_db()
    assert run.status == "FAILED"
    assert run.step("leftovers")["status"] == "policy_refresh_required"


def test_mock_infrastructure_run_never_touches_boto3(make_nuke_run, monkeypatch):
    from api.models.infrastructure import Infrastructure
    from api.services.nuke_worker import NukeWorker

    _owner, infra, _env, run = make_nuke_run(name="prod-infra", is_mock=True)
    with patch("api.services.nuke_worker.boto3.client") as boto_client, \
         patch("api.services.nuke_worker._nuke_applications", return_value={"deleted_count": 0, "errors": [], "log_groups": []}), \
         patch("api.services.terraform_worker.TerraformWorker.destroy") as destroy, \
         patch("api.services.nuke_worker.NotificationService"):

        def _fake_destroy(infra_id, nuke=False):
            from api.models.environment import Environment
            Environment.objects.filter(infrastructure_id=infra_id).update(status="DESTROYED", logs="[MOCK] destroyed")
        destroy.side_effect = _fake_destroy

        NukeWorker.run(str(infra.id))

    boto_client.assert_not_called()
    run.refresh_from_db()
    assert not Infrastructure.objects.filter(id=infra.id).exists()


# ── databases: pre-existing snapshot cleanup ─────────────────────────────────────

def test_databases_step_deletes_pre_existing_snapshots_per_engine(make_nuke_run):
    from api.models.database import Database
    from api.services.nuke_worker import _cleanup_database_snapshots, _NukeContext

    _owner, infra, env, run = make_nuke_run(name="prod-infra")
    pg = Database.objects.create(environment=env, name="pg", engine="postgres", engine_version="16",
                                  instance_class="db.t3.micro", allocated_storage=20, status="DELETED")
    doc = Database.objects.create(environment=env, name="doc", engine="docdb", engine_version="5.0",
                                   instance_class="db.t3.medium", allocated_storage=20, status="ACTIVE")
    Database.objects.create(environment=env, name="cache", engine="redis", engine_version="7",
                             instance_class="cache.t3.micro", status="DELETED")

    ctx = _NukeContext(infra=infra, run=run)
    fake_rds = MagicMock()
    with patch.object(_NukeContext, "boto_client", return_value=fake_rds):
        result = _cleanup_database_snapshots(ctx)

    fake_rds.delete_db_snapshot.assert_called_once_with(DBSnapshotIdentifier=pg.final_snapshot_id)
    fake_rds.delete_db_cluster_snapshot.assert_called_once_with(DBClusterSnapshotIdentifier=doc.final_snapshot_id)
    assert set(result["snapshots_deleted"]) == {pg.final_snapshot_id, doc.final_snapshot_id}


def test_databases_step_ignores_already_gone_snapshots(make_nuke_run):
    from api.models.database import Database
    from api.services.nuke_worker import _cleanup_database_snapshots, _NukeContext

    _owner, infra, env, run = make_nuke_run(name="prod-infra")
    Database.objects.create(environment=env, name="pg", engine="postgres", engine_version="16",
                             instance_class="db.t3.micro", allocated_storage=20, status="DELETED")

    ctx = _NukeContext(infra=infra, run=run)
    fake_rds = MagicMock()
    fake_rds.delete_db_snapshot.side_effect = ClientError(
        {"Error": {"Code": "DBSnapshotNotFound", "Message": "gone"}}, "DeleteDBSnapshot"
    )
    with patch.object(_NukeContext, "boto_client", return_value=fake_rds):
        result = _cleanup_database_snapshots(ctx)  # must not raise

    assert result["snapshots_deleted"] == []


def test_databases_step_access_denied_raises_policy_refresh(make_nuke_run):
    from api.models.database import Database
    from api.services.nuke_worker import (
        PolicyRefreshRequiredForNuke,
        _cleanup_database_snapshots,
        _NukeContext,
    )

    _owner, infra, env, run = make_nuke_run(name="prod-infra")
    Database.objects.create(environment=env, name="pg", engine="postgres", engine_version="16",
                             instance_class="db.t3.micro", allocated_storage=20, status="ACTIVE")

    ctx = _NukeContext(infra=infra, run=run)
    fake_rds = MagicMock()
    fake_rds.delete_db_snapshot.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "nope"}}, "DeleteDBSnapshot"
    )
    with patch.object(_NukeContext, "boto_client", return_value=fake_rds), \
         pytest.raises(PolicyRefreshRequiredForNuke):
        _cleanup_database_snapshots(ctx)


# ── terraform teardown ──────────────────────────────────────────────────────────

def test_terraform_teardown_best_effort_deletes_app_sg_before_destroy(make_nuke_run):
    """Whether AWS's DeleteVpc tolerates a leftover non-default security group is
    unverified (see the PR notes) — deleting it before terraform runs removes the
    question for the case it matters."""
    from api.services.nuke_worker import _NukeContext, _run_terraform_teardown

    _owner, infra, _env, run = make_nuke_run(name="prod-infra")
    ctx = _NukeContext(infra=infra, run=run)

    with patch("api.services.nuke_worker._delete_app_security_group") as delete_sg, \
         patch("api.services.terraform_worker.TerraformWorker.destroy") as destroy:
        def _fake_destroy(infra_id, nuke=False):
            from api.models.environment import Environment
            Environment.objects.filter(infrastructure_id=infra_id).update(status="DESTROYED")
        destroy.side_effect = _fake_destroy

        _run_terraform_teardown(ctx)

    delete_sg.assert_called_once_with(ctx, str(infra.id))
    destroy.assert_called_once()


def test_terraform_teardown_survives_app_sg_delete_failure(make_nuke_run):
    """The pre-destroy SG delete is best-effort — a DependencyViolation or any other
    failure must not stop terraform from running; the leftovers step's own pass is the
    authoritative one."""
    from api.services.nuke_worker import _NukeContext, _run_terraform_teardown

    _owner, infra, _env, run = make_nuke_run(name="prod-infra")
    ctx = _NukeContext(infra=infra, run=run)

    with patch("api.services.nuke_worker._delete_app_security_group", side_effect=RuntimeError("DependencyViolation")), \
         patch("api.services.terraform_worker.TerraformWorker.destroy") as destroy:
        def _fake_destroy(infra_id, nuke=False):
            from api.models.environment import Environment
            Environment.objects.filter(infrastructure_id=infra_id).update(status="DESTROYED")
        destroy.side_effect = _fake_destroy

        _run_terraform_teardown(ctx)  # must not raise

    destroy.assert_called_once()


# ── deployment role + state backend: last-infra vs shared decision ─────────────

def test_deployment_role_keeps_role_and_edits_trust_when_other_infra_shares_the_account(make_nuke_run):
    from api.services.nuke_worker import _manage_deployment_role, _NukeContext

    owner, infra, _env, run = make_nuke_run(name="prod-infra")
    from api.models.infrastructure import Infrastructure
    Infrastructure.objects.create(
        user=owner, name="other-infra", cloud_provider="aws", max_cpu=1, max_memory=1,
        code=infra.code, metadata={"aws_region": "us-east-1"},
    )

    ctx = _NukeContext(infra=infra, run=run)
    fake_iam = MagicMock()
    fake_iam.get_role.return_value = {
        "Role": {"AssumeRolePolicyDocument": {
            "Statement": [{"Effect": "Allow", "Condition": {"StringEquals": {
                "sts:ExternalId": [str(infra.id), "other-external-id"],
            }}}],
        }},
    }
    with patch.object(_NukeContext, "boto_client", return_value=fake_iam):
        result = _manage_deployment_role(ctx)

    assert result["action"] == "kept"
    assert "shared with 1 other" in result["reason"]
    assert result["trust_updated"] is True
    fake_iam.update_assume_role_policy.assert_called_once()
    written_doc = fake_iam.update_assume_role_policy.call_args.kwargs["PolicyDocument"]
    assert str(infra.id) not in written_doc
    assert "other-external-id" in written_doc


def test_deployment_role_deletes_role_and_policy_when_last_infra(make_nuke_run):
    from api.services.nuke_worker import _manage_deployment_role, _NukeContext

    _owner, infra, _env, run = make_nuke_run(name="prod-infra")
    ctx = _NukeContext(infra=infra, run=run)

    fake_iam = MagicMock()
    fake_iam.list_attached_role_policies.return_value = {
        "AttachedPolicies": [{"PolicyName": "LaunchpadDeploymentPolicy", "PolicyArn": "arn:aws:iam::123456789012:policy/LaunchpadDeploymentPolicy"}],
    }
    fake_iam.list_role_policies.return_value = {"PolicyNames": []}
    fake_iam.list_policy_versions.return_value = {"Versions": [{"VersionId": "v1", "IsDefaultVersion": True}]}

    with patch.object(_NukeContext, "boto_client", return_value=fake_iam):
        result = _manage_deployment_role(ctx)

    fake_iam.detach_role_policy.assert_called_once()
    fake_iam.delete_role.assert_called_once_with(RoleName="LaunchpadDeploymentRole")
    assert result["action"] == "deleted"


def test_deployment_role_keeps_role_with_unexpected_attached_policy(make_nuke_run):
    from api.services.nuke_worker import _manage_deployment_role, _NukeContext

    _owner, infra, _env, run = make_nuke_run(name="prod-infra")
    ctx = _NukeContext(infra=infra, run=run)

    fake_iam = MagicMock()
    fake_iam.list_attached_role_policies.return_value = {
        "AttachedPolicies": [{"PolicyName": "SomeoneElsePolicy", "PolicyArn": "arn:aws:iam::123456789012:policy/SomeoneElsePolicy"}],
    }
    fake_iam.list_role_policies.return_value = {"PolicyNames": []}

    with patch.object(_NukeContext, "boto_client", return_value=fake_iam):
        result = _manage_deployment_role(ctx)

    fake_iam.delete_role.assert_not_called()
    assert result["action"] == "kept"
    assert "unexpected" in result["reason"]


def test_deployment_role_mock_mode_never_touches_boto3(make_nuke_run):
    from api.services.nuke_worker import _manage_deployment_role, _NukeContext

    _owner, infra, _env, run = make_nuke_run(name="prod-infra", is_mock=True)
    ctx = _NukeContext(infra=infra, run=run)
    with patch("api.services.nuke_worker.boto3.client") as boto_client:
        result = _manage_deployment_role(ctx)

    boto_client.assert_not_called()
    assert result == {"mock": True}


def test_state_backend_kept_when_other_infra_shares_the_account_and_region(make_nuke_run):
    from api.services.nuke_worker import _manage_state_backend, _NukeContext

    owner, infra, _env, run = make_nuke_run(name="prod-infra")
    from api.models.infrastructure import Infrastructure
    Infrastructure.objects.create(
        user=owner, name="other-infra", cloud_provider="aws", max_cpu=1, max_memory=1,
        code=infra.code, metadata={"aws_region": "us-east-1"},
    )

    ctx = _NukeContext(infra=infra, run=run)
    with patch("api.services.nuke_worker.boto3.client") as boto_client:
        result = _manage_state_backend(ctx)

    boto_client.assert_not_called()
    assert result["action"] == "kept"
    assert "shared with 1 other" in result["reason"]


def test_state_backend_kept_when_other_infra_in_account_claims_a_different_region(make_nuke_run):
    """A missing/stale metadata.aws_region on another infra must never let nuke empty a
    state bucket that infra may still use: any other infra in the account keeps it."""
    from api.services.nuke_worker import _manage_state_backend, _NukeContext

    owner, infra, _env, run = make_nuke_run(name="prod-infra")
    from api.models.infrastructure import Infrastructure
    Infrastructure.objects.create(
        user=owner, name="other-region-infra", cloud_provider="aws", max_cpu=1, max_memory=1,
        code=infra.code, metadata={"aws_region": "eu-west-1"},
    )
    Infrastructure.objects.create(
        user=owner, name="no-region-infra", cloud_provider="aws", max_cpu=1, max_memory=1,
        code=infra.code, metadata={},
    )

    ctx = _NukeContext(infra=infra, run=run)
    with patch("api.services.nuke_worker.boto3.client") as boto_client:
        result = _manage_state_backend(ctx)

    boto_client.assert_not_called()
    assert result["action"] == "kept"
    assert "shared with 2 other" in result["reason"]


def test_state_backend_deleted_when_last_infra_in_account_and_region(make_nuke_run):
    from api.services.nuke_worker import _manage_state_backend, _NukeContext

    _owner, infra, _env, run = make_nuke_run(name="prod-infra")
    ctx = _NukeContext(infra=infra, run=run)

    fake_s3 = MagicMock()
    fake_s3.get_paginator.return_value.paginate.return_value = [{"Versions": [], "DeleteMarkers": []}]
    fake_dynamodb = MagicMock()
    clients = {"s3": fake_s3, "dynamodb": fake_dynamodb}

    with patch.object(_NukeContext, "boto_client", lambda self, service: clients[service]):
        result = _manage_state_backend(ctx)

    fake_s3.delete_bucket.assert_called_once()
    fake_dynamodb.delete_table.assert_called_once()
    assert result["bucket_deleted"] is True
    assert result["table_deleted"] is True


# ── verify ───────────────────────────────────────────────────────────────────────

def test_verify_reports_tagged_leftovers(make_nuke_run):
    from api.services.nuke_worker import _NukeContext, _verify_nothing_left

    _owner, infra, _env, run = make_nuke_run(name="prod-infra")
    ctx = _NukeContext(infra=infra, run=run)

    fake_tagging = MagicMock()
    fake_tagging.get_paginator.return_value.paginate.return_value = [
        {"ResourceTagMappingList": [{"ResourceARN": "arn:aws:ec2:us-east-1:123456789012:security-group/sg-abc"}]},
    ]
    fake_codebuild = MagicMock()
    fake_codebuild.batch_get_projects.return_value = {"projects": []}
    fake_iam = MagicMock()
    fake_iam.get_role.side_effect = ClientError({"Error": {"Code": "NoSuchEntity", "Message": "x"}}, "GetRole")
    clients = {"resourcegroupstaggingapi": fake_tagging, "codebuild": fake_codebuild, "iam": fake_iam, "rds": MagicMock()}

    with patch.object(_NukeContext, "boto_client", lambda self, service: clients[service]):
        result = _verify_nothing_left(ctx)

    assert result["leftovers_found"] == 1
    assert ctx.run.leftovers[0]["id"] == "arn:aws:ec2:us-east-1:123456789012:security-group/sg-abc"


def test_verify_clean_reports_no_leftovers(make_nuke_run):
    from api.services.nuke_worker import _NukeContext, _verify_nothing_left

    _owner, infra, _env, run = make_nuke_run(name="prod-infra")
    ctx = _NukeContext(infra=infra, run=run)

    fake_tagging = MagicMock()
    fake_tagging.get_paginator.return_value.paginate.return_value = [{"ResourceTagMappingList": []}]
    fake_codebuild = MagicMock()
    fake_codebuild.batch_get_projects.return_value = {"projects": []}
    fake_iam = MagicMock()
    fake_iam.get_role.side_effect = ClientError({"Error": {"Code": "NoSuchEntity", "Message": "x"}}, "GetRole")
    clients = {"resourcegroupstaggingapi": fake_tagging, "codebuild": fake_codebuild, "iam": fake_iam, "rds": MagicMock()}

    with patch.object(_NukeContext, "boto_client", lambda self, service: clients[service]):
        result = _verify_nothing_left(ctx)

    assert result["leftovers_found"] == 0
    assert ctx.run.leftovers == []


def test_mock_verify_reports_clean_with_no_aws_calls(make_nuke_run):
    from api.services.nuke_worker import _NukeContext, _verify_nothing_left

    _owner, infra, _env, run = make_nuke_run(name="prod-infra", is_mock=True)
    ctx = _NukeContext(infra=infra, run=run)

    with patch("api.services.nuke_worker.boto3.client") as boto_client:
        result = _verify_nothing_left(ctx)

    boto_client.assert_not_called()
    assert result == {"mock": True}
    assert ctx.run.leftovers == []


def test_apps_step_fails_when_application_service_refuses():
    """Real AWS: a 403 from application-service parsed as a dict without "errors" and
    the apps step reported success with nothing deleted."""
    from api.services import nuke_worker

    response = MagicMock(status_code=403, text='{"detail":"Authentication credentials were not provided."}')
    response.json.return_value = {"detail": "Authentication credentials were not provided."}
    with patch.object(nuke_worker, "ResilientHttpClient") as client_cls:
        client_cls.return_value.post.return_value = response
        with pytest.raises(RuntimeError, match="HTTP 403"):
            nuke_worker._nuke_applications("01a0eaf1-1c80-7fc2-b2d2-eda435072025")


def test_verify_ignores_a_nat_gateway_already_in_deleted_state(make_nuke_run):
    """Real AWS: terraform destroy had deleted the NAT gateway, but the tagging API still
    listed it (NAT gateways stay describable as "deleted" for ~1h) and verify failed the run."""
    from api.services.nuke_worker import _NukeContext, _tagged_leftovers

    _owner, infra, _env, run = make_nuke_run(name="prod-infra")
    ctx = _NukeContext(infra=infra, run=run)
    gone = "arn:aws:ec2:us-east-1:123456789012:natgateway/nat-gone"
    live = "arn:aws:ec2:us-east-1:123456789012:natgateway/nat-live"
    tagging = MagicMock()
    tagging.get_paginator.return_value.paginate.return_value = [
        {"ResourceTagMappingList": [{"ResourceARN": gone}, {"ResourceARN": live}]}
    ]
    ec2 = MagicMock()
    ec2.describe_nat_gateways.side_effect = lambda NatGatewayIds: {
        "NatGateways": [{"State": "deleted" if NatGatewayIds == ["nat-gone"] else "available"}]
    }
    clients = {"resourcegroupstaggingapi": tagging, "ec2": ec2}
    with patch.object(_NukeContext, "boto_client", lambda self, service: clients[service]):
        leftovers = _tagged_leftovers(ctx, str(infra.id))

    assert [item["id"] for item in leftovers] == [live]


@pytest.mark.parametrize("module,view", [
    ("api.views.custom_domain_internal", "custom_domain_disable_for_application"),
    ("api.views.infrastructure_internal", "infrastructure_exit_status"),
])
def test_internal_views_need_no_user_only_the_internal_token(module, view):
    """Machine-to-machine views: no user JWT is ever sent, so the production default
    IsAuthenticated would 403 every call. The internal token is the trust boundary."""
    import importlib

    from rest_framework.permissions import AllowAny

    cls = getattr(importlib.import_module(module), view).cls
    assert list(cls.authentication_classes) == []
    assert list(cls.permission_classes) == [AllowAny]

