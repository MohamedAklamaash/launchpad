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

This is Terraform **ticket 2** of F7's execution plan: the skeleton only. No application
code lives here — nothing launches a build, mints a credential, or fetches source. That is
tickets 3–6, in `deployment-services/application-service/aws/`.

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
  worker's credentials can launch, monitor, or read the logs of a build task — the
  `builder_launcher_policy_json` output below is wired to a *separate* principal that
  `application-service`'s build-launcher process holds, and nothing else. This is the same
  reasoning `infra/platform-dns/README.md` gives for keeping the DNS writer out of the
  provisioning worker's hands.
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
| `ecs.tf` | Fargate-only ECS cluster + the Kaniko executor task definition (cpu/memory from variables, x86_64, pinned by digest, awslogs). |
| `iam.tf` | Task execution role, build task role, and the `builder_launcher_policy_json` output (not attached to anything here). |
| `variables.tf` / `outputs.tf` | Documented inline; outputs are everything the build-launcher code (tickets 3–6) needs to actually drive this: cluster, task def, subnets, SG, bucket, log group, both role ARNs, the launcher policy document. |

## Apply it in its own account

```bash
cd infra/platform-builder
terraform init
terraform apply -var kaniko_image_digest=sha256:<digest from the mirror step below>
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
crane copy ghcr.io/chainguard-images/kaniko:latest <mirror-repo-url>:latest
crane digest <mirror-repo-url>:latest   # sha256:... — use this for kaniko_image_digest
```

Re-apply with `-var kaniko_image_digest=<that digest>` once you have it. Re-mirror and
re-apply whenever the upstream image is updated; nothing here does that automatically.

### Wire the launcher policy to a real principal — out of band

```bash
terraform output -raw builder_launcher_policy_json
```

Attach that document to whichever IAM principal `application-service`'s build-launcher
process authenticates as (a dedicated IAM user with long-lived keys, or a role if the
process already runs somewhere that can assume one). Creating and attaching that principal
is deliberately not a resource here — the same reasoning `platform-dns` gives for creating
its DNS-writer access key out of band: Terraform state is a file people copy, and a
long-lived secret has no business sitting in it.

That principal must **not** be the infrastructure-service provisioning worker's identity,
and must not be given `sts:AssumeRole` onto `LaunchpadDeploymentRole` — see "Why this can't
live in the customer tree" above.

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

- **Any application code.** Nothing in this tree launches a build, mints a scoped ECR
  push credential, fetches source, or creates/deletes a per-build Secrets Manager secret.
  That's F7 tickets 3–6, in `deployment-services/application-service/aws/`.
- **A registered IAM principal for the launcher policy.** The document is an output; wiring
  it to a real user/role is an out-of-band step (above), same pattern as `platform-dns`'s
  access key.
- **Per-build resources of any kind** (Secrets Manager secrets, S3 objects, task-definition
  revisions). Everything per-build is created and torn down by the application code this
  skeleton doesn't contain.
- **A backend block.** Left to the operator, matching `platform-dns`.
- **VPC endpoints for ECR/S3/Secrets Manager.** The NAT path works without them; adding
  interface endpoints later is a cost/latency optimization, not a correctness requirement,
  and is left for whoever operates this once real build volume exists.
- **Dropping the per-infra CodeBuild path** in the customer account, or narrowing
  `LaunchpadDeploymentPolicy`'s `codebuild:*` grant. F7's Non-goals are explicit that this
  ships behind a feature flag with the old path kept as fallback — unrelated to this tree
  existing.
