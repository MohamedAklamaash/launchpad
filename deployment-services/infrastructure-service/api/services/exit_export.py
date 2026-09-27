"""Exit export (F6): a continuity handover archive, streamed and never persisted.

Assembled in memory exactly like the evidence pack (F5) — this is a bigger, more
sensitive sibling of it, not a new pattern. Read-only against the customer's account: the
Terraform bundle is generated the same way `terraform_worker._generate_config` renders it
for a real apply, never `terraform output` or a read of the remote state file, and no AWS
call is made from this module at all — the whole archive is built from rows Launchpad
already owns plus a same-origin call to application-service.

H6 is the design: three of the archive's content types carry secrets today (ECS task-def
`environment`, Kubernetes `Deployment` env / `Secret.data`, the buildspec's GitHub-token
fallback) and a fourth risk exists indirectly (`backend.hcl` points at a state bucket whose
objects hold plaintext secrets Launchpad never fetches). Every value is redacted **before**
it reaches this module — see `application-service/api/services/exit_inventory.py` — this
module only assembles already-redacted text into a zip.
"""
import io
import uuid
import zipfile

from api.cloud_providers.aws.iam_policy.live_diff import POLICY_NAME, ROLE_NAME
from api.models.environment import Environment
from api.repositories.infrastructure import InfrastructureRepository
from api.services.infrastructure import validate_aws_region, validate_vpc_cidr
from api.services.terraform_worker import (
    DEFAULT_EKS_CLUSTER_VERSION,
    TF_MODULES_DIR,
    TerraformWorker,
)
from django.conf import settings
from shared.enums.orchestrator import ComputeType
from shared.resilience.http_client import ResilientHttpClient

MAX_ARCHIVE_BYTES = 20 * 1024 * 1024
# A sanity backstop on the internal call's response, checked before .json() parses it —
# per-app inventory entries are a few KB each; anything past this points at a runaway
# upstream, not a legitimately large infrastructure.
MAX_UPSTREAM_RESPONSE_BYTES = 10 * 1024 * 1024


class ExitExportNotFound(Exception):
    """Infrastructure not found, or infra_id is not a valid UUID."""


class ExitExportForbidden(Exception):
    """Authenticated user is not this infrastructure's owner."""


class ExitExportUpstreamError(Exception):
    """application-service's export-inventory call failed or timed out."""


class ExitExportTooLarge(Exception):
    """Assembled archive exceeded MAX_ARCHIVE_BYTES — a size cap, not expected in
    practice given how bounded the inputs are, kept as a hard backstop."""


def _safe_component(value: str) -> str:
    """A UUID is always filesystem-safe; nothing derived from user input (an app or
    infrastructure name) is ever used as a zip path component — see `_app_paths`."""
    return str(uuid.UUID(str(value)))


def _app_paths(app_id: str) -> str:
    return f"apps/{_safe_component(app_id)}"


def _terraform_bundle(infra) -> tuple[str, str, dict]:
    """(main.tf text, backend.hcl text, facts) — main.tf is `_generate_config`'s real
    output, unmodified: it already carries the true bucket/key/region/table this
    infrastructure's state lives at, so a would-be reader gains nothing this pack doesn't
    already have to say to be useful. `backend.hcl` restates the same four values as a
    ready-to-use `-backend-config` file for a fresh checkout."""
    metadata = infra.metadata or {}
    # Re-validate at the interpolation sink, not just the create-time boundary — matches
    # terraform_worker.py's own rule (a row written before this check existed must not
    # still reach an HCL string). Here that HCL is handed to the customer to run.
    if metadata.get("aws_region") is not None:
        validate_aws_region(metadata["aws_region"])
    if metadata.get("vpc_cidr") is not None:
        validate_vpc_cidr(metadata["vpc_cidr"])

    region = metadata.get("aws_region", "us-west-2")
    account_id = infra.code or "default"
    compute_type = infra.compute_type
    infra_id = str(infra.id)
    bucket = f"launchpad-tf-state-{account_id}-{region}"
    table = f"launchpad-tf-locks-{account_id}-{region}"
    key = f"infra/{infra_id}/terraform.tfstate"

    live_dbs = TerraformWorker._live_dbs_for_infra(infra_id)
    vars = {
        "environment": f"cli-{infra_id}",
        "owner": str(infra.user_id),
        "project": "launchpad-infra",
        "aws_region": region,
        "vpc_cidr": metadata.get("vpc_cidr", "10.0.0.0/16"),
        "cluster_version": metadata.get("cluster_version", DEFAULT_EKS_CLUSTER_VERSION),
        # Left blank rather than fetched or re-created: this pack never calls AWS, and a
        # get-or-create here would be a write against a "strictly read-only" export. The
        # README tells the customer which security group id to fill in if they have a
        # live managed database.
        "db_app_sg_id": "",
    }
    main_tf = TerraformWorker._generate_config(vars, infra_id, bucket, table, region, compute_type, account_id)
    backend_hcl = (
        f'bucket         = "{bucket}"\n'
        f'key            = "{key}"\n'
        f'region         = "{region}"\n'
        f'dynamodb_table = "{table}"\n'
        f'encrypt        = true\n'
    )
    facts = {
        "state_bucket": bucket, "lock_table": table, "state_key": key,
        "has_live_managed_database": live_dbs.exists(),
    }
    return main_tf, backend_hcl, facts


