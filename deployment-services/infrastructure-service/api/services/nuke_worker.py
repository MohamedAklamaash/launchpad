"""Nuke infrastructure worker: the ordered, idempotent teardown behind POST
.../nuke/. Runs on the infra worker's destroy pool — same per-infra Redis dedup lock
and DB lock as provision/destroy (InfraQueue.enqueue_nuke reuses DESTROY_QUEUE; see
run_worker.py's dispatch_destroy routing on job["action"]), so it can never race a
terraform apply/destroy against the same customer account.

Step order (api/models/nuke_run.py:NUKE_STEP_DEFINITIONS):
  apps -> databases -> terraform_teardown -> leftovers -> shared_resources -> verify

Every step is safe to re-run: NukeWorker.run() skips a step whose stored status is
already "success", so a retry after a mid-run failure resumes instead of repeating
finished work. mock mode (infra.is_mock or MODE=dev) simulates every step with no AWS
calls, mirroring TerraformWorker's own mock branches — steps that only touch the local
DB (apps, databases-as-snapshot-cleanup) still run for real even in mock mode, since
they have no AWS side effect to fake.
"""
import json
import logging
import re
from typing import ClassVar

import boto3
from api.cloud_providers.aws.authenticate import authenticate_infrastructure
from api.common.envs.application import app_config
from api.models.database import Database
from api.models.environment import Environment
from api.models.infrastructure import Infrastructure
from api.models.nuke_run import NukeRun
from api.services.notification import NotificationService
from api.services.terraform_worker import TerraformWorker, _capped_error
from botocore.exceptions import ClientError
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from shared.aws.app_security_group import app_security_group_name
from shared.aws.cost_tags import TAG_INFRA_KEY
from shared.mode import is_dev_mode
from shared.resilience.http_client import ResilientHttpClient

logger = logging.getLogger(__name__)
audit_logger = logging.getLogger("audit.nuke")

_ACCESS_DENIED_CODES = {"AccessDenied", "AccessDeniedException", "UnauthorizedOperation"}
_ROLE_NAME = "LaunchpadDeploymentRole"
_POLICY_NAME = "LaunchpadDeploymentPolicy"
_SNAPSHOT_NOT_FOUND_CODES = {"DBSnapshotNotFound", "DBSnapshotNotFoundFault", "DBClusterSnapshotNotFoundFault"}


class PolicyRefreshRequiredForNuke(RuntimeError):
    """Raised when an AWS call inside a nuke step is denied — the customer's applied
    LaunchpadDeploymentPolicy is behind. Caught by NukeWorker.run() and reported as
    step status "policy_refresh_required" instead of a generic failure."""


def _raise_if_access_denied(error: ClientError):
    code = error.response.get("Error", {}).get("Code", "")
    if code in _ACCESS_DENIED_CODES:
        raise PolicyRefreshRequiredForNuke(
            f"AWS denied {error.response.get('Error', {}).get('Message', code)} — "
            "re-run the Refresh policy script for this infrastructure and retry."
        ) from error


