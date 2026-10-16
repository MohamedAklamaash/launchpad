# F7 — Platform-side image builds

**Status:** done, real-AWS verified (tickets 2-9, #129-#136); ticket 10 + the retirement
ticket shipped together — `PLATFORM_BUILD_ENABLED` is gone, the platform builder is the
only build path, and the customer-account CodeBuild code (`aws/codebuild.py`,
`_trigger_build`/`_wait_for_build`, per-infra CodeBuild project/role creation) is deleted
from application-service. Nuke and exit-export still find/report/delete a legacy
per-infra CodeBuild project/role for infrastructures onboarded before this ship — see
"Retiring the old path" below, which this status line now matches. `policy.json` v8
narrowed `codebuild:*` to `BatchGetProjects`/`DeleteProject` for the same reason.
**Depends on:** nothing · **Blocked by:** nothing — the open questions below, and the
"unverified AWS fact" note, were resolved by the real-AWS spike (see "Spike results").

## Problem

`application-service` builds every customer image with AWS CodeBuild **inside the
customer's own AWS account** (`aws/codebuild.py`, driven from
`api/services/application_deployment_service.py:_trigger_build`,
~lines 360–436). It creates a per-infra CodeBuild project + service role on first deploy,
then `start_build`/`wait_for_build` push the result to the infra's ECR.

This breaks on any customer account where CodeBuild concurrency quotas are 0, which
`plan/REAL-AWS-VALIDATION.md` ("Run 1") confirms is not a hypothetical: real account
`403260035234` — used in that run as customer, platform, and DNS account at once — got
`StartBuild: Cannot have more than 0 builds in queue` on every build type, and AWS closed
the quota-increase case **without approving it**. New AWS accounts are not guaranteed to
get this quota raised on request. Every app deploy (F2, F3, H4, host URLs, custom domains —
anything that needs a running app) is blocked on any customer account in this state, and
there is no code-side retry or workaround: it is an AWS-side allow-list.

Owner decision, already made: **build on the platform side.** Launchpad builds images with
platform-owned capacity and pushes to the customer's ECR over the existing cross-account
`LaunchpadDeploymentRole` (`ExternalId` = infra id), instead of asking the customer's
account to run the build.

## Goals

- A customer deploy succeeds regardless of the customer AWS account's CodeBuild (or any
  build-service) quota.
- The build container never holds the customer's AssumeRole credentials, the platform's
  AWS credentials, `JWT_SECRET`/`INTERNAL_API_TOKEN`, or any other tenant's data — only a
  push-only, single-repository, short-lived ECR credential.
- One customer's build cannot read, cache-poison, or deny service to another's (no shared
  build cache across tenants without their own credential scoping it; a bounded
  concurrency/time budget per infra).
- Deploy pipeline semantics (status transitions, `Deployment` history, tag scheme, digest
  pinning, rollback addressability) are preserved or strictly improved — F3 rollback and
  F4 cost tagging must keep working.
- A real-AWS validation path that doesn't depend on a customer-account quota grant.

## Non-goals

- Migrating customer GitHub OAuth tokens from per-user `UserAccount.metadata['github']` to
  a GitHub App installation token. Right answer eventually (narrower scope, revocable,
  no user-token-as-build-credential), but it's a separate feature — this design keeps using
  whatever token `application-service` already resolves for a user, just stops writing it
  into `.git/config` inside the build.
- Encrypting `Application.envs` at rest. Already tracked as unscheduled in
  `plan/README.md`; unrelated to where the build runs, and out of scope here. Build-time
  values a Dockerfile needs are build args, not `Application.envs` (which is runtime env
  injected into the task/pod, never passed to the builder either today or in this design).
- Dropping `codebuild:*` from `policy.json`, or deleting the per-infra CodeBuild
  project/role cleanup code in `nuke_worker.py` / `exit_inventory.py`. Every already-onboarded
  customer's trust/permission policy still grants it, Nuke and exit-export still need to
  find and delete/report on a project that might exist from before this ships. Narrowing
  the grant is a later, separate policy-version bump (see "Retiring the old path").
