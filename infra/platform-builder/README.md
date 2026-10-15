# Platform builder — Kaniko on Fargate

**Platform-owned, like `infra/platform-dns/`.** Everything under
`deployment-services/infrastructure-service/infra/aws/` provisions the *customer's*
account; this provisions Launchpad's own, to build customer images instead of asking the
customer's account to run the build (`plan/F7-platform-builder.md`). It is the second
platform-owned tree in the repo, not the first — `infra/platform-dns/` was — but the
separation rule it set is the one this tree follows too.

It exists because `application-service` currently builds every customer image with AWS
CodeBuild **inside the customer's account**, which breaks outright on any customer account
with a zero CodeBuild concurrency quota (confirmed, not hypothetical — see
`plan/F7-platform-builder.md`'s "quota fact" section). Moving the build onto platform-owned
Fargate capacity removes that dependency.

This tree started as Terraform **ticket 2** of F7's execution plan (the skeleton) and was
extended by **ticket 5** (the builder-launcher role, the init-container/volume credential
path, dropping Secrets Manager — see "Credential delivery" below) once the builder client
landed in `deployment-services/application-service/aws/platform_builder.py`. No
application code lives *here* — it's still just the cluster, task definition, roles, and
bucket that `aws/platform_builder.py` drives.

## Why this can't live in the customer tree, and can't share the provisioning worker's credentials

- **Account.** This applies to a platform-owned AWS account (or at minimum a dedicated
  VPC/subnet set in a platform account not used for anything customer-reachable) — never a
  customer account, and ideally not the same account that holds
  `LAUNCHPAD_PLATFORM_PRINCIPAL_ARN`'s trust-policy principal, if that is treated as a
  separate blast-radius concern. A credential leaked from here can push to one scoped ECR
  repository at a time (the push credential application-service mints per build, from the
  customer's own `LaunchpadDeploymentRole` session — see F7 "Credential flow"); it grants
  nothing about this account's own infrastructure beyond what's defined here.
- **Credential.** The infrastructure-service provisioning worker already holds
  `JWT_SECRET`, `INTERNAL_API_TOKEN`, database credentials, and the platform IAM user keys
  used to assume `LaunchpadDeploymentRole` into every customer account. None of that
  worker's credentials can launch, monitor, or read the logs of a build task — only
  `aws_iam_role.builder_launcher` can, and only `application-service`'s build-launcher
  process (`aws/platform_builder.py`) is trusted to assume into it
  (`var.launcher_principal_arn`). This is the same reasoning `infra/platform-dns/README.md`
  gives for keeping the DNS writer out of the provisioning worker's hands — and the same
  "assume-only, no policy attached to the user itself" stance the platform IAM user
  already keeps for `LaunchpadDeploymentRole` (see `aws/session.py`): that user is never
  given `builder_launcher`'s permissions directly, only `sts:AssumeRole` onto this role.
- **Network.** Private subnets with NAT, no inbound, and no route back into any other
  platform VPC. A hostile customer Dockerfile's `RUN` step has outbound reach (to pull
  images and push the result) and nowhere to pivot to.

## What's here

| File | Contents |
|---|---|
| `versions.tf` / `providers.tf` | Provider pin, default tags, `aws_caller_identity`/`aws_region` data sources. No backend block — left to the operator, same as `platform-dns`. |
| `network.tf` | VPC, 2 public + 2 private subnets, one NAT (in a public subnet, which exists only to host it), private route table → NAT, a build-task security group with no ingress and all-protocol egress. |
| `ecr.tf` | One ECR repo for the mirrored Kaniko image — immutable tags, scan on push. |
| `logs.tf` | One CloudWatch log group for build output, short retention (`var.build_log_retention_days`, default 7). See "Log group vs. per-build log stream" below — this is a deliberate deviation from the design doc's literal wording. |
| `s3.tf` | One S3 bucket for per-build source tarballs — private, SSE-S3 encrypted, all public access blocked, versioning off, 1-day lifecycle expiry under `var.source_prefix`, deny-insecure-transport bucket policy. |
| `ecs.tf` | Fargate-only ECS cluster + the Kaniko task definition: a shared `docker-config` ephemeral volume, a non-essential `init` container (pinned by digest, copies `docker-config.json` from S3 into that volume) that `kaniko` (essential, `dependsOn` `init`'s `SUCCESS`) mounts read-only at `/kaniko/.docker`. |
| `iam.tf` | Task execution role, build task role, the `builder_launcher` policy document, and `aws_iam_role.builder_launcher` (trusts `var.launcher_principal_arn`) that document is actually attached to. |
| `variables.tf` / `outputs.tf` | Documented inline; outputs are everything `aws/platform_builder.py` needs to actually drive this: cluster, task def, subnets, SG, bucket, log group, both ECS role ARNs, the launcher role ARN, the launcher policy document (for audit). |

## Apply it in its own account

```bash
cd infra/platform-builder
terraform init
terraform apply \
  -var kaniko_image_digest=sha256:<digest from the mirror step below> \
  -var aws_cli_image_digest=sha256:<digest of public.ecr.aws/aws-cli/aws-cli, see below> \
  -var launcher_principal_arn=arn:aws:iam::<account>:user/<application-service's build-launcher user>
```

No backend configuration is defined — add one (S3 + DynamoDB lock, or your platform's
convention) before a second person needs to apply this.

### Mirror the Kaniko image before the first apply

`kaniko_image_digest` has no default on purpose: a digest must be deliberately chosen, not
silently resolved from a tag. Mirror the maintained Chainguard fork (Google archived
upstream kaniko in June 2025 — see F7's Decision section) into
`aws_ecr_repository.kaniko_mirror` before applying anything that references the digest:

```bash
# After a first apply that creates the (empty) mirror repo, or via a throwaway
# aws_ecr_repository data source if you want the repo URL before applying everything else.
# <upstream-kaniko-source> — confirm the current published image reference against the
# Chainguard fork's own README before using it here; not hardcoded in this doc since an
# image reference copied from memory is exactly the kind of unverified claim this repo's
# conventions (see platform-dns README's "I have not verified..." note) ask not to assert.
crane copy <upstream-kaniko-source> <mirror-repo-url>:latest
crane digest <mirror-repo-url>:latest   # sha256:... — use this for kaniko_image_digest
```

Re-apply with `-var kaniko_image_digest=<that digest>` once you have it. Re-mirror and
re-apply whenever the upstream image is updated; nothing here does that automatically.

### Pin the init container's image digest

`aws_cli_image_digest` has no default, same reasoning as `kaniko_image_digest`. Unlike
Kaniko, this one is **not** mirrored into this account's ECR — it's pulled straight from
the public ECR Gallery (`public.ecr.aws/aws-cli/aws-cli`), which needs no IAM grant to
pull anonymously and isn't subject to Docker Hub's rate limits:

```bash
crane digest public.ecr.aws/aws-cli/aws-cli:latest   # sha256:... — confirm the tag you want first
```

### Creating the build-launcher principal — out of band

`aws_iam_role.builder_launcher` trusts exactly one principal, named by
`var.launcher_principal_arn` (no default — must be set on every apply). That principal
itself — a dedicated IAM user for `application-service`'s build-launcher process, with
long-lived keys — is created **out of band**, the same reasoning `platform-dns` gives for
creating its DNS-writer access key outside Terraform: state is a file people copy, and a
long-lived secret has no business sitting in it. Hand this tree only the ARN.

That principal must **not** be the infrastructure-service provisioning worker's identity,
and must not be given `sts:AssumeRole` onto `LaunchpadDeploymentRole` — see "Why this can't
live in the customer tree" above. It needs exactly one permission of its own:
`sts:AssumeRole` onto `aws_iam_role.builder_launcher.arn` (this tree's own output) —
everything else it can do flows from that role's policy, not from anything attached to
the user directly.

## Credential delivery (ticket 5)

The original skeleton (ticket 2) sketched a per-build Secrets Manager secret for the
scoped ECR push credential. That's gone: ECS fixes a container's `secrets` block at
task-definition *registration*, not per `RunTask`, so a per-build secret would need a
task-definition revision per build — see `plan/F7-platform-builder.md`'s Decision section.
Instead, the credential travels exactly like the source tarball does: `application-service`
writes `sources/<build_id>/docker-config.json` beside `sources/<build_id>/source.tar.gz`
in the sources bucket; the task definition's `init` container (non-essential, `aws-cli`
pinned by digest) copies it into the shared `docker-config` volume, and `kaniko`
(`dependsOn: [{init, SUCCESS}]`) mounts that volume read-only at `/kaniko/.docker`, where
Kaniko reads registry auth from by default. Both S3 objects are deleted by the worker once
the build task stops, success or failure.

The build task role keeps only `s3:GetObject` on `var.source_prefix/*` — no
`s3:ListBucket` anywhere. A hostile `RUN` step that reaches the task role's own
credentials still can't enumerate another build's source or credential; it can only ever
read the exact key it already knows, and that key is an unguessable `uuid4`.

## Log group vs. per-build log stream

F7's "Platform infra needed" section asks for log groups "named per-build (include the
build id, not the infra id)". In practice, the awslogs `logConfiguration` is fixed at
task-definition register time, and ECS's `RunTask` container overrides have no field that
repoints a container's log group per invocation — only the command, environment, and a
handful of other fields are overridable. Registering a new task-definition revision per
build to get a per-build log *group* is out of scope for this skeleton (no application code
here).

What this tree actually gives you: **one log group, one stream per task**
(`awslogs-stream-prefix = "build"` → stream names like `build/kaniko/<ecs-task-id>`), which
still satisfies the underlying intent — retention and access control key off a
build-scoped identifier, never an infra id, exactly as F7 asks. If a literal per-build log
*group* turns out to matter (for example, per-build IAM-scoped read access rather than
access control enforced at the application layer), that's a task-definition-per-build
redesign, not a skeleton change — flagged as an open question in the PR, not resolved here.

## What's intentionally not here

- **Most application code.** This tree's roles and task definition are driven by
  `deployment-services/application-service/aws/platform_builder.py` (ticket 5), but the
  per-build values themselves — build id, S3 keys, `--destination` — are never baked into
  Terraform; they arrive per `RunTask` as `containerOverrides`. Ticket 6 (wiring this into
  `application_deployment_service.py`) is still unstarted.
- **The build-launcher *user*.** `aws_iam_role.builder_launcher` is defined and attached
  here; the IAM user that assumes into it is created out of band (above), same pattern as
  `platform-dns`'s access key.
- **Any Secrets Manager resource.** Dropped in ticket 5 — see "Credential delivery" above.
- **Per-build resources of any kind** (S3 objects, task-definition revisions). Everything
  per-build is created and torn down by `aws/platform_builder.py`, not this tree.
- **A backend block.** Left to the operator, matching `platform-dns`.
- **VPC endpoints for ECR/S3.** The NAT path works without them; adding interface
  endpoints later is a cost/latency optimization, not a correctness requirement, and is
  left for whoever operates this once real build volume exists.
- **Dropping the per-infra CodeBuild path** in the customer account, or narrowing
  `LaunchpadDeploymentPolicy`'s `codebuild:*` grant. F7's Non-goals are explicit that this
  ships behind a feature flag with the old path kept as fallback — unrelated to this tree
  existing.