class NukeWorker:
    @staticmethod
    def run(infra_id: str) -> None:
        try:
            infra = Infrastructure.objects.get(id=infra_id)
        except Infrastructure.DoesNotExist:
            logger.error(f"Nuke: infrastructure {infra_id} not found; nothing to do")
            return

        run = NukeRun.objects.filter(infrastructure_id=infra_id).order_by('-created_at').first()
        if not run:
            logger.error(f"Nuke: no NukeRun row for {infra_id}; refusing to run untracked")
            return

        run.status = 'RUNNING'
        if not run.started_at:
            run.started_at = timezone.now()
        run.save(update_fields=['status', 'started_at', 'updated_at'])

        ctx = _NukeContext(infra=infra, run=run)
        for key, step_fn in ctx.STEPS:
            if not NukeWorker._run_step(ctx, key, step_fn):
                return

        if run.leftovers:
            run.status = 'FAILED'
            run.finished_at = timezone.now()
            run.save(update_fields=['status', 'finished_at', 'updated_at'])
            NukeWorker._notify_failure(infra, f"{len(run.leftovers)} resource(s) still present after nuke")
            return

        NukeWorker._finalize_success(infra, run)

    @staticmethod
    def _run_step(ctx: "_NukeContext", key: str, step_fn) -> bool:
        """Runs one step, persists its outcome, and returns whether the run should
        continue to the next step."""
        run = ctx.run
        existing = run.step(key)
        if existing and existing['status'] == 'success':
            return True

        run.set_step(key, 'running', None)
        run.save(update_fields=['steps', 'updated_at'])
        try:
            detail = step_fn(ctx)
        except PolicyRefreshRequiredForNuke as e:
            run.set_step(key, 'policy_refresh_required', str(e))
            run.status = 'FAILED'
            run.finished_at = timezone.now()
            run.save(update_fields=['steps', 'status', 'finished_at', 'updated_at'])
            NukeWorker._notify_failure(ctx.infra, f"{key}: policy refresh required")
            return False
        except Exception as e:
            logger.exception(f"Nuke step {key} failed for {ctx.infra.id}")
            run.set_step(key, 'failed', str(e))
            run.status = 'FAILED'
            run.finished_at = timezone.now()
            run.save(update_fields=['steps', 'status', 'finished_at', 'updated_at'])
            NukeWorker._notify_failure(ctx.infra, f"{key}: {e}")
            return False

        run.set_step(key, 'success', detail)
        # 'leftovers' is included unconditionally: only the verify step ever changes it,
        # but including it every time is cheap and means step_verify's mutation of
        # ctx.run.leftovers (the same object as `run`) is never silently dropped.
        run.save(update_fields=['steps', 'leftovers', 'updated_at'])
        return True

    @staticmethod
    def _finalize_success(infra: Infrastructure, run: NukeRun) -> None:
        run.status = 'COMPLETED'
        run.finished_at = timezone.now()
        run.save(update_fields=['status', 'finished_at', 'updated_at'])

        user_id, name, infra_id = infra.user_id, infra.name, str(infra.id)
        with transaction.atomic():
            Environment.objects.filter(infrastructure_id=infra_id).delete()
            infra.delete()

        try:
            from api.messaging.producer.producer import infra_producer
            infra_producer.publish_infrastructure_deleted(user_id=user_id, infra_id=infra_id)
        except Exception:
            logger.exception(f"Nuke: failed to publish infrastructure.deleted for {infra_id}")

        try:
            NotificationService.send_destroy_success(str(user_id), infra_id, name)
        except Exception:
            logger.exception(f"Nuke: failed to notify success for {infra_id}")
        audit_logger.info("nuke completed", extra={"infrastructure_id": infra_id, "user_id": str(user_id)})

    @staticmethod
    def _notify_failure(infra: Infrastructure, error: str) -> None:
        try:
            NotificationService.send_destroy_failure(
                str(infra.user_id), str(infra.id), infra.name, _capped_error(error),
            )
        except Exception:
            logger.exception(f"Nuke: failed to notify failure for {infra.id}")
        audit_logger.error("nuke failed", extra={"infrastructure_id": str(infra.id), "error": error})


class _NukeContext:
    """Per-run scratch state: the infra/run rows, plus lazily-authenticated AWS
    credentials shared across every AWS-touching step in this attempt. Never assumed to
    survive a worker restart — any data a later step needs is read back from
    `run.step(...)['detail']`, never from an instance attribute, so a resumed run
    (fresh _NukeContext) still works."""

    def __init__(self, infra: Infrastructure, run: NukeRun):
        self.infra = infra
        self.run = run
        self.mock = bool(infra.is_mock) or is_dev_mode(app_config.mode)
        self._credentials = None

    @property
    def region(self) -> str:
        return (self.infra.metadata or {}).get("aws_region", "us-west-2")

    @property
    def account_id(self) -> str:
        return self.infra.code

    def credentials(self) -> dict:
        if self._credentials is None:
            self._credentials = authenticate_infrastructure(self.infra)
            self.infra.refresh_from_db()
        return self._credentials

    def boto_client(self, service: str):
        creds = self.credentials()
        return boto3.client(
            service, region_name=self.region,
            aws_access_key_id=creds.get("aws_access_key_id"),
            aws_secret_access_key=creds.get("aws_secret_access_key"),
            aws_session_token=creds.get("aws_session_token"),
        )

    # ── steps ────────────────────────────────────────────────────────────────────

    def step_apps(self) -> dict:
        return _nuke_applications(str(self.infra.id))

    def step_databases(self) -> dict:
        return _cleanup_database_snapshots(self)

    def step_terraform_teardown(self) -> dict:
        return _run_terraform_teardown(self)

    def step_leftovers(self) -> dict:
        return _cleanup_out_of_terraform_leftovers(self)

    def step_shared_resources(self) -> dict:
        return _manage_shared_resources(self)

    def step_verify(self) -> dict:
        return _verify_nothing_left(self)

    STEPS: ClassVar = [
        ("apps", lambda ctx: ctx.step_apps()),
        ("databases", lambda ctx: ctx.step_databases()),
        ("terraform_teardown", lambda ctx: ctx.step_terraform_teardown()),
        ("leftovers", lambda ctx: ctx.step_leftovers()),
        ("shared_resources", lambda ctx: ctx.step_shared_resources()),
        ("verify", lambda ctx: ctx.step_verify()),
    ]