- ARM/Graviton images. `aws/ecs.py`'s task definitions request `requiresCompatibilities:
  FARGATE` / `launchType: FARGATE` with no `runtimePlatform.cpuArchitecture` — the fleet is
  x86_64 only today. Building ARM images nobody can run is not useful; revisit if/when ECS
  task definitions gain ARM support.
- Cross-tenant build cache. Every tenant's cache lives in their own ECR, scoped by their
  own push credential (see Option decision). A platform-wide shared cache is explicitly
  rejected as a tenant-isolation hazard, not deferred as a future optimization.
- Streaming build logs live over the gateway's `proxy_request` (10s timeout, per
  `plan/F2-runtime-logs.md`'s note on the same constraint) — reuse F2's on-demand-tail
  pattern, not a websocket/SSE rebuild.

## Context & constraints

- `deployment-services/application-service/aws/codebuild.py` — current builder: creates a
  `NO_SOURCE` CodeBuild project per infra, buildspec clones the repo with an optional
  plaintext/Secrets-Manager GitHub token, builds, tags `-latest` / `$IMAGE_TAG` /
  `$APP_NAME-$RESOLVED_SHA`, pushes three tags, exports `RESOLVED_SHA`.
- `api/services/application_deployment_service.py` — `deploy_application` (~56–177) and
  `rollback_application` (~1005–1051) are the state machine. `_trigger_build` (360–436)
  creates the per-infra CodeBuild role (`launchpad-codebuild-role-{infra_id}`, attaches
  AWS-managed `AmazonEC2ContainerRegistryPowerUser` + `CloudWatchLogsFullAccess`) and
  project (`launchpad-build-{infra_id}`), both **inside the customer account**, using the
  full `LaunchpadDeploymentRole` session from `_create_aws_session`.
- `aws/session.py:_build_real_session` — the session the worker already holds is a
  `RefreshableCredentials` wrapper around `sts.assume_role(LaunchpadDeploymentRole,
  ExternalId=infra.id)` using the **platform's own** IAM user keys
  (`app_config.aws_access_key_id/secret_access_key`). This is the credential this design
  must scope down before anything touches a build container — today it is used unscoped
  for everything (CodeBuild project/role creation, ECR push via the project's own role,
  ECS/ALB calls).
- `deployment-services/infrastructure-service/api/cloud_providers/aws/iam_policy/policy.json`
  — `LaunchpadDeploymentPolicy`, version 6. Statement block (~95–115) grants
  `ecr:*`/`codebuild:*`/`logs:*`/`iam:*` etc. account-wide (`Resource: "*"`) under
  `LaunchpadDeploymentRole`. Per `CLAUDE.md`, this is **generated** from this file; the
  heredocs in `app_scripts/create_aws_role.sh` and `docs/IAM_POLICIES.md` are
  `BEGIN/END GENERATED` regions, never hand-edited. A version bump requires
  `NEXT_PUBLIC_LAUNCHPAD_SCRIPT_REF` to move in the same release (`plan/README.md`'s
  standing release action — three past misses already recorded there).
- `deployment-services/application-service/aws/ecr.py` — `get_image_ref` (pins by digest
  when known), `ensure_lifecycle_policy` (protects `-latest`, caps retained per-commit
  images at 10, expires untagged after 7 days). The repository is **tag-mutable**.
- `deployment-services/application-service/api/models/deployment.py` — append-only
  `Deployment` history; `tag_source` (`resolved_sha` / `latest`) decides rollback
  addressability; `image_digest` is the stable pointer when present.
- `deployment-services/application-service/api/models/application.py` — `status` choices
  already include `PUSHING_IMAGE` (unused by the current flow — build and push both
  happen inside one CodeBuild phase today). This design is the first thing that needs it.
- `deployment-services/application-service/api/services/deployment_queue.py` — Redis
  BLPOP work queue (`deployment_queue`), `deployment-worker.service` /
  `start-deployment-worker.sh` run the consumer. `deploy_application` already runs
  entirely inside this worker process, synchronously, including the CodeBuild
  start/poll loop — the platform build call slots into the exact same place.
- `deployment-services/shared/ratelimit/budget.py` — `customer_call_budget(user_id,
  bucket, limit, window)`, Redis-backed, fail-closed on Redis down. Built for F0; reusable
  here for a per-customer build-concurrency/abuse bucket without new infrastructure.
- `deployment-services/infrastructure-service/api/services/nuke_worker.py:396–475` and
  `api/services/exit_inventory.py:219–283` — both independently recompute
  `launchpad-build-{infra_id}` / `launchpad-codebuild-role-{infra_id}` to delete (Nuke) or
  report (exit export, `exit_export.py:169–193`) the per-infra CodeBuild project/role. Both
  must keep working for infras onboarded before this ships.
- `infra/platform-dns/README.md` — the only precedent for platform-owned (not
  customer-account) infrastructure in this repo, and it is explicit that this is a
  **deliberate exception**: own Terraform tree, own AWS account, own credential, never
  held by the provisioning/deploy worker. The same separation rules apply to whatever
  platform builder infra this design adds — it cannot live under
  `deployment-services/infrastructure-service/infra/aws/` (customer-account tree) or
  reuse the provisioning worker's credentials.
- `.github/workflows/ci.yml` `check-terraform` job runs `fmt -check` +
  `init -backend=false && validate` over `deployment-services/infrastructure-service/infra/aws`
  (every module) and `infra/platform-dns` by name — a new platform-builder Terraform tree
  needs its own lines added here or it ships unvalidated.
- `plan/F2-runtime-logs.md` — the house pattern for "platform touches customer-sensitive
  output": no-store on the platform, no `str(exc)` passthrough, owner-only two-step authz,
  bounded window, dedicated audit log + DB row, no gateway rate-limit exemption. This
  design's build-log handling should match it, not re-derive it, since build output is the
  same category of risk (now worse: see Risks).
- **Critical path.** This is the credential-issuance and code-execution path for every
  customer deploy — a mistake here means either deploys silently stop working platform-wide,
  or a customer's Dockerfile gains a way to reach platform credentials or another tenant's
  image. Bar is elevated accordingly: every credential handed to a build container must be
  independently a provable no-wider-than-necessary grant, not "the role already had this."

### The quota fact that gates the recommended option

The account used in `REAL-AWS-VALIDATION.md` Run 1 (`403260035234`) played **customer,
platform, and DNS account at once** — the file says so explicitly. If that is also the
account that will hold platform build capacity going forward, "platform-account CodeBuild"
inherits the exact same 0-quota, increase-denied problem this feature exists to route
around, and is dead on arrival. This has to be checked (`aws service-quotas
list-service-quotas --service-code codebuild --region <region>` plus one real
`StartBuild`) against whichever account is actually proposed to hold the builder, **before**
committing to CodeBuild as the mechanism. See Open questions #1 and the phased plan's first
ticket.

## Options considered

### Option A: Platform-account CodeBuild, one project reused across all builds

**How it works:** a single (or small per-region pool of) CodeBuild project lives in a
platform-owned AWS account, `NO_SOURCE`, same buildspec shape as today minus the parts that
move to the platform side (credential minting, SHA resolution). `start_build` passes
per-build environment variable overrides (repo, SHA, Dockerfile path, a Secrets-Manager
reference to the scoped ECR push credential). The worker polls exactly as it does today.

**Pros**
- Smallest code delta — `aws/codebuild.py` is ~90% reusable; `wait_for_build`,
  `_get_build_error`, the retry/backoff shape all carry over unchanged.
- AWS-managed build fleet: no host patching, no capacity planning beyond quota.
- Native per-build CloudWatch log group, matching the existing operational pattern.

**Cons**
- **Existentially blocked on the same failure mode this feature exists to fix** if the
  platform account's own quota is ever 0 and AWS won't raise it. Needs independent
  confirmation on the specific account before this is viable at all.
- `privilegedMode: true` (needed for `docker build`) means every build container can reach
  the CodeBuild credentials endpoint (`169.254.170.2`) over the bridge network unless the
  buildspec explicitly blocks it — this is true of the current design too and has silently
  relied on the build being the customer's own problem; on the platform side it is now
  *Launchpad's* IAM credentials a hostile Dockerfile could try to reach. Mitigated by a
  bridge/iptables rule run at the top of the buildspec (`DOCKER-USER` chain `DROP` to
  `169.254.170.2` and `169.254.169.254`), not by CodeBuild itself.
- Shared capacity across tenants: one tenant's build-minute spike can delay another's
  unless a per-infra concurrency ceiling is enforced in front of it (see "Credential flow
  and abuse limits").
- Build cost is now a platform expense, not itemized per customer AWS bill — needs its own
  metering table for F4-equivalent attribution (CodeBuild carries no build-level cost
  allocation tag usable the way `app_tags`/`infra_tags` work on customer-account resources).

**Cost/complexity:** S–M (3–5 days). CodeBuild pricing: ~$0.005/build-minute for
`BUILD_GENERAL1_SMALL` on Linux — **verify against current AWS pricing before using this
number for the cost estimate**, it is not re-checked here.

### Option B: Self-hosted BuildKit (or Kaniko) workers on platform ECS/Fargate, one task per build

**How it works:** a platform ECS cluster (Fargate, no EC2 fleet to patch) runs a BuildKit
daemon container per build, launched on demand (`RunTask`, not a long-lived service) with
the repo tarball and a scoped ECR push credential injected via Secrets Manager. BuildKit
builds with `buildctl build --frontend dockerfile.v0 ... --output type=image,push=true`
and the scoped credential; the task's own role carries no AWS permissions beyond reading
its own Secrets Manager entry (the ECR push creds are the *customer's*, scoped, not the
platform's).

**Pros**
- No reliance on a CodeBuild-specific service quota at all — Fargate task-launch capacity
  is the platform's own ECS quota, already relied on for every customer's ECS compute, and
  far less likely to be the same all-zero failure mode (it's the platform's steady-state
  workload, not a cold-start quota nobody has exercised).
- Strongest isolation of the options considered: each build is its own Fargate task (its
  own kernel-level sandbox via Firecracker under Fargate), the task's IAM role is scoped to
  nothing beyond "read one Secrets Manager entry, write its own CloudWatch log stream," and
  BuildKit itself runs unprivileged relative to the host (no `--privileged` Docker-in-Docker
  the way CodeBuild's `privilegedMode` requires — BuildKit's OCI worker uses runc/rootless
  mode). Kaniko is explicitly **not** recommended here: it has no sandboxing of `RUN` steps
  beyond filesystem snapshotting (steps execute as root in the same container, same as a
  Dockerfile always implies, with no stronger isolation than that) and the project's
  maintenance cadence has visibly slowed — picking it over BuildKit buys cache tooling
  familiarity at the cost of a weaker security story for the exact threat (untrusted
  customer Dockerfiles) this design is built around.
- Build cache lives wherever we point it — the registry-cache answer in "Credential flow"
  applies unchanged.
- Full control over timeouts, concurrency, and per-tenant limits — no CodeBuild service
  quota mediates any of it.

**Cons**
- Most implementation work of the options considered: a new platform-owned ECS cluster,
  task definition, a "launch task and stream its result" control loop to replace
  `CodeBuildClient.start_build`/`wait_for_build`, a Secrets-Manager-per-build lifecycle
  (create before launch, `ForceDeleteWithoutRecovery` after).
- Operational surface the team now owns outright: image for the BuildKit worker, its
  patch/update cadence, its own CloudWatch log group naming and retention.
- No AWS-managed "build history" console — `BatchGetBuilds`-equivalent introspection has to
  be built (Fargate `DescribeTasks` + CloudWatch Logs, which is also what Option A's error
  tail already reads, so not wasted work, just more of it upfront).

**Cost/complexity:** L (1.5–2.5 weeks). Fargate compute cost is pay-per-build-second, same
order of magnitude as CodeBuild for equivalent CPU/memory — **verify against current
Fargate pricing**, not re-checked here.

### Option C: GitHub Actions in a platform-owned repo (customer code checked out via a reusable workflow)

**How it works:** a platform GitHub organization hosts a reusable workflow; triggered via
the GitHub API (`workflow_dispatch` or `repository_dispatch`) with inputs naming the
customer repo/SHA, it checks out the customer's code (needs read access to it — a problem
for private repos not already shared with the platform org) and pushes with OIDC-federated,
per-run-scoped AWS credentials (`aws-actions/configure-aws-credentials` + GitHub's OIDC
provider, which *is* a legitimately narrow, short-lived credential pattern).

**Pros**
- GitHub Actions' own compute is free of any AWS build-service quota question entirely.
- OIDC federation is a genuinely strong credential story if adopted — no long-lived key
  anywhere.

**Cons**
- **Does not solve private-repo access cleanly.** Today's private-repo story is a per-user
  GitHub OAuth token (`application-service` reads `user.metadata['github']['token']`,
  per `_trigger_build`, ~413–420). A platform-owned Actions workflow needs either that same
  user token (now flowing through *GitHub's* infrastructure as a workflow secret, a new
  place for it to leak) or a GitHub App installed on every customer's repo — which is this
  design's stated non-goal, done the hard way, as a side effect of picking this option
  rather than as a deliberate choice.
- Multi-tenant isolation of *GitHub's* own runners for untrusted customer Dockerfiles is
  outside Launchpad's control entirely — GitHub-hosted runners are ephemeral VMs, which is
  fine, but Launchpad has no say over or visibility into that boundary the way it does with
  its own Fargate tasks or CodeBuild projects.
- Adds a second, non-AWS "when did it start, how is it going, what failed" integration
  surface (GitHub Actions API) to the existing error-handling/log-tailing code, which is
  built entirely around CloudWatch/boto3 today (`_get_build_error`, F2's design).
- Rate limits and concurrency on GitHub's side (Actions concurrent-job limits per plan tier)
  are yet another quota this doesn't actually remove, just relocates to a different vendor.

**Cost/complexity:** M–L (1–1.5 weeks), plus an unresolved private-repo credential story
that likely grows into its own feature.

### Option D: Remote BuildKit as a long-lived platform service (shared daemon, `buildctl --addr tcp://...`)