# Allowlist, not a denylist: the module tree today contains only `.tf` files (verified
# against infra/aws/modules/ — no `.tfvars`, `.tftpl`, docs, or anything else). A denylist
# of "known bad" extensions (state files, provider binaries) only ever excludes what
# someone thought to name; an allowlist of "known good" ones can't be bypassed by a
# `.tfstate.json` or a provider binary with no extension at all. Widen this only when the
# module tree genuinely grows a new legitimate file type.
_ALLOWED_MODULE_FILE_SUFFIXES = frozenset({".tf"})


def _copy_terraform_modules(zf: zipfile.ZipFile) -> None:
    """Copies the template modules this container ships with — never a state file, a
    `.tfvars`, a provider binary a developer might have left behind from running terraform
    in-tree, or anything reached through a symlink. `Path.rglob` follows directory
    symlinks, so a symlinked subdirectory (or the module directory entry itself) pointing
    outside the module tree would otherwise have its real target's files enumerated and
    zipped with no `is_symlink()` true anywhere in that walk — resolving every candidate
    and requiring it stay under the module's own resolved root is what actually closes
    that, not a per-path `is_symlink()` check alone."""
    modules_dir = TF_MODULES_DIR / "modules"
    for module in sorted(modules_dir.iterdir()):
        if module.is_symlink() or not module.is_dir():
            continue
        module_real = module.resolve()
        for path in sorted(module.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            if path.suffix not in _ALLOWED_MODULE_FILE_SUFFIXES:
                continue
            try:
                resolved = path.resolve(strict=True)
            except OSError:
                continue
            if resolved != module_real and module_real not in resolved.parents:
                continue
            arcname = f"terraform/modules/{module.name}/{path.relative_to(module)}"
            zf.write(path, arcname=arcname)


def _account_role_arns(infra) -> dict:
    account_id = infra.code or "<account-id>"
    return {
        "role_arn": f"arn:aws:iam::{account_id}:role/{ROLE_NAME}",
        "policy_name": POLICY_NAME,
    }


def _render_readme(infra, environment, inventory: dict, tf_facts: dict) -> str:
    account_id = infra.code or "<account-id>"
    region = (infra.metadata or {}).get("aws_region", "us-west-2")
    codebuild = inventory.get("codebuild", {})
    codebuild_project_arn = (
        f"arn:aws:codebuild:{region}:{account_id}:project/{codebuild.get('project_name')}"
    )
    codebuild_role_arn = f"arn:aws:iam::{account_id}:role/{codebuild.get('role_name')}"

    lines = [
        f"# Launchpad exit export — infrastructure {infra.id}",
        "",
        (
            "This archive documents what Launchpad has deployed into your AWS account so "
            "you can keep running it without Launchpad. Nothing here changes anything in "
            "your account — see REVOCATION.md for the one change you make yourself."
        ),
        "",
        "## Inventory",
        "",
        f"- Compute type: `{infra.compute_type}`",
        f"- AWS account: `{account_id}`, region `{region}`",
        f"- VPC: `{environment.vpc_id if environment else 'unknown'}`",
        f"- ALB: `{environment.alb_arn if environment else 'unknown'}` (`{environment.alb_dns if environment else 'unknown'}`)",
        f"- ECR repository: `{environment.ecr_repository_url if environment else 'unknown'}`",
        f"- ECS task execution role: `{environment.ecs_task_execution_role_arn if environment else 'unknown'}`" if infra.compute_type != ComputeType.EKS else f"- EKS cluster: `{environment.cluster_arn if environment else 'unknown'}`",
        f"- CodeBuild project: `{codebuild_project_arn}`",
        f"- CodeBuild service role: `{codebuild_role_arn}`",
        "",
        "### Applications",
        "",
    ]
    for app in inventory.get("apps", []):
        lines.append(f"#### {app['name']} (`{app['id']}`)")
        lines.append(f"- Repository: {app['repo_url']} (branch `{app['branch']}`)")
        if infra.compute_type == ComputeType.EKS:
            refs = app.get("runtime_refs") or {}
            lines.append(f"- Namespace: `{refs.get('namespace', 'unknown')}`")
            lines.append(f"- Deployment/Service/Ingress: `{refs.get('deployment', app['slug'])}`")
            lines.append(f"- Manifest: `apps/{app['id']}/k8s-manifest.json`")
        else:
            lines.append(f"- ECS service: `{app.get('service_arn') or 'unknown'}`")
            lines.append(f"- Task definition: `{app.get('task_definition_arn') or 'unknown'}`")
            lines.append(f"- Target group: `{app.get('target_group_arn') or 'unknown'}`")
            lines.append(f"- Listener rule: `{app.get('listener_rule_arn') or 'unknown'}`")
            lines.append(f"- Task definition (redacted, cleaned): `apps/{app['id']}/task-definition.json`")
        lines.append("")

    lines += [
        "## Terraform-managed resources",
        "",
        (
            "Networking, IAM, ALB/ECS or EKS cluster scaffolding, and any managed database "
            "are Terraform-managed and already live in your account's own state:"
        ),
        f"- State bucket: `{tf_facts['state_bucket']}`",
        f"- Lock table: `{tf_facts['lock_table']}`",
        f"- State key: `{tf_facts['state_key']}`",
        "",
        (
            "`terraform/main.tf` + `terraform/modules/` reproduce exactly what Launchpad's "
            "provisioning worker generates today for this infrastructure's compute type "
            "and current managed-database set; `terraform/backend.hcl` restates the same "
            "backend block as a `-backend-config` file. Run `terraform init` from "
            "`terraform/` to pick the state back up."
        ),
    ]
    if tf_facts["has_live_managed_database"]:
        lines.append(
            "\n**This infrastructure has a live managed database.** The generated "
            "`main.tf`'s database module block(s) have `app_security_group_id = \"\"` — "
            "fill in the id of the security group named for this infrastructure "
            "(`aws ec2 describe-security-groups --filters Name=group-name,Values=infra-*-fargate-sg`) "
            "before running `terraform apply` again."
        )

    lines += [
        "",
        "## CI seed",
        "",
        (
            "`buildspec/buildspec.yml` is the exact build definition Launchpad's CodeBuild "
            "project runs. `buildspec/env.example` lists the environment variables it "
            "needs per app, with `GITHUB_TOKEN` always shown as `<redacted>` — Launchpad "
            "never stores this value in a form this export can read; supply your own."
        ),
        "",
        "## GitHub webhooks",
        "",
        "Remove these from each repository's Settings → Webhooks — they point at Launchpad and will fail silently once you revoke access:",
        "",
    ]
    webhook_apps = [a for a in inventory.get("apps", []) if a.get("webhook_url")]
    if webhook_apps:
        for app in webhook_apps:
            lines.append(f"- **{app['name']}** (`{app['repo_url']}`): `{app['webhook_url']}`")
    else:
        lines.append("- No application on this infrastructure has an active webhook.")

    lines += [
        "",
        "## DNS and custom domains",
        "",
        (
            f"This infrastructure's platform hostnames under `{infra.dns_label or '<unset>'}."
            f"{getattr(settings, 'RESERVED_DOMAIN_SUFFIX', 'launchpad.app')}` stop resolving "
            "once you complete the exit action (or once the infrastructure is deleted) — "
            "Launchpad's platform DNS zone is not part of your account and is not "
            "handed over. Point any production traffic at your own custom domain before "
            "then; a CNAME to your ALB (ECS) or Ingress hostname (EKS) works the same way "
            "your platform hostname did."
        ),
        "",
        "## Revocation",
        "",
        "See REVOCATION.md.",
        "",
    ]
    return "\n".join(lines)


def _render_revocation(infra) -> str:
    role_arns = _account_role_arns(infra)
    return "\n".join([
        "# Revoking Launchpad's access",
        "",
        (
            "Launchpad's only access to your account is the cross-account role below, "
            "assumed with `ExternalId` equal to this infrastructure's id "
            f"(`{infra.id}`). Deleting it revokes every capability Launchpad has, "
            "immediately."
        ),
        "",
        f"1. Detach and delete the policy `{role_arns['policy_name']}` from the role `{ROLE_NAME}`.",
        f"2. Delete the role: `aws iam delete-role --role-name {ROLE_NAME}`.",
        f"   (Role ARN: `{role_arns['role_arn']}`)",
        (
            "3. Nothing else Launchpad ever created in your account depends on this role "
            "existing — your applications, load balancer, and any managed database keep "
            "running unaffected."
        ),
        "",
    ])


def build_archive(infra, environment, inventory: dict) -> bytes:
    main_tf, backend_hcl, tf_facts = _terraform_bundle(infra)
    readme = _render_readme(infra, environment, inventory, tf_facts)
    revocation = _render_revocation(infra)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("README.md", readme)
        zf.writestr("REVOCATION.md", revocation)
        zf.writestr("terraform/main.tf", main_tf)
        zf.writestr("terraform/backend.hcl", backend_hcl)
        _copy_terraform_modules(zf)
        zf.writestr("buildspec/buildspec.yml", inventory.get("buildspec", ""))
        env_lines = []
        for app in inventory.get("apps", []):
            env_lines.append(f"# {app['name']}")
            env_lines.append(f"REPO_URL={app['repo_url']}")
            env_lines.append(f"BRANCH={app['branch']}")
            env_lines.append(f"APP_NAME={app['slug']}")
            env_lines.append(f"DOCKERFILE_PATH={app['dockerfile_path'] or 'Dockerfile'}")
            env_lines.append(f"BUILD_CONTEXT={app['build_context'] or ''}")
            env_lines.append("GITHUB_TOKEN=<redacted>")
            env_lines.append("")
        zf.writestr("buildspec/env.example", "\n".join(env_lines))
        for app in inventory.get("apps", []):
            path = _app_paths(app["id"])
            if app.get("task_definition_json"):
                zf.writestr(f"{path}/task-definition.json", app["task_definition_json"])
            if app.get("k8s_manifest_json"):
                zf.writestr(f"{path}/k8s-manifest.json", app["k8s_manifest_json"])

    data = buf.getvalue()
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ExitExportTooLarge(f"Exit export exceeded {MAX_ARCHIVE_BYTES} bytes")
    return data


class ExitExportService:
    def __init__(self):
        self.infra_repo = InfrastructureRepository()
        self._app_client = ResilientHttpClient(
            name="ApplicationServiceExportClient",
            base_url=settings.APPLICATION_SERVICE_URL,
            timeout=settings.EXIT_EXPORT_UPSTREAM_TIMEOUT_SECONDS,
        )

    def get_owned_infra(self, user_id, infra_id):
        # A malformed id reaching filter(id=...) on a UUIDField raises Django's
        # ValidationError, not a ValueError — matches evidence_pack.py's guard.
        try:
            uuid.UUID(str(infra_id))
        except ValueError:
            raise ExitExportNotFound("Infrastructure not found")
        infra = self.infra_repo.get_by_id(user_id, infra_id)
        if not infra:
            raise ExitExportNotFound("Infrastructure not found")
        if str(infra.user_id) != str(user_id):
            raise ExitExportForbidden("Only the infrastructure owner can export this infrastructure")
        return infra

    def fetch_app_inventory(self, infra, authorization_header: str | None) -> dict:
        """Same-origin call to application-service, forwarding the caller's own JWT so
        it re-checks ownership against its own copy of Infrastructure — a second,
        independent authorization check, not just a trust-the-caller hop. No new JWT or
        internal-auth exemption is needed: this is an ordinary protected endpoint."""
        headers = {"X-INTERNAL-TOKEN": settings.INTERNAL_AUTH_TOKEN}
        if authorization_header:
            headers["Authorization"] = authorization_header
        try:
            # (connect, read) explicitly: a bare float applies to *each* phase, so the
            # constructor's single timeout value would allow up to 2x itself worst case —
            # comfortably over the gateway's fixed 10s proxy timeout otherwise.
            # allow_redirects=False: this call carries the caller's own bearer token and
            # the shared internal-auth secret — a redirect (a misconfigured
            # APPLICATION_SERVICE_URL, or a compromised upstream) must never cause
            # `requests` to silently replay both onto a different host.
            response = self._app_client.get(
                f"/api/v1/infrastructures/{infra.id}/export-inventory/", headers=headers,
                timeout=(2, settings.EXIT_EXPORT_UPSTREAM_TIMEOUT_SECONDS),
                allow_redirects=False,
            )
        except Exception as e:
            raise ExitExportUpstreamError(str(type(e).__name__)) from e
        finally:
            # self._app_client is a module-level singleton (ExitExportService is
            # instantiated once), so its requests.Session is reused across every caller —
            # any Set-Cookie on one response must never ride along into the next
            # unrelated user's or infrastructure's request.
            self._app_client.session.cookies.clear()
        if response.status_code != 200:
            raise ExitExportUpstreamError(f"export-inventory returned {response.status_code}")
        if len(response.content) > MAX_UPSTREAM_RESPONSE_BYTES:
            raise ExitExportUpstreamError("export-inventory response exceeded the size limit")
        return response.json()

    def build(self, infra, authorization_header: str | None) -> tuple[bytes, int]:
        inventory = self.fetch_app_inventory(infra, authorization_header)
        environment = Environment.objects.filter(infrastructure_id=infra.id).first()
        zip_bytes = build_archive(infra, environment, inventory)
        return zip_bytes, len(inventory.get("apps", []))