# ── apps ─────────────────────────────────────────────────────────────────────────

def _nuke_applications(infra_id: str) -> dict:
    """Synchronously force-deletes every Application for this infra via
    application-service's internal endpoint. A long timeout — this runs on a
    background worker thread, not a user-facing request, and per-app AWS cleanup
    (ECS drain, EKS namespace teardown) can legitimately take minutes."""
    client = ResilientHttpClient(
        name="ApplicationServiceNukeClient", base_url=settings.APPLICATION_SERVICE_URL, timeout=600,
    )
    try:
        response = client.post(
            "/api/v1/internal/applications/nuke/",
            json={"infrastructure_id": infra_id},
            headers={"X-INTERNAL-TOKEN": settings.INTERNAL_AUTH_TOKEN},
            timeout=(5, 600),
        )
    except Exception as e:
        raise RuntimeError(f"application-service unreachable: {e}") from e

    result = response.json()
    if result.get("errors"):
        raise RuntimeError(f"{len(result['errors'])} application(s) failed cleanup: {result['errors']}")
    return result


# ── databases (+ snapshots) ─────────────────────────────────────────────────────

def _cleanup_database_snapshots(ctx: _NukeContext) -> dict:
    """Deletes any pre-existing final snapshot for every Database row this infra has
    ever had (live or already-deleted — an earlier single-database delete may have left
    one). Actual live-database resource deletion happens inside the terraform_teardown
    step (skip_final_snapshot=true), not here — see TerraformWorker.destroy's `nuke`
    kwarg docstring for why this is safer than an out-of-band delete racing terraform's
    own state refresh."""
    rows = list(Database.objects.filter(environment__infrastructure_id=ctx.infra.id))
    if ctx.mock:
        return {"snapshots_deleted": 0, "mock": True, "databases": len(rows)}

    deleted = []
    if rows:
        rds = ctx.boto_client("rds")
        for db in rows:
            if db.engine == "redis" or not db.final_snapshot_id:
                continue
            try:
                if db.engine == "docdb":
                    rds.delete_db_cluster_snapshot(DBClusterSnapshotIdentifier=db.final_snapshot_id)
                else:
                    rds.delete_db_snapshot(DBSnapshotIdentifier=db.final_snapshot_id)
                deleted.append(db.final_snapshot_id)
            except ClientError as e:
                _raise_if_access_denied(e)
                code = e.response.get("Error", {}).get("Code", "")
                if code not in _SNAPSHOT_NOT_FOUND_CODES:
                    raise
    return {"snapshots_deleted": deleted}


# ── terraform teardown (custom domains, EKS orphans, terraform destroy) ────────────

def _run_terraform_teardown(ctx: _NukeContext) -> dict:
    TerraformWorker.destroy(str(ctx.infra.id), nuke=True)
    env = Environment.objects.filter(infrastructure_id=ctx.infra.id).first()
    if env is None:
        # destroy() deleted the Environment row itself — only happens if the
        # Infrastructure row had already vanished, which can't be true here.
        return {"status": "DESTROYED"}
    if env.status != "DESTROYED":
        raise RuntimeError(env.error_message or f"terraform destroy left environment in {env.status}")
    return {"status": "DESTROYED"}


# ── out-of-terraform leftovers ──────────────────────────────────────────────────

def _codebuild_project_name(infra_id: str) -> str:
    return re.sub(r'[^a-zA-Z0-9\-_]', '', f"launchpad-build-{infra_id}")


def _codebuild_role_name(infra_id: str) -> str:
    return f"launchpad-codebuild-role-{infra_id}"


def _cleanup_out_of_terraform_leftovers(ctx: _NukeContext) -> dict:
    if ctx.mock:
        return {"mock": True}

    infra_id = str(ctx.infra.id)
    detail = {"codebuild": _delete_codebuild_resources(ctx, infra_id)}
    detail["app_security_group"] = _delete_app_security_group(ctx, infra_id)
    detail["app_log_groups"] = _delete_app_log_groups(ctx)
    return detail