**How it works:** one or a small pool of long-lived BuildKit daemons on platform ECS,
builds submitted over the BuildKit gRPC API rather than one task per build.

**Pros**
- No per-build task-launch latency (Option B's biggest latency cost) — builds queue against
  a warm daemon.
- Still get registry-based build cache, same as Option B.

**Cons**
- **Rejected on isolation grounds alone, independent of everything else.** A long-lived
  shared daemon means concurrent builds from different tenants share one process's
  filesystem/cgroup namespace unless BuildKit's own multi-tenant isolation (worker-level
  snapshotting, not a separate sandbox per build) is trusted to hold — which is exactly the
  "no tenant should be able to read another's data" requirement this design is graded on,
  and BuildKit was not built with multi-tenant-adversarial isolation as a primary design
  goal (it assumes a single trusted CI operator's builds, not N untrusted third parties'
  Dockerfiles). Fargate-per-build (Option B) gets the same build-cache win with a real
  process/kernel boundary per build instead.

**Cost/complexity:** not pursued further — isolation gap outweighs the latency win.

## Spike results (real AWS, 2026-10-15) — supersede the Decision below where they conflict

Run in account 403260035234 (`us-east-1`) on a throwaway `lp-builder-spike` Fargate cluster,
pushing into a real customer-infra ECR repo:

1. **Rootless BuildKit does not run on Fargate.** `moby/buildkit:rootless` →
   `rootlesskit: fork/exec /proc/self/exe: operation not permitted` (Fargate blocks the
   user namespaces it needs). Root BuildKit with `--oci-worker-no-process-sandbox` refuses
   outright: `can't enable NoProcessSandbox without Rootless`. **Option B as written
   (BuildKit) is not viable on Fargate.**
2. **Kaniko works on Fargate unprivileged.** `executor:debug`, 1 vCPU / 2 GB: fetched a
   commit-pinned tarball, built a multi-stage Node/React app in ~2 min, pushed to the
   customer repo, `--digest-file` digest matched ECR. **Decision: Option B with Kaniko**,
   using the maintained Chainguard fork (Google archived kaniko in June 2025), pinned by
   digest and mirrored into platform ECR. The isolation boundary is the per-build Fargate
   microVM (one task per build, task role with nothing but its own inputs) — which is why
   Kaniko's lack of an inner RUN sandbox, the reason this doc originally preferred
   BuildKit, does not change the threat model: there is nothing else in that VM to reach.
3. **Scoped push credential confirmed.** AssumeRole(LaunchpadDeploymentRole, ExternalId,
   DurationSeconds=900, session Policy = ECR push on one repository ARN): own-repo
   `InitiateLayerUpload` allowed; other repo, `DeleteRepository`, `ListImages`, S3 all
   `AccessDenied`. No `policy.json` change needed.
4. **ECR token lifetime vs the 900s session:** `expiresAt` reports the nominal 12h; whether
   it still authenticates after the session expires — see the result line appended below.