def _delete_codebuild_resources(ctx: _NukeContext, infra_id: str) -> dict:
    project_name = _codebuild_project_name(infra_id)
    role_name = _codebuild_role_name(infra_id)
    result = {"project": project_name, "role": role_name}

    codebuild = ctx.boto_client("codebuild")
    try:
        codebuild.delete_project(name=project_name)
        result["project_deleted"] = True
    except ClientError as e:
        _raise_if_access_denied(e)
        code = e.response.get("Error", {}).get("Code", "")
        result["project_deleted"] = code in ("ResourceNotFoundException",)
        if not result["project_deleted"]:
            raise

    logs = ctx.boto_client("logs")
    log_group = f"/aws/codebuild/{project_name}"
    try:
        logs.delete_log_group(logGroupName=log_group)
        result["log_group_deleted"] = True
    except ClientError as e:
        _raise_if_access_denied(e)
        code = e.response.get("Error", {}).get("Code", "")
        result["log_group_deleted"] = code in ("ResourceNotFoundException",)
        if not result["log_group_deleted"]:
            raise

    iam = ctx.boto_client("iam")
    result["role_deleted"] = _delete_codebuild_role(iam, role_name)
    return result


def _delete_codebuild_role(iam, role_name: str) -> bool:
    try:
        attached = iam.list_attached_role_policies(RoleName=role_name)["AttachedPolicies"]
        inline = iam.list_role_policies(RoleName=role_name)["PolicyNames"]
    except ClientError as e:
        _raise_if_access_denied(e)
        if e.response.get("Error", {}).get("Code") == "NoSuchEntity":
            return True
        raise

    # This role only ever gets the two AWS-managed policies
    # application_deployment_service._trigger_build attaches. Anything else attached is
    # unexpected — leave the role alone rather than guessing what else depends on it.
    expected = {
        "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryPowerUser",
        "arn:aws:iam::aws:policy/CloudWatchLogsFullAccess",
    }
    actual = {p["PolicyArn"] for p in attached}
    if inline or not actual.issubset(expected):
        return False

    for policy in attached:
        iam.detach_role_policy(RoleName=role_name, PolicyArn=policy["PolicyArn"])
    iam.delete_role(RoleName=role_name)
    return True


def _delete_app_security_group(ctx: _NukeContext, infra_id: str) -> dict:
    """Deleted from the leftovers step, after terraform_teardown, so the VPC it lives
    in either already carried this away as part of its own destroy, or is gone and this
    is a clean no-op (a security group cannot outlive its VPC)."""
    sg_name = app_security_group_name(infra_id)
    ec2 = ctx.boto_client("ec2")
    try:
        found = ec2.describe_security_groups(
            Filters=[{"Name": "group-name", "Values": [sg_name]}]
        )["SecurityGroups"]
    except ClientError as e:
        _raise_if_access_denied(e)
        raise
    if not found:
        return {"name": sg_name, "deleted": False, "reason": "not found"}
    sg_id = found[0]["GroupId"]
    try:
        ec2.delete_security_group(GroupId=sg_id)
        return {"name": sg_name, "id": sg_id, "deleted": True}
    except ClientError as e:
        _raise_if_access_denied(e)
        code = e.response.get("Error", {}).get("Code", "")
        if code == "InvalidGroup.NotFound":
            return {"name": sg_name, "deleted": False, "reason": "not found"}
        raise


def _delete_app_log_groups(ctx: _NukeContext) -> list:
    apps_detail = (ctx.run.step("apps") or {}).get("detail") or {}
    names = apps_detail.get("log_groups") or []
    if not names:
        return []
    logs = ctx.boto_client("logs")
    deleted = []
    for name in names:
        try:
            logs.delete_log_group(logGroupName=name)
            deleted.append(name)
        except ClientError as e:
            _raise_if_access_denied(e)
            if e.response.get("Error", {}).get("Code") != "ResourceNotFoundException":
                raise
    return deleted


# ── shared resources (Terraform state backend, deployment role/policy) ─────────────

def _other_infras_same_account(infra: Infrastructure):
    return list(Infrastructure.objects.filter(code=infra.code).exclude(id=infra.id))


def _manage_shared_resources(ctx: _NukeContext) -> dict:
    if ctx.mock:
        return {"mock": True}

    infra = ctx.infra
    others = _other_infras_same_account(infra)
    result = {}

    if others:
        result["role"] = {"action": "kept", "reason": f"shared with {len(others)} other infrastructure(s)"}
        result["role"].update(_remove_external_id_from_trust(ctx, infra, str(infra.id)))
    else:
        result["role"] = _delete_deployment_role_and_policy(ctx)

    same_region_others = [o for o in others if (o.metadata or {}).get("aws_region", "us-west-2") == ctx.region]
    if same_region_others:
        result["state_backend"] = {
            "action": "kept", "reason": f"shared with {len(same_region_others)} other infrastructure(s) in this region",
        }
    else:
        result["state_backend"] = _delete_state_backend(ctx)

    return result


def _remove_external_id_from_trust(ctx: _NukeContext, infra: Infrastructure, external_id: str) -> dict:
    iam = ctx.boto_client("iam")
    try:
        role = iam.get_role(RoleName=_ROLE_NAME)["Role"]
    except ClientError as e:
        _raise_if_access_denied(e)
        if e.response.get("Error", {}).get("Code") == "NoSuchEntity":
            return {"trust_updated": False, "reason": "role not found"}
        raise

    doc = role["AssumeRolePolicyDocument"]
    changed = False
    for statement in doc.get("Statement", []):
        condition = statement.get("Condition", {}).get("StringEquals", {})
        external_ids = condition.get("sts:ExternalId")
        if isinstance(external_ids, list) and external_id in external_ids:
            external_ids.remove(external_id)
            changed = True
        elif external_ids == external_id:
            # A single-ExternalId trust policy with no other infra sharing this role
            # would mean `others` should have been empty — defensive only.
            condition["sts:ExternalId"] = []
            changed = True

    if not changed:
        return {"trust_updated": False, "reason": "external id not present"}
    iam.update_assume_role_policy(RoleName=_ROLE_NAME, PolicyDocument=json.dumps(doc))
    return {"trust_updated": True}


def _delete_deployment_role_and_policy(ctx: _NukeContext) -> dict:
    iam = ctx.boto_client("iam")
    try:
        attached = iam.list_attached_role_policies(RoleName=_ROLE_NAME)["AttachedPolicies"]
        inline = iam.list_role_policies(RoleName=_ROLE_NAME)["PolicyNames"]
    except ClientError as e:
        _raise_if_access_denied(e)
        if e.response.get("Error", {}).get("Code") == "NoSuchEntity":
            return {"action": "deleted", "reason": "role already absent"}
        raise

    unexpected = inline or any(_POLICY_NAME not in p["PolicyArn"] for p in attached)
    if unexpected:
        return {
            "action": "kept",
            "reason": "role has unexpected attached/inline policies beyond LaunchpadDeploymentPolicy — not removed",
        }

    for policy in attached:
        policy_arn = policy["PolicyArn"]
        iam.detach_role_policy(RoleName=_ROLE_NAME, PolicyArn=policy_arn)
        _delete_managed_policy(iam, policy_arn)

    iam.delete_role(RoleName=_ROLE_NAME)
    return {"action": "deleted"}


def _delete_managed_policy(iam, policy_arn: str) -> None:
    versions = iam.list_policy_versions(PolicyArn=policy_arn)["Versions"]
    for version in versions:
        if not version["IsDefaultVersion"]:
            iam.delete_policy_version(PolicyArn=policy_arn, VersionId=version["VersionId"])
    iam.delete_policy(PolicyArn=policy_arn)


def _delete_state_backend(ctx: _NukeContext) -> dict:
    bucket = f"launchpad-tf-state-{ctx.account_id}-{ctx.region}"
    table = f"launchpad-tf-locks-{ctx.account_id}-{ctx.region}"
    result = {"bucket": bucket, "table": table}

    s3 = ctx.boto_client("s3")
    try:
        _empty_bucket(s3, bucket)
        s3.delete_bucket(Bucket=bucket)
        result["bucket_deleted"] = True
    except ClientError as e:
        _raise_if_access_denied(e)
        code = e.response.get("Error", {}).get("Code", "")
        result["bucket_deleted"] = code in ("NoSuchBucket", "404")
        if not result["bucket_deleted"]:
            raise

    dynamodb = ctx.boto_client("dynamodb")
    try:
        dynamodb.delete_table(TableName=table)
        result["table_deleted"] = True
    except ClientError as e:
        _raise_if_access_denied(e)
        code = e.response.get("Error", {}).get("Code", "")
        result["table_deleted"] = code == "ResourceNotFoundException"
        if not result["table_deleted"]:
            raise

    return result