5. **Source fetch must not happen inside the build container with busybox `wget`**: it
   does not validate TLS (`TLS certificate validation not implemented`), and it would need
   the GitHub token in the container. Revised flow: the worker fetches the tarball (verified
   TLS, app's GitHub token, commit-pinned), uploads it to a per-build S3 object in the
   platform account, and Kaniko reads `--context s3://...`; the task role may read only that
   one object. The GitHub token never enters the build VM.
6. **Build scripts must use `set -o pipefail`** — the spike's first run piped the build
   through `tail` and reported exit 0 on a failed build.

### Decision: how the per-build push credential reaches the build (2026-10-15)

ECS fixes a container's `secrets` at task-definition registration, not per RunTask, so a
per-build Secrets Manager secret would need a task-definition revision per build. Instead
the credential travels like the source: the worker writes
`sources/<uuid4>/docker-config.json` beside `sources/<uuid4>/source.tar.gz`; an init
container in the same (fixed) task definition copies it into a shared task volume and the
Kaniko container (`dependsOn` the init container succeeding) reads it from there. Both
objects are deleted when the build ends (1-day lifecycle as backstop). The build task role
keeps only `s3:GetObject` on the prefix and **no `s3:ListBucket`**: a customer `RUN` step can
reach the task role's credentials, so the per-build key must be an unguessable uuid4 —
one build can't enumerate or fetch another's source or credential. This drops the
Secrets Manager pieces from `infra/platform-builder` (ticket 5 removes them and adds the
init container + volume).

## Decision

**Option B (self-hosted BuildKit on platform Fargate, one task per build)**, with Option A
(platform-account CodeBuild) as the fallback **only if** the platform build account's own
CodeBuild quota is confirmed nonzero before any implementation starts.

Why B over A despite more work: A's core premise — "move the build to an account where the
quota problem doesn't exist" — is not actually verified to be true for AWS CodeBuild; it
assumes the *platform* account doesn't hit the same allow-list wall that triggered this
feature, and the one account referenced in `REAL-AWS-VALIDATION.md` already combined
customer+platform+DNS roles, so that assumption needs an explicit, separate check, not an
inherited one. B's capacity (Fargate task launch) is the same primitive the platform
already depends on for every customer's own ECS compute — it is not a cold, unexercised
quota the way CodeBuild-in-a-fresh-account is. B is also the stronger isolation answer for
the stated critical-path requirement (untrusted customer Dockerfiles, no `--privileged`
Docker-in-Docker, no shared credentials-endpoint reachability by default), which the
problem statement calls out as critical, not incidental.

Practical sequencing given the extra cost of B: **ticket 1 of the phased plan is the quota
check**, cheap and fast. If platform-account CodeBuild quota comes back nonzero and stable,
start with Option A to unblock deploys fastest, and schedule the Option B migration as a
follow-up once the fast path is shipped — the credential-flow design (next section) is
identical either way, so the migration is a swap of "what launches and watches the build,"
not a redesign of how credentials or source checkout work. If the owner wants to skip
straight to B regardless of A's quota outcome (the stronger long-term position), that's a
reasonable call too — flagged in Open questions rather than decided unilaterally here,
since it's a time-to-unblock-deploys vs. build-this-once trade-off the owner is better
placed to make.

## Credential flow

The platform worker (`application-service`'s deployment-queue consumer) already holds a
`RefreshableCredentials` session from `sts.assume_role(LaunchpadDeploymentRole,
ExternalId=infra.id)` (`aws/session.py:_build_real_session`), signed with the platform's own
IAM user keys. That assumed-role session is scoped by `LaunchpadDeploymentPolicy` — today
account-wide `ecr:*` (policy.json ~line 101). **The build container must never see this
session.** What it gets instead:

1. Before minting anything, re-check `application.infrastructure.exited_at` (the existing
   `_abort_if_exited` guard, H2) — pushing into the customer's ECR is a mutation into their
   account and must not proceed past an exit that landed mid-deploy, matching every other
   mutation on this path.
2. The worker calls `sts.assume_role` a **second time**, from the already-assumed
   `LaunchpadDeploymentRole` session (or directly, reusing the same `RoleArn`+`ExternalId`,
   whichever boto3 handles more simply — a session policy applies either way), passing a
   `Policy=` session policy that narrows the effective permissions to exactly:
   - `ecr:GetAuthorizationToken` on `Resource: "*"` (this action has no resource-level
     permissions in AWS's model — unavoidable, matches the `ce:*` precedent already noted
     in `policy.json`'s own comments for Cost Explorer).
   - `ecr:BatchCheckLayerAvailability`, `ecr:InitiateLayerUpload`, `ecr:UploadLayerPart`,
     `ecr:CompleteLayerUpload`, `ecr:PutImage`, `ecr:BatchGetImage` scoped to
     `arn:aws:ecr:{region}:{account_id}:repository/{this infra's one ECR repo name}` —
     never `*`, never another infra's repo even within the same customer AWS account.
   - `DurationSeconds=900` (the STS minimum) — short-lived by construction, re-minted per
     build, never reused across builds or infras.
   - This is a **session policy that narrows an already-granted permission**, not a new
     grant — `LaunchpadDeploymentRole` already has account-wide `ecr:*`, so **no
     `policy.json` version bump is needed for this alone**. Session policies can only
     reduce, never expand, the role's own policy.
3. The worker calls `ecr.get_authorization_token()` on *that* narrowed session and extracts
   just `{username: "AWS", password: <token>, registry: <this repo's registry host>}` —
   never the underlying STS access key/secret/session-token triple. The ECR auth token
   itself is registry-wide in shape, but the *identity* that minted it can only act on the
   one repository ARN above, so a build container that somehow exfiltrated the token still
   can't push/pull any other repository, in this account or another's.
4. That `{username, password}` pair is written to a **per-build** Secrets Manager secret in
   the **platform** account (not the customer's — the build task's own IAM role only needs
   to read platform-side secrets, keeping its permission surface entirely separate from
   anything AWS-account-of-the-customer-shaped), referenced by the build task as a
   `SECRETS_MANAGER`-sourced environment variable. **Never `PLAINTEXT`** — the current
   `codebuild.py` GitHub-token fallback to `PLAINTEXT` (visible in `BatchGetBuilds`/console)
   is a known gap this design does not repeat for either credential.
5. After the build task exits (success or failure), the secret is deleted with
   `ForceDeleteWithoutRecovery=True` — it is single-use and nothing else ever needs it
   again; a 7–30 day recovery window would just be a longer-lived copy of a push credential
   sitting in Secrets Manager for no benefit.
6. **Open, needs a real-AWS check before relying on it in the timeout design:** whether an
   ECR authorization token minted from a 900-second STS session stays valid for its full
   nominal ~12h, or expires when the narrowed session's 900s does. Either answer is
   survivable (worst case: push must happen within ~15 minutes of mint, which is already
   the expected build+push window), but the retry/expiry-handling code should assume the
   pessimistic case until verified.

This pattern — the worker holds the wide credential, mints a narrow one per unit of work,
hands the narrow one (never the wide one) to the thing that does the risky part — mirrors
how `runtime_logs`' cursor design (F2 decision 5) and the general "never give the build
container the AssumeRole creds" instruction in the problem statement both already think
about this class of problem; it is not a new pattern for this codebase.

### What the build task's own IAM role gets (platform-account identity)

Separate from the above — this is the Fargate task role in the *platform* account that
pulls the BuildKit image and runs the build:
- Read exactly one Secrets Manager secret ARN (passed at task launch, not wildcarded).
- Write to its own CloudWatch Logs stream (log group name includes a build id, not an
  infra id — see Observability).
- Nothing else. No `ecr:*`, no `sts:AssumeRole`, no access to the platform's own
  `LaunchpadDeploymentRole`-calling credentials, no network path to any other AWS account.
- Network egress: outbound-only (pull the BuildKit image, fetch the source tarball from
  GitHub, push to the customer's ECR, pull base images the Dockerfile names) through a NAT
  in the platform VPC; **no inbound**, and no route back into the platform's own data-plane
  VPCs/databases — a hostile Dockerfile's `RUN` step has nowhere useful to pivot to even if
  it fully compromises the build container.

## Source checkout

Two problems with the current buildspec that this design fixes rather than carries over
(both already present in `aws/codebuild.py` today, both worse once "the GitHub token" is a
platform-held value rather than the customer's own problem):

- **No `git clone` with an embedded token.** `git clone
  https://$GITHUB_TOKEN@host/...` (current buildspec, `_get_buildspec`) writes the token
  into `.git/config` inside the build context; a Dockerfile with `COPY . .` and no
  `.dockerignore` excluding `.git/` bakes the token straight into an image layer, readable
  by anyone who later pulls that image. Replace with the GitHub REST tarball endpoint
  (`GET /repos/{owner}/{repo}/tarball/{ref}`, token in the `Authorization` header, never in
  a URL or working-tree file) fetched by the platform worker (or the build task, same
  credential either way) and extracted — no `.git` directory ever exists in the build
  context, so there is nothing to leak via `COPY . .`. **Known limitation, explicit
  non-goal note:** the tarball API does not include submodules; this design does not add
  submodule support (the current `git clone --depth 1` doesn't fetch them either without
  `--recurse-submodules`, which it doesn't pass, so this is not a regression).
- **Resolve the SHA before the build starts, not after.** Today, `image_tag` is chosen
  before the build (so the EKS deployer has something to reference) while `RESOLVED_SHA` is
  exported *during* the build from whatever `git checkout` actually landed on — the two can
  diverge on a manual deploy with no `commit_hash`, which is why
  `_pinned_image_tag`/`application_deployment_service.py:244–252` carries an explicit
  fallback-to-latest branch and a comment explaining the EKS-vs-ECS tag divergence this
  caused historically. With the platform resolving checkout, the SHA is knowable up front
  in every case: a webhook deploy already supplies a commit hash; a manual deploy resolves
  `GET /repos/{owner}/{repo}/commits/{branch}` once, before minting any credential or
  starting any build. `image_tag = {app_slug}-{sha}` is then fixed before the build starts,
  for both compute types, with no post-hoc reconciliation step — collapsing the
  `RESOLVED_SHA`-export mechanism and its fallback branch rather than keeping it as a
  second source of truth that platform-side checkout makes largely redundant.
- **Dockerfile path / build context**: unchanged semantics — `application.dockerfile_path`
  / `application.build_context`, resolved exactly as today's buildspec does (`BUILD_CONTEXT`
  prepended unless `DOCKERFILE_PATH` already starts with it).
- **Build args / env**: `Application.envs` (runtime env, Fernet-encrypted at rest per H1)
  is never sent to the builder — true today, stays true. If a future Dockerfile needs a
  genuine *build-time* secret (not in scope for this ship, noted for completeness since the
  problem statement asks about it), the answer is a BuildKit `--secret` mount
  (`RUN --mount=type=secret,id=foo`), which never appears in a layer or in
  `docker history`/image config the way `--build-arg` does — never add a `--build-arg` path
  for anything secret-shaped.
- **Private repos**: continues reading the per-user GitHub OAuth token from
  `user.metadata['github']['token']` exactly as `_trigger_build` does today — this design
  changes *how* that token reaches the source (header to the tarball API, not a URL/
  `.git/config`), not *where* it comes from. Migrating to a GitHub App is the stated
  non-goal.

## Deploy pipeline integration

`deploy_application` keeps its existing shape; `_trigger_build`/`_wait_for_build` are
replaced, not the surrounding steps:

1. **Step 2 (AssumeRole)** unchanged — the worker still needs the full
   `LaunchpadDeploymentRole` session later, for ECS/ALB/EKS calls.
2. **New step, before build**: resolve the SHA (webhook-supplied or one GitHub API call),
   compute `image_tag = {slug}-{sha}` up front — for both ECS and EKS, closing the
   divergence noted above.
3. **`status='BUILDING'`** — set exactly where it is today, before the build call, but now
   the build call targets the platform builder, not a customer-account CodeBuild project.
   No infra-side AssumeRole is spent on this step at all; building needs none of the
   customer's credentials.
4. **New status transition: `status='PUSHING_IMAGE'`** (model field already exists, unused
   today — see Context). Set once the build succeeds and the worker is about to mint the
   scoped ECR credential and push. This makes the previously-invisible "build finished, now
   pushing into your account" phase observable in the dashboard, which it genuinely wasn't
   before (CodeBuild's own `post_build` push was opaque to `Application.status`).
5. **Push + digest capture**: the build task pushes `{slug}-{sha}` (single immutable tag;
   `-latest` can still be pushed for compatibility with anything that reads it, see
   "Retiring the old path") and reports back the pushed digest (`buildctl`'s
   `--output ...,push=true` can emit the digest; alternatively the worker calls
   `ecr.describe_images` right after, same as `ECRClient.get_image_digest` does today).
   `_create_task_definition` / `EKSDeployer` then pin by digest immediately
   (`ecr.get_image_ref(repo, tag, digest)`), not by tag — removing the "tag is mutable,
   digest look-up is best-effort" caveat that `ECRClient.get_image_digest`'s docstring
   currently carries, since the digest is now known at push time rather than looked up
   after the fact.
6. **`status='DEPLOYING'`** — unchanged, same point in the flow (task definition /
   EKS manifest creation).
7. **`Deployment` history (`_record_deployment`)** — unchanged schema; `tag_source` is now
   `resolved_sha` on every row (the `latest`-fallback branch, for a CodeBuild project that
   predates the two-tag buildspec, becomes dead code for every *new* build once this ships,
   but stays correct for reading old rows — it is not retroactively rewritten).
8. **Cancellation**: a deploy cancel (if one exists — not found as a standalone endpoint in
   this codebase today, only `ApplicationRetryDeployView`'s reset) should map to `StopTask`
   (Fargate) rather than CodeBuild's `StopBuild` — same shape, different API. If no
   cancellation endpoint exists today, this design does not add one; it only needs to make
   sure an abandoned build task doesn't run forever (see Timeouts).
9. **Timeouts/retries**: mirror today's `wait_for_build(timeout=1800)` — poll
   `ecs.describe_tasks` on the platform cluster instead of `codebuild.batch_get_builds`,
   same 30-minute ceiling, same "FAILED/FAULT/TIMED_OUT/STOPPED → sanitized error + log
   tail" mapping via `sanitize_deploy_error`. A build task that exceeds the ceiling is
   force-stopped (`StopTask`) by the worker, not left running — platform Fargate capacity is
   shared across all tenants, so an orphaned task is a cost and concurrency leak, not just
   wasted customer time.
10. **Rollback (`rollback_application`)** — unaffected. It already skips the build entirely
    (`# Skips CodeBuild entirely` comment, ~998) and pins to an already-pushed image by tag
    or digest; nothing here changes.

### Logs streaming to the dashboard

Build output now lives in the **platform** account's CloudWatch (the Fargate task's log
group), not the customer's. Reuse F2's already-designed pattern rather than inventing a
second one: on-demand tail (`filter_log_events`), nothing stored beyond CloudWatch's own
retention, bounded window, owner-only two-step authz, a dedicated `customer_call_budget`
bucket (`build_logs`), no gateway rate-limit exemption, and — the one place this *must not*
copy F2's open question — a **settled** answer on redaction, not a deferred one (see Risks):
unlike F2's app-runtime logs, build output is the one surface here that can legitimately
contain Launchpad-side detail (base-image pull errors, BuildKit internals) alongside
customer Dockerfile `RUN` output, so `sanitize_deploy_error`-style type-dispatched handling
applies to the *error summary* shown on `Application.error_message`, while the raw tail
shown by the dedicated log-tail endpoint follows F2's "never `str(exc)`, never stored
beyond CloudWatch retention" rule.

### Retiring the old path — done (ticket 10 + the retirement ticket, shipped together)

What actually happened, superseding the original plan below (kept for history): the
real-AWS validation pass (ticket 9) came back clean, so ticket 10 ("flip the flag on by
default") and the separate retirement ticket this section originally deferred were
combined into one PR rather than staged through an interim default-on period.

- **`PLATFORM_BUILD_ENABLED` is deleted, not defaulted on.** There is no flag and no
  fallback — `application_deployment_service.py`'s build step unconditionally calls
  `aws/platform_builder.py`. `aws/codebuild.py` (the `CodeBuildClient` class,
  `_trigger_build`/`_wait_for_build`, per-infra CodeBuild project/role creation) is
  deleted, along with its dedicated tests.
- **Already-onboarded infras keep their per-infra CodeBuild project/role sitting idle** in
  the customer account — this still does not retroactively delete them (a customer-account
  mutation with no customer-facing benefit). They stay covered by **Nuke**
  (`nuke_worker.py:_delete_codebuild_resources`, unconditional) and by **exit export**
  (`exit_inventory.py` reports them in the customer's own teardown README) — both keep
  working unchanged, with a comment at each call site noting new infras never get one.
- **`policy.json` bumped to v8**, narrowing the account-wide `codebuild:*` grant to
  exactly `codebuild:BatchGetProjects`/`codebuild:DeleteProject` — what Nuke needs to find
  and delete a legacy project, nothing that creates, starts, or updates one.
  `NEXT_PUBLIC_LAUNCHPAD_SCRIPT_REF` must move in the same release per the standing rule
  in `CLAUDE.md`/`plan/README.md`. Because this narrows a *base* statement, every existing
  infrastructure's rendered document changes regardless of compute type —
  `required_version_for` moves to 7 for both `ecs_fargate` and `eks`, so every
  already-onboarded infra will show the stale-policy flag until it re-runs the refresh
  script, even though the change only narrows permissions. `iam:*` was checked (grepped
  every `iam.` call in both services) and left unchanged — it is a shared blanket grant
  Terraform still needs for ECS/EKS role creation, not a CodeBuild-only grant with
  anything narrow to carve out.
- `aws/codebuild.py` originally reserved below for "a later, separate ticket" (narrowing
  `iam:PassRole`/role-creation) found nothing to narrow: there never was a CodeBuild-only
  IAM statement, only the single blanket `iam:*` the rest of the policy already explains.

## Platform infra needed

New Terraform tree, following the `infra/platform-dns/` precedent exactly (own account, own
credential, never held by a worker that also holds customer-account-reachable secrets):

`infra/platform-builder/` (repo root, sibling to `infra/platform-dns/`, **not** under
`deployment-services/infrastructure-service/infra/aws/` — that tree provisions the
*customer's* account; this provisions Launchpad's own, same distinction
`infra/platform-dns/README.md` draws for itself).

Contents:
- A dedicated platform-builder AWS account (or at minimum a dedicated VPC/subnet set in
  whatever platform account is chosen) — isolated from the account that will eventually
  hold `LAUNCHPAD_PLATFORM_PRINCIPAL_ARN`'s trust-policy principal if that's a separate
  concern; isolated at minimum from any account holding customer data.
- VPC: private subnets + NAT (outbound only, as described in Credential flow), no inbound.
- ECS cluster (Fargate) + task definition for the BuildKit worker image.
- IAM: the task execution role (pull the BuildKit image, write its own log stream) and the
  task role (read exactly the one Secrets Manager secret passed at launch) — **not** the
  same role, matching ECS's own execution-role/task-role split that `aws/ecs.py` already
  uses for customer tasks.
- CloudWatch log group(s) for build output, named per-build (include the build id, not the
  infra id, so retention/access-control doesn't key on tenant identity the way F2's log
  groups deliberately do for *runtime* logs — build logs are shorter-lived and the access
  check is already keyed off `Application`/`Deployment`, not the log group name).
- A registry-cache repository convention: `--cache-to/from
  type=registry,ref={customer_ecr_repo}:{slug}-buildcache`, pushed with the **same** scoped
  per-infra credential as the image itself — this is why cache lives in the *customer's*
  ECR, never a platform-side cache registry: it means no cross-tenant cache ever exists to
  poison or read, and no new credential shape is needed for cache vs. image pushes. Add a
  protective ECR lifecycle rule for the `{slug}-buildcache` tag (mirroring
  `ECRClient._lifecycle_policy()`'s rule 1 for `-latest`) so rule 2's count-based expiry
  doesn't quietly evict the cache layer out from under every subsequent build.
- `.github/workflows/ci.yml`'s `check-terraform` job gets two new lines (`terraform fmt
  -check -recursive infra/platform-builder` and an `init -backend=false && validate` loop
  over it, same shape as the existing `infra/platform-dns` lines) — this tree does not
  validate itself for free.

### Observability

- Per-build structured log line (build id, infra id, app id, duration, outcome) emitted by
  the worker — not customer Dockerfile output, just the control-plane record of "a build
  ran." This is what cost/abuse dashboards key on.
- Metric: build queue depth, build duration p50/p95, build failure rate, broken out by
  failure class (checkout, build, push, timeout) the same way `_get_build_error` already
  classifies Docker Hub rate-limiting specially.
- Alert: build failure rate above a threshold (likely signal of a platform-side capacity or
  config problem, distinct from a customer's own broken Dockerfile) and build-task queue
  depth sustained above the Fargate cluster's steady-state capacity (early warning before
  customers see timeouts).
- **F4 cost-attribution gap**: CodeBuild resources can carry `launchpad:app`/
  `launchpad:infra` tags today (`aws/tags.py`, `infra_tags`) because they live in the
  customer's own account and get swept into that account's Cost Explorer view. A platform
  Fargate task does not — its cost is a platform expense, not a line item on the customer's
  AWS bill. Replace with platform-side metering: a small table (build id → infra id → app
  id → duration → approximate cost) populated from the per-build structured log above, so
  internal reporting doesn't lose per-customer build-cost visibility just because the spend
  moved off the customer's own bill. This is new scope beyond what F4 shipped and should be
  called out to whoever owns internal cost reporting, not silently absorbed.

### Quotas / abuse limits per customer

- **Concurrency**: a per-infra budget via `customer_call_budget`-style Redis counter (reuse
  the existing primitive, new bucket `builds_concurrent`) capping simultaneous in-flight
  builds per infra — e.g. 1–2 concurrent, since a single infra rarely needs to build many
  apps at once and this bounds one tenant's blast radius on shared Fargate capacity.
- **Rate**: a per-user/per-infra build-starts-per-window budget (same primitive, bucket
  `builds_started`), so a misconfigured webhook retry storm or a scripted abuse attempt
  can't monopolize the builder fleet — fail-closed on Redis down, matching every other F0
  bucket's posture, since an unavailable limiter here is directly a cost and availability
  risk to every other tenant, not just this one.
- **Timeout**: the existing 1800s ceiling (ticket item above), enforced by the worker
  force-stopping the task, not relying on the task to self-terminate.
- **Resource ceiling**: a fixed CPU/memory shape for the BuildKit task (no per-customer
  tuning in v1) bounds per-build cost and prevents one customer's build from starving
  Fargate capacity by requesting an outsized task.

### Cost estimate

Order-of-magnitude only, **every number below needs verification against current AWS
pricing before being used in a real budget**:
- Fargate (Option B): roughly comparable to CodeBuild `BUILD_GENERAL1_SMALL` per build-
  minute for equivalent vCPU/memory — both options are "pay for build duration," the cost
  delta between A and B is secondary to the quota-availability question that actually
  decided this.
- NAT gateway data processing + hourly cost for the platform VPC — small relative to build
  compute unless build images are unusually large.
- Secrets Manager: per-secret-per-month pricing is irrelevant here since secrets are
  created and force-deleted within minutes of each other — effectively the per-API-call
  cost, negligible at any plausible build volume.
- ECR storage for the registry build cache adds modestly to the *customer's* own ECR
  storage bill (the cache tag lives in their repo) — bounded by the new protective
  lifecycle rule above, not unbounded.

## Risks & mitigations

- **Platform account's own CodeBuild quota could be 0** (if Option A is chosen as the fast
  path). *Likelihood*: unknown, exactly the open question this design flags first.
  *Impact*: total — the fast path doesn't ship. *Mitigation*: ticket 1 is the quota check,
  before any other implementation work; Option B has no dependency on this quota at all.
- **A hostile customer Dockerfile reaches the CodeBuild/Fargate metadata credentials
  endpoint.** *Likelihood*: moderate — this is a known, documented attack surface for any
  privileged container build, not a hypothetical. *Impact*: high if realized (platform
  credential exposure). *Mitigation*: Option B's unprivileged BuildKit worker avoids the
  Docker-in-Docker privileged-mode requirement entirely; if Option A is used as an interim
  step, the buildspec must block `169.254.170.2`/`169.254.169.254` via `iptables -I
  DOCKER-USER ... -j DROP` before any `docker build` runs, and this must be tested (a build
  that tries to `curl` those addresses and observes the drop) as part of validation, not
  assumed to work from the rule's presence alone.
- **The scoped ECR session policy is wrong and ends up broader than one repository.**
  *Likelihood*: low if written and reviewed carefully, but this is exactly the kind of
  "looks right, isn't" IAM mistake the project's own `plan/README.md` security-pre-review
  rule exists for. *Impact*: a build for infra A could push/pull into infra B's repository
  in the same customer account, or (if the resource ARN is malformed) another customer's
  account entirely. *Mitigation*: this slice gets a security pre-review before
  implementation (same bar as F2's two BLOCKs), plus a concrete test: mint the scoped
  session in a test and assert `PutImage` succeeds against the target repo and fails
  (`AccessDenied`) against a second, same-account repo and against a second mock account's
  repo.
- **Build logs now live in the platform account, not the customer's** — a direct regression
  against the "nothing of yours lives with us" posture that F2 (runtime logs) was
  deliberately built around and left as the *last*, highest-risk feature specifically
  because it's the first time Launchpad reads customer output. This design reopens that
  question for build output. *Likelihood*: certain — it's a structural consequence of where
  the build runs, not a bug. *Impact*: moderate — build output is less likely to contain
  end-user PII than runtime request logs, but can still contain dependency-registry
  credentials echoed by a misconfigured `RUN npm install`, environment leakage from a
  verbose build script, etc. *Mitigation*: short CloudWatch retention on the build log
  group (days, not the customer-account default), F2's no-store/no-`str(exc)` discipline
  applied to anything surfaced through `Application.error_message` or the dashboard tail
  endpoint, and an explicit owner decision (Open questions) on whether this trade-off is
  accepted as-is or needs its own redaction pass before ship — do not let it default silently
  the way F2 flagged redaction as "a decision, not a default."
- **F4 cost attribution breaks for the build line item** once builds move off the customer's
  AWS bill. *Likelihood*: certain. *Impact*: low-to-moderate — internal visibility, not a
  customer-facing correctness bug (the customer's bill actually looks *better*, not worse).
  *Mitigation*: the platform-side metering table described under Observability; flagged
  explicitly to whoever owns F4/cost reporting rather than silently dropped.
- **Rollback path regression.** *Likelihood*: low — rollback does not call the builder at
  all (confirmed in code, ~998). *Impact*: n/a if the likelihood holds. *Mitigation*: an
  explicit regression test asserting `rollback_application` never calls the new build path,
  so a future refactor can't accidentally wire it in.
- **Shared Fargate capacity lets one tenant degrade another's build latency.**
  *Likelihood*: moderate without the concurrency/rate limits described above; low with
  them. *Impact*: moderate — delayed deploys, not data exposure. *Mitigation*: the
  per-infra `builds_concurrent`/`builds_started` Redis buckets, fail-closed, before this
  ships to more than a pilot set of customers.
- **Data loss**: none of this touches a durable store beyond `Deployment` history (already
  append-only, unaffected schema) and the new platform-side build-cost metering table
  (new, no migration risk since nothing reads it yet). No rollback-of-data-migration concern
  applies to this feature.
- **Rollback path for the feature flag itself**: flipping `PLATFORM_BUILD_ENABLED` off mid-
  rollout must leave in-flight builds on whichever path they started on (don't switch a
  build that's already running) — the flag check belongs at the top of
  `_trigger_build`'s replacement, read once per deploy attempt, not polled mid-build.

## Execution plan

Each step is sized to be one PR. Security-pre-review gates are called out explicitly,
matching the project's established practice (`plan/README.md`'s "run a pre-review on each
slice" rule, which caught two BLOCKs on F2 alone).

1. **Quota check (no code).** Confirm, against the actual account proposed to hold platform
   build capacity, that CodeBuild concurrency quotas are nonzero (`aws service-quotas
   list-service-quotas --service-code codebuild --region <region>` + one real `StartBuild`
   against a throwaway `NO_SOURCE` project). This decides whether ticket 2 starts with
   Option A or jumps straight to Option B's Fargate design. **Blocking** on everything else
   if the owner wants A as the fast path; informational only if B is chosen regardless.
2. **Platform-builder Terraform skeleton** (`infra/platform-builder/`): VPC, NAT, ECS
   cluster (Option B) or the platform CodeBuild project (Option A), IAM roles per the
   Credential-flow split above, CloudWatch log groups, no application code yet. Add the two
   new lines to `.github/workflows/ci.yml`'s `check-terraform` job. Tests: `terraform fmt
   -check` + `validate` (the same CI job this PR adds to, run locally first).
3. **Scoped-credential minting**, isolated from the rest of the pipeline: a new
   `aws/platform_builder.py` (or similar) exposing `mint_push_credential(infrastructure,
   ecr_repository_arn) -> {username, password, registry}`, built on the existing
   `_create_aws_session`/`RefreshableCredentials` machinery but adding the session-policy
   narrowing step. **Security pre-review required before merge** — this is the credential-
   scoping slice called out as a risk above. Tests: the two-repo/two-account `PutImage`
   allow/deny assertions described in Risks, run against mock STS (`botocore.Stubber`,
   matching the project's existing mock-first convention) since no real multi-account setup
   exists yet; a real-AWS version of the same assertion is appended to
   `plan/REAL-AWS-VALIDATION.md` for later.
4. **Source checkout rework**: tarball-based fetch (`aws/github_source.py` or similar)
   replacing the `git clone`-with-embedded-token pattern, plus upfront SHA resolution
   (webhook-supplied or one `GET /commits/{branch}` call) before `_trigger_build`'s
   replacement is invoked. Tests: tarball fetch with a valid/invalid token, SHA resolution
   for branch vs. explicit-commit deploys, and a regression test asserting no `.git`
   directory or token-bearing string appears in the extracted build context.
5. **Builder client** (`aws/platform_builder.py` build-launch half, or Option A's thin
   CodeBuild-project-reuse wrapper): launch, poll, timeout, cancel-on-timeout, log-tail
   error summarization (`sanitize_deploy_error`-compatible), mirroring
   `CodeBuildClient.start_build`/`wait_for_build`/`_get_build_error`'s existing shape and
   tests one-for-one. Tests: success, failure with log tail, timeout force-stop, Docker-Hub-
   rate-limit special-case message (carry the existing check over).
6. **Wire into `application_deployment_service.py`**: replace `_trigger_build`/
   `_wait_for_build`'s bodies behind `PLATFORM_BUILD_ENABLED`, add the `PUSHING_IMAGE`
   status transition, switch `_record_deployment`/task-definition creation to pin by the
   now-known-at-push-time digest. Tests: full `deploy_application` happy path against mocks
   (reuse whatever `Stubber`/mock-session fixtures the existing deploy tests use), explicit
   regression test that `rollback_application` still never touches the builder, flag-off
   path still exercises the original CodeBuild branch unchanged.
7. **Abuse limits**: `builds_concurrent`/`builds_started` Redis buckets via
   `customer_call_budget`, wired at the top of the (new) `_trigger_build` replacement.
   Tests: concurrency cap enforced, rate cap enforced, Redis-down fail-closed (503/retry
   signal propagated as a deploy failure with a clear message, not a silent pass-through).
8. **Dashboard build-log tail**, following F2's endpoint shape exactly (owner-only authz,
   bounded window, dedicated rate-limit bucket, no-store, audit log row).
   **Security pre-review required** — same category of customer-sensitive-output exposure
   F2 was pre-reviewed for twice. Tests: authz (owner vs. invited ADMIN vs. stranger),
   bounded window enforcement, no raw exception ever reaches the response body.
9. **Real-AWS validation pass**, appended to `plan/REAL-AWS-VALIDATION.md`: deploy against
   the new path end-to-end on a real customer account (ideally one with zero CodeBuild
   quota, to directly prove the fix), confirm digest pinning, confirm rollback still works
   against an image built this way, confirm Nuke/exit-export still correctly find-and-report
   the *old* per-infra CodeBuild project if one happens to exist on that account from before
   this shipped, confirm the ECR-token-lifetime-vs-session-lifetime open question from
   Credential flow #6.
10. **Flip `PLATFORM_BUILD_ENABLED` on by default**, once 9 is green. Old path stays in the
    codebase (Non-goals) pending its own separate retirement ticket.

### Real-AWS validation checklist (appended to `plan/REAL-AWS-VALIDATION.md` verbatim by ticket 9)

- [ ] Platform build account's CodeBuild quota check (ticket 1) result recorded, with the
      actual numbers, not just pass/fail.
- [ ] A customer account with confirmed 0 CodeBuild quota completes a full deploy via the
      new path (this is the headline proof the feature works).
- [ ] Scoped ECR session policy: real `AssumeRole` + `PutImage` succeeds against the target
      repo and fails `AccessDenied` against a second repo in the same account.
- [ ] ECR auth token lifetime when minted from a 900s STS session — confirmed against real
      AWS, not assumed.
- [ ] Pushed image digest matches what the task definition/EKS manifest is pinned to
      (no tag-mutability gap).
- [ ] Rollback to an image built via the new path succeeds.
- [ ] Nuke against an infra with both an old (idle, never-built) per-infra CodeBuild
      project and new-path deploy history still finds and deletes the old project.
- [ ] Build-task timeout force-stop actually stops the task (not just marks it failed while
      it keeps running and billing).
- [ ] `builds_concurrent`/`builds_started` limits enforced against real concurrent deploy
      attempts, not just mocked Redis.
- [ ] Build log tail endpoint: confirm CloudWatch retention setting actually applies (short
      retention, not the account default).

## Open questions

1. **Which AWS account holds platform build capacity, and has its CodeBuild quota actually
   been checked?** This gates the Option A vs. B sequencing decision in the Decision
   section. Needs an answer before ticket 1 can close.
2. **Does the owner want to skip Option A entirely and go straight to Option B**, accepting
   the longer time-to-ship in exchange for not building (and later migrating off) an interim
   CodeBuild-based path? The Decision section defaults to "check quota first, use A if
   viable" but this is a reasonable owner call either way.
3. **Is platform-owned build output (Dockerfile stdout, dependency-fetch logs) acceptable
   to the "nothing of yours lives with us" positioning**, or does it need its own
   customer-facing data-handling disclosure (the way F2 commits to "an explicit
   data-handling statement in customer-facing docs" for runtime logs)? This design assumes
   short retention + no-store-beyond-CloudWatch is sufficient but does not assume the
   marketing/legal answer.
4. **Does GitHub Actions concurrency or org-level API rate limiting become a real
   constraint at projected build volume**, making Option C worth reconsidering later even
   though it's not the pick here? Not urgent, but worth a number before ruling it out
   permanently rather than just for this ship.
5. **Who owns the new platform-side build-cost metering table** (the F4-attribution
   replacement under Observability) — is this internal finance tooling, or does it need to
   feed back into any customer-facing cost view? Affects whether ticket scope for
   Observability includes a dashboard surface or just a database table.
6. **Should the per-infra build concurrency/rate limits be configurable per customer** (e.g.
   a paid tier gets higher concurrency), or is a single global default acceptable for v1?
   Affects whether `customer_call_budget`'s `limit`/`window` are hardcoded constants or need
   a per-infra override column.