def _empty_bucket(s3, bucket: str) -> None:
    try:
        s3.head_bucket(Bucket=bucket)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchBucket"):
            return
        raise

    paginator = s3.get_paginator("list_object_versions")
    for page in paginator.paginate(Bucket=bucket):
        to_delete = [
            {"Key": v["Key"], "VersionId": v["VersionId"]}
            for v in page.get("Versions", []) + page.get("DeleteMarkers", [])
        ]
        if to_delete:
            s3.delete_objects(Bucket=bucket, Delete={"Objects": to_delete})


# ── verify ───────────────────────────────────────────────────────────────────────

def _verify_nothing_left(ctx: _NukeContext) -> dict:
    if ctx.mock:
        ctx.run.leftovers = []
        return {"mock": True}

    infra_id = str(ctx.infra.id)
    leftovers = []
    leftovers.extend(_tagged_leftovers(ctx, infra_id))
    leftovers.extend(_untagged_leftovers(ctx, infra_id))

    ctx.run.leftovers = leftovers
    return {"leftovers_found": len(leftovers)}


def _tagged_leftovers(ctx: _NukeContext, infra_id: str) -> list:
    tagging = ctx.boto_client("resourcegroupstaggingapi")
    leftovers = []
    try:
        paginator = tagging.get_paginator("get_resources")
        for page in paginator.paginate(TagFilters=[{"Key": TAG_INFRA_KEY, "Values": [infra_id]}]):
            for resource in page.get("ResourceTagMappingList", []):
                arn = resource["ResourceARN"]
                leftovers.append({"type": arn.split(":")[2] if ":" in arn else "unknown", "id": arn,
                                   "reason": f"still tagged {TAG_INFRA_KEY}={infra_id}"})
    except ClientError as e:
        _raise_if_access_denied(e)
        raise
    return leftovers


def _untagged_leftovers(ctx: _NukeContext, infra_id: str) -> list:
    """Explicit describes for kinds the tagging API doesn't reliably index (IAM is
    excluded from resourcegroupstaggingapi entirely) or that were never tagged."""
    leftovers = []

    project_name = _codebuild_project_name(infra_id)
    codebuild = ctx.boto_client("codebuild")
    projects = codebuild.batch_get_projects(names=[project_name])["projects"]
    if projects:
        leftovers.append({"type": "codebuild_project", "id": project_name, "reason": "still present"})

    role_name = _codebuild_role_name(infra_id)
    iam = ctx.boto_client("iam")
    if _iam_role_exists(iam, role_name):
        leftovers.append({"type": "iam_role", "id": role_name, "reason": "still present"})

    shared_step = ctx.run.step("shared_resources") or {}
    shared_detail = shared_step.get("detail") or {}
    if (shared_detail.get("role") or {}).get("action") == "deleted" and _iam_role_exists(iam, _ROLE_NAME):
        leftovers.append({"type": "iam_role", "id": _ROLE_NAME, "reason": "expected deleted but still present"})

    for db in Database.objects.filter(environment__infrastructure_id=infra_id):
        if db.engine == "redis" or not db.final_snapshot_id:
            continue
        if _snapshot_exists(ctx, db):
            leftovers.append({"type": "db_snapshot", "id": db.final_snapshot_id, "reason": "still present"})

    return leftovers


def _iam_role_exists(iam, role_name: str) -> bool:
    try:
        iam.get_role(RoleName=role_name)
        return True
    except ClientError as e:
        _raise_if_access_denied(e)
        if e.response.get("Error", {}).get("Code") == "NoSuchEntity":
            return False
        raise


def _snapshot_exists(ctx: _NukeContext, db: Database) -> bool:
    rds = ctx.boto_client("rds")
    try:
        if db.engine == "docdb":
            rds.describe_db_cluster_snapshots(DBClusterSnapshotIdentifier=db.final_snapshot_id)
        else:
            rds.describe_db_snapshots(DBSnapshotIdentifier=db.final_snapshot_id)
        return True
    except ClientError as e:
        _raise_if_access_denied(e)
        if e.response.get("Error", {}).get("Code") in _SNAPSHOT_NOT_FOUND_CODES:
            return False
        raise
