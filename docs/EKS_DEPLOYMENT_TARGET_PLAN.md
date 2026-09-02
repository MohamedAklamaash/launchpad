# EKS as a second deployment target (user picks Fargate or Kubernetes)

## Context

Launchpad today provisions exactly one shape of compute into a customer's AWS account: an ECS Fargate cluster behind a shared ALB, with apps deployed as task definitions. Customers who already run Kubernetes, or who want k8s-native primitives, have no path onto the platform.

This change makes the compute target a **choice made at infrastructure creation** — `ecs_fargate` or `eks` — and teaches the platform to provision an EKS cluster and deploy applications onto it on the customer's behalf, through the same cross-account AssumeRole it already uses.

Locked product decisions (already made, not open):
- **EKS Auto Mode** — AWS manages nodes, autoscaling, load balancing, storage, upgrades. Not managed node groups, not Fargate profiles.
- **One cluster per `Infrastructure`** (per customer AWS account).
- **Direct Kubernetes API access from the deploy worker** — assume the cross-account role, mint an EKS token, apply Deployment/Service/Ingress. No Helm, no GitOps.
- **New infrastructures only.** `compute_type` is chosen at creation and immutable. Existing ECS infras are untouched; there is no migration path in scope.

Intended outcome: a user picks "Kubernetes" in the create-infrastructure wizard, runs the same onboarding script, and gets an ACTIVE infra they can deploy to at `http://{alb_dns}/{slug}` — byte-identical URL scheme, status lifecycle, and failure semantics to ECS.

> **This plan has already been through a security pre-review, which returned BLOCK on the first draft.** Every must-fix is folded into the design below rather than appended — see the *Security review* section for the finding-by-finding mapping. Do not implement an earlier version of this design.

---

## Goals

- `compute_type` typed end-to-end: model → serializer → gateway body → frontend, immutable after create, gated by an `EKS_ENABLED` server-side flag.
- EKS infra provisions cluster + VPC + ECR + IAM into the customer account via the existing terraform worker, reaching `ACTIVE` with a populated `alb_dns`, exactly like ECS.
- App deploy on EKS reuses the CodeBuild pipeline unchanged and produces the same `deployment_url` scheme, status lifecycle, and failure-unwind semantics.
- Destroy works: controller-created ALBs/ENIs (which live outside Terraform state) are reaped **before** `terraform destroy`, on all three teardown entry points.
- Full mock/dev parity: `LAUNCHPAD_MOCK=1` onboarding, `_mock_provision`, MockSession-style deploy tests, paired mock/real hard-gate assertions preserved at every seam.
- Zero new binaries in any Docker image — pure-Python cluster access (no `kubectl`, no `helm`, no `aws` CLI).
- Every migration additive; existing ECS rows default to `ecs_fargate`.

## Non-goals

- No migration of existing ECS infras to EKS; no `compute_type` mutation ever.
- No Helm, no GitOps, no managed node groups, no Fargate profiles.
- No DNS/TLS/host-based routing; HTTP on the shared ALB stays as-is.
- No HPA/autoscaling config, no multi-environment-per-infra work (the 1:1 `Environment` assumption stands).
- No log-viewer UI (`Environment.logs` stays unserialized — noted as a fast-follow).
- No refresh-policy UI resurrection — new-infras-only means bootstrap always runs fresh.

---

## Approach

**A. URL scheme — keep the nginx sidecar.** Pod = app container + nginx sidecar reusing `_generate_nginx_config` (mounted via ConfigMap instead of ECS command injection). Service targets nginx; ALB Ingress forwards `/{slug}` and `/{slug}/*` intact; nginx strips the prefix and injects `X-Forwarded-Prefix`. *ALB Ingress cannot rewrite paths* — the `rewrite-target` annotation is nginx-ingress-only — so the sidecar is load-bearing, not vestigial. Host-based routing was rejected: it needs a domain, Route53, and wildcard certs the platform does not have.

**B. `alb_dns` at provision time via a bootstrap Ingress.** Under EKS the ALB is created lazily by the Load Balancer Controller on Ingress reconcile, so it cannot be a Terraform output. A post-apply bootstrap step creates the cluster-scoped IngressClass + IngressClassParams (`scheme: internet-facing`, group `launchpad-{infra_id[:8]}`), a `launchpad-bootstrap` namespace, and a placeholder default-backend Ingress; polls `status.loadBalancer.ingress[0].hostname` (~3–5 min) and writes `Environment.alb_dns`. This keeps `_validate_infrastructure`, the `environment.updated` payload, and "ACTIVE ⇒ deployable" working unmodified. Backfilling at first deploy was rejected — it would make `alb_dns` null-tolerant everywhere and fire `environment.updated` twice with different shapes.

**C. Namespace per application, not per infrastructure.** *(Security must-fix C4 — this overrides the obvious design.)* `Application.unique_together` is `('user','infrastructure','name')` and the slug is derived from `name` at deploy time, so two users on a shared infra can produce the same slug. Under namespace-per-infra that is the same `Service/api` and `Ingress/api` in the same namespace — a silent cross-user overwrite, not a conflict error. Every isolation primitive needed (NetworkPolicy, ResourceQuota, PSA labels, RBAC) is namespace-scoped and per-app namespaces cost nothing. Namespace = `app-{slug}`; the ALB Ingress `group.name` stays shared per infra so the URL scheme is unchanged.

**D. K8s resource handles in `Application.runtime_refs` (JSONField).** `{"runtime": "eks", "namespace", "deployment", "service", "ingress", "configmap"}`. Cleanup jobs gain a `runtime` key; absent key ⇒ legacy ECS ARN shape, so in-flight jobs stay valid across deploy. One additive nullable column beats four new k8s-named columns.

**E. Immutable per-deploy image tags.** Deployer computes `image_tag = f"{slug}-{commit_hash[:12]}"` (or `{slug}-{uuid4().hex[:12]}` for manual deploys), passed via `environmentVariablesOverride`; the buildspec pushes `$ECR_URL:$IMAGE_TAG` **and** the existing `$APP_NAME-latest`. ECS keeps consuming `-latest` (zero behavior change); EKS references the unique tag, so an `image:` update always rolls pods and old tags are natural rollback points. The ECR repo stays `MUTABLE`: the buildspec re-pushes `$APP_NAME-latest` on every
build for ECS, and an immutable repo rejects that. Immutability comes from the per-deploy
tag never being reused, not from the repository setting.

### Non-obvious mechanics — get these right

**STS session length (two-sided fix).** `authenticate.py` currently calls AssumeRole with no `DurationSeconds` → 1h, and infrastructure-service has **no** credential refresh (unlike application-service, which has `RefreshableCredentials`). EKS cluster creation plus retries can exceed an hour mid-apply. Fix both sides: `create_aws_role.sh` passes `--max-session-duration 7200` on `create-role`, and `authenticate_infrastructure` passes `DurationSeconds=7200` with a logged fallback retry at 3600 on `ValidationError` (**log the exception type, not its text — it contains the role ARN**). Workers mint fresh creds at the start of every provision/destroy dispatch. Redis dedup lock TTL 3600 → 7200, env-overridable.

**Credentials must stop being persisted.** There are **two** writers of plaintext STS creds into `Infrastructure.metadata`: `infrastructure-service/api/cloud_providers/aws/authenticate.py:60-70` and `application-service/aws/session.py:96-123` (`_assume_role_raw`, which re-persists on *every* `RefreshableCredentials` refresh; `_build_real_session:131` seeds from that blob). `_redact_metadata` protects only the API boundary, never the DB row, backups, or replicas. Doubling session lifetime without removing persistence is pure downside — drop the write at **both** sites (keep `is_cloud_authenticated`) and delete existing stored creds in the same migration.

**Cluster endpoint is not left at the default.** EKS defaults to `endpoint_public_access = true, public_access_cidrs = ["0.0.0.0/0"]`. With `authentication_mode = "API"` and a cluster-admin access entry, that puts internet↔cluster-admin behind one static IAM secret. `public_access_cidrs` comes from platform config, and provisioning **hard-refuses** if the list is empty or contains `0.0.0.0/0`. `enabled_cluster_log_types = ["api","audit","authenticator"]`.

**NetworkPolicy is silently unenforced on Auto Mode by default.** Policies are accepted by the API server and do nothing unless the `amazon-vpc-cni` ConfigMap in `kube-system` sets `enable-network-policy-controller: "true"` **and** the NodeClass sets `spec.networkPolicy`. Both go in the provisioning path, and the test must assert a *denied connection actually fails* — a manifest-shape test passes against an unenforced policy.

**Pods get no AWS identity — state it as an invariant.** Auto Mode pins the IMDSv2 hop limit to 1 (unchangeable), so pods cannot reach `169.254.169.254`. With no IRSA and no ServiceAccount role annotations, workload pods have zero AWS identity — matching today's ECS posture (`aws/ecs.py` sets only `executionRoleArn`; no `taskRoleArn` exists anywhere in the repo). Make this a checked invariant, not an accident.

**`_generate_config` is the repo's top trust boundary.** It interpolates `vars.get(...)` straight into HCL and `_exec_tf` runs terraform with the worker's full environ (`JWT_SECRET`, `INTERNAL_API_TOKEN`, platform AWS keys); `metadata` is caller-settable at create. The EKS builder must select on the **`compute_type` column only**, never `vars.get('compute_type')`, or `metadata` becomes a channel that bypasses `EKS_ENABLED`. `cluster_version` is exact-match allowlisted (`{"1.29","1.30","1.31"}`). Add the containing control while here: a subprocess `env=` allowlist (PATH/HOME/TF_*/customer creds only).

**Destroy ordering is first-class, and there are three entry points.** EKS branch of `_pre_destroy_cleanup`: fresh creds → k8s client → delete Ingresses and LoadBalancer-type Services **in Launchpad-created namespaces only** (never `kube-system`) → poll ELBv2 until reaped (~8 min timeout) → boto3 fallback → existing ENI/lock cleanup → `terraform destroy`. The fallback must be fail-*safe*: trigger only on a **positive** signal (`eks:DescribeCluster` → `ResourceNotFoundException`, or status `DELETED`/`FAILED`) — never on a k8s API timeout — and match on **both** the `elbv2.k8s.aws/cluster` tag (forgeable: cluster names derive from the infra id, and any principal in the account can set that tag via `alb.ingress.kubernetes.io/tags`) **and** `VpcId` equal to the infra's terraform-output VPC id (not forgeable, terraform-owned per infra). For SGs additionally require zero remaining ENI attachments. Refuse the batch if more than N match; log the full match set before deleting.

The three entry points that must all run this: (1) the destroy dispatch, (2) the **inline rollback-destroy** on permanent provision error in `terraform_worker.py`, and (3) `InfrastructureService.delete_infrastructure`, which today deletes the env + row with **no terraform run** when status is `ERROR`/`DESTROYED`/`PENDING`. Under ECS a failed apply left little behind; under EKS it leaves a billed, internet-reachable control plane with a cluster-admin access entry for a role Launchpad has just forgotten about.

**Access entries are split.** Cluster-admin (`AmazonEKSClusterAdminPolicy`) for the provisioning identity; a second namespace-scoped least-privilege entry for the deploy worker. Cheap now, expensive to retrofit.

**Token minting.** Presigned SigV4 `GetCallerIdentity` with `x-k8s-aws-id: {cluster_name}`, base64url, `k8s-aws-v1.` prefix — **`expires_in=60`, not 900** (`aws eks get-token` signs with `X-Amz-Expires=60`; the ~14-minute figure is the returned `expirationTimestamp`, not the presign window). The presigned URL is a bearer credential for cluster-admin. **`x-k8s-aws-id` must appear in `X-Amz-SignedHeaders`** — if merely sent and not signed, the token replays against any cluster the role can reach. Assert both in a unit test on the generated URL. Never log the `kubernetes.client.Configuration` object (`to_debug_report()` and its repr include `api_key`); set `Configuration.debug = False`; add a log filter redacting `k8s-aws-v1.` and `X-Amz-Signature`.

**`EKS_ENABLED` must be checked at provision dispatch, not only at create.** The reaper and startup recovery re-enqueue provisioning independently of the create-time check, so an infra created while the flag was on keeps re-provisioning as EKS after it is turned off.

### User-visible behavior change

Slug uniqueness moves from `('user','infrastructure','name')` to being scoped to `infrastructure` alone. Two users on a shared infrastructure can no longer both name an app `api`. This is required (it is what makes namespace-per-app and cleanup unambiguous), and the migration must detect and report existing duplicates before the constraint lands. The slug helper is currently reimplemented independently in `application_cleanup_service.py:108` — collapse to one shared helper, or cleanup can compute a different slug than deploy and delete the wrong thing.

### Phases

Each ships alone with ECS green and the flag off until Phase 5.

1. **Plumbing** — enum, `Infrastructure.compute_type` + mirror column + migrations, serializers/types, gateway body, producer payloads + consumer, `EKS_ENABLED` rejection at create *and* dispatch.
2. **Onboarding + STS** — scoped EKS policy statements (compute_type-gated), max-session-duration on both script branches, `DurationSeconds`, fresh-creds-per-dispatch, **drop both credential writers + purge migration**, dedup TTL bump.
3. **Provision/destroy** — `modules/eks`, vpc tag variable, config builders, `_save_outputs` map, `shared/k8s/*`, `eks_bootstrap.py`, CNI network-policy enablement, pre-destroy k8s cleanup across all three teardown paths, mock fixtures branch, transient-error patterns.
4. **Deploy** — `IMAGE_TAG` buildspec, `EKSDeployer` + `aws/eks.py`, per-app namespace with quota/PSA/NetworkPolicy, `runtime_refs` + migration, slug-uniqueness migration, cleanup/queue/worker runtime branch, retry/delete snapshot fix, serializer gating, `mock_k8s.py`.
5. **Surface** — frontend selector + conditional CPU/memory UI, notification buckets, docs, CI terraform job, flip `EKS_ENABLED` default.

---

## File-level breakdown

### `deployment-services/shared/`
- `enums/orchestrator.py` — **new**: `ComputeType(models.TextChoices)` with `ECS_FARGATE = "ecs_fargate"`, `EKS = "eks"` (one-enum-per-file convention).
- `k8s/token.py` — **new**: presigned STS EKS token, `expires_in=60`, `x-k8s-aws-id` in signed headers.
- `k8s/client.py` — **new**: build `kubernetes.client.ApiClient` from cluster endpoint/CA + token; paired mock/real hard-gate mirroring `aws/session.py`; `debug = False`, no Configuration logging.

### `deployment-services/infrastructure-service/`
- `api/models/infrastructure.py` + migration — `compute_type` CharField, default `ecs_fargate`.
- `api/serializers/infrastructure.py`, `api/types/infrastructure.py` — accept on create, reject on update.
- `api/services/infrastructure.py` — `ALLOWED_CREATE_FIELDS += compute_type` with enum validation; reject `eks` unless `settings.EKS_ENABLED`; **EKS branch of `delete_infrastructure`'s no-terraform path** (H4).
- `api/cloud_providers/aws/authenticate.py` — `DurationSeconds=7200` + 3600 fallback (log type, not text); **remove the plaintext credential write** (`:60-70`); keep the mock/real paired assertion.
- `api/services/terraform_worker.py` — the primary seam. Split `_generate_config` (`:186-258`) → `_generate_config_ecs` (verbatim) / `_generate_config_eks`; select on the model column only; allowlist `cluster_version`; per-orchestrator `_save_outputs` (`:405-434`) map; fresh creds at dispatch; post-apply call into `eks_bootstrap`; EKS branch of `_pre_destroy_cleanup` (`:481`) **also invoked from the inline rollback-destroy branch**; `env=` allowlist on the subprocess; EKS transient-error patterns (`ResourceInUseException`, k8s API timeouts) in `_is_transient_error` (`:262`).
- `api/services/eks_bootstrap.py` — **new**: get-or-create IngressClass / IngressClassParams / `launchpad-bootstrap` namespace / placeholder Ingress; enable the `amazon-vpc-cni` network-policy controller; poll ALB hostname; write `Environment.alb_dns`. All operations idempotent (covers transient retries and the reprovision view).
- `api/services/infra_queue.py` — dedup lock TTL 3600 → 7200, env-overridable.
- `api/messaging/producer/producer.py` — `compute_type` on `infrastructure.created` + `environment.updated`.
- `api/mock/aws_fixtures.py` — `synthesize_environment_outputs(compute_type)`: ECS branch unchanged; EKS branch fills its subset (+ fake `alb_dns`), nulls elsewhere.
- `core/settings.py` — `EKS_ENABLED`, `EKS_PUBLIC_ACCESS_CIDRS`.
- `infra/aws/modules/eks/` — **new**: Auto Mode cluster (`compute_config { enabled = true, node_pools = ["general-purpose"] }`, `bootstrap_self_managed_addons = false`, `authentication_mode = "API"`, allowlisted `cluster_version`, restricted `public_access_cidrs`, `enabled_cluster_log_types`), Auto Mode managed policies on cluster + node roles, `sts:TagSession` in the cluster role trust, split access entries. Outputs `cluster_arn`, `cluster_name`.
- `infra/aws/modules/vpc/` — `enable_elb_subnet_tags` variable (default `false`; EKS builder sets `true`) adding `kubernetes.io/role/elb=1` / `internal-elb=1`. Variable, not unconditional, so ECS plans stay byte-identical.
- `infra/aws/**/*.tf` — one-time `terraform fmt` normalization commit before the CI check lands (the checked-in root `main.tf`/`variables.tf`/`providers.tf` are dead code and have never been linted).
- `requirements.txt` — `kubernetes==<pinned>`.

### `deployment-services/application-service/`
- `api/models/infrastructure.py`, `api/models/environment.py` — mirror `compute_type`; fix the pre-existing status-choices drift in the same migration.
- `api/models/application.py` — `runtime_refs = JSONField(null=True)`; **slug uniqueness scoped to `infrastructure`** with duplicate detection in the migration.
- `api/messaging/consumers/environment.py` — copy `compute_type`; tolerate the absent key and null ECS fields (deploy this service first).
- `api/services/application_deployment_service.py` — orchestrator branch after `_validate_infrastructure` (`:136`), which gets per-orchestrator required fields (EKS: `vpc_id`, `cluster_arn`, `ecr_repository_url`, `alb_dns`); compute `image_tag`; EKS step sequence; k8s handles in `created_resources`; `_cleanup_resource` k8s branch. Single shared slug helper (`:17-19`).
- `api/k8s/deployer.py` — **new** `EKSDeployer`: ensure `app-{slug}` namespace with PSA `baseline` enforce label + ResourceQuota + LimitRange + default-deny-ingress NetworkPolicy (with DNS/egress allow) → nginx ConfigMap → Deployment (app + nginx sidecar, `automountServiceAccountToken: false`, `allowPrivilegeEscalation: false`, `capabilities.drop: [ALL]`, resources from `alloted_cpu/memory` with no Fargate matrix, readinessProbe on nginx `/`) → ClusterIP Service → Ingress (shared `group.name`, paths `/{slug}` + `/{slug}/*`, `healthcheck-path: /`, `success-codes: 200-499` mirroring the ECS TG matcher) → rollout poll harvesting pod events into `error_message`. **Do not mandate `runAsNonRoot`** — customer Dockerfiles frequently run as root; note the sidecar needs a high port if `NET_BIND_SERVICE` is dropped.
- `api/aws/eks.py` — **new** thin `EKSClient(session)`: `describe_cluster` for endpoint/CA (fetched fresh per deploy, never persisted).
- `api/aws/container_config.py` — **new**: pure move of `_generate_nginx_config` (`aws/ecs.py:157-228`) + env-injection helpers so both runtimes share them. ECS behavior identical.
- `api/aws/codebuild.py` — buildspec (`:63-104`) pushes `$ECR_URL:$IMAGE_TAG` and `-latest`; `IMAGE_TAG` in `environmentVariablesOverride` (`:106-146`).
- `aws/session.py` — **remove the plaintext credential write** in `_assume_role_raw` (`:96-123`); `_build_real_session` (`:131`) stops seeding from stored creds.
- `api/services/application_cleanup_service.py` — k8s deletion path (Ingress → Service → Deployment → ConfigMap → namespace); use the shared slug helper.
- `api/services/deployment_queue.py`, `api/management/commands/run_worker.py` — cleanup jobs carry `runtime` + `refs`; absent ⇒ legacy ARN shape.
- `api/views/application.py` — retry + delete snapshot/null `runtime_refs` for EKS as they do ARNs for ECS.
- `api/serializers/application.py` — Fargate CPU/memory matrix enforced only when `compute_type == ecs_fargate`; EKS gets positive-bounds validation.
- `api/mock/mock_session.py` — `eks.describe_cluster` stub (unstubbed-raises preserved); `api/mock/mock_k8s.py` — **new** fake k8s API surface.
- `requirements.txt` — `kubernetes==<same pin>`.

### `gateway-service/`
- `app/api/endpoints/infrastructure.py` — `compute_type` on the create body (honest OpenAPI; the proxy already forwards verbatim).

### `launchpad-frontend/`
- `app/dashboard/infrastructures/new/page.tsx` — compute selector following the region-dropdown pattern; **replace the hardcoded `readyCount < 4`** with `Object.keys(checks).length` (it breaks silently when the selector adds a check).
- `app/dashboard/applications/new/page.tsx` — `CPU_MEMORY_MAP` gated to ECS infras; free-form inputs for EKS.
- `app/dashboard/infrastructures/[id]/page.tsx`, `types/infrastructure.ts`, `lib/api/infrastructures.ts` — badge + typing.

### `app_scripts/create_aws_role.sh`
- EKS statements **conditional on compute_type** (the script already receives `LAUNCHPAD_INFRA_ID`) so ECS-only customers are never widened: `eks:CreateCluster`/`List*`/`Describe*` on `*`; all mutating actions scoped to `arn:aws:eks:*:${ACCOUNT_ID}:cluster/infra-*` plus its `access-entry/*`, `addon/*`, `nodegroup/*` children; an explicit **`Deny` on `eks:*AccessEntry*` / `eks:*AccessPolicy*` for non-`infra-*` clusters**.
- `--max-session-duration 7200` on `create-role` and `aws iam update-role --max-session-duration 7200` in the refresh branch (`:197-253`) — **gated the same way**.

### `identity-services/`
- `services/notification-service/src/templates/infra-email.template.ts` — new `ERROR_SUMMARIES` buckets (EKS AccessDenied, cluster-create timeout, addon/CNI, node capacity, k8s API unreachable). EKS errors carry cluster ARNs, OIDC issuer URLs, and the cluster API endpoint — **no bucket may interpolate a captured group**.
- `templates.test.ts` — assert the generic fallback catches unmatched EKS errors and that no raw error text is echoed.

### CI / docs
- `.github/workflows/ci.yml` — **new** `check-terraform` job (there is currently no Terraform checking at all): pinned `hashicorp/setup-terraform`, `terraform fmt -check -recursive`, per-module `init -backend=false && validate`.
- `docs/IAM_POLICIES.md`, `docs/USER_ONBOARDING_GUIDE.md`, `context.md`, and **`CLAUDE.md` — which is stale**: it claims a repo-root `infra/aws/` that does not exist. All Terraform lives at `deployment-services/infrastructure-service/infra/aws/`.

---

## Security review — how the BLOCK is discharged

| Finding | Resolution in this plan |
|---|---|
| **C1** `eks:*` on `*` grants cluster-admin on the customer's *pre-existing* clusters | Split + resource-scoped to `cluster/infra-*`, explicit `Deny` on the access-entry APIs and `eks:DescribeCluster` elsewhere, heredoc gated on `compute_type` so ECS-only customers are not widened. Defense in depth, not containment: the same policy still grants `iam:*` on `*`, so the role could re-grant itself access — this removes the one-call path and makes anything wider an auditable IAM change. Narrowing `iam:*` is separate work. |
| **C2** Default cluster endpoint is internet-open cluster-admin | `public_access_cidrs` from config with hard-refusal on empty/`0.0.0.0/0`; `enabled_cluster_log_types` on |
| **C3** NetworkPolicy silently unenforced on Auto Mode | CNI ConfigMap + NodeClass `spec.networkPolicy` enabled in provisioning; default-deny per namespace; enforcement asserted by a *connection* test |
| **C4** Namespace-per-infra + slug collision = silent cross-user takeover | Namespace-per-app; slug uniqueness scoped to `infrastructure`; one shared slug helper |
| **H1** Fail-dangerous ALB deletion fallback on a forgeable tag | Positive-signal trigger only; `VpcId` as a second non-forgeable predicate; batch bound + full match-set logging |
| **H2** Doubling cred lifetime while creds sit in Postgres plaintext | Both writers dropped; stored creds purged by migration; max-session-duration gated like C1; fallback logs no exception text |
| **H3** New builder inside the top RCE sink | Select on the model column only; `cluster_version` allowlist; subprocess `env=` allowlist |
| **H4** Orphaned clusters on the no-terraform teardown path | All three teardown entry points run the EKS pre-destroy reap |
| **H5** 15-min presigned bearer token | `expires_in=60`; `x-k8s-aws-id` asserted in `X-Amz-SignedHeaders`; token/Configuration leak paths closed |
| **M1** `EKS_ENABLED` not a kill switch | Checked at provision dispatch, not only at create; immutability locked by test |
| **M2** One identity holds cluster-admin for provision *and* deploy | Split access entries |
| **M3** Missing pod-security posture | `automountServiceAccountToken: false`, ResourceQuota + LimitRange (a tenant can otherwise inflict financial DoS on the infra owner via Auto Mode node provisioning), PSA `baseline`, dropped capabilities |
| **M4** New error strings vs. the sanitized-email invariant | Fallback-path test; no captured-group interpolation |
| **LOW** ECR tag mutability | Not taken. Per-deploy tags are already write-once by construction, but the repo must stay `MUTABLE` because ECS re-pushes `$APP_NAME-latest` every build; `IMMUTABLE` would break ECS deploys. The original finding assumed unique tags *replaced* `-latest` rather than coexisting with it. |

Noted as pre-existing and **not** fixed here: the GitHub token plaintext fallback in `codebuild.py:128`; plaintext app envs; infra notifications going to the globally-first `super_admin` rather than the infra owner (see open question 4).

---

## Verification

**Automated**
- Both Django suites pass **unmodified** after every phase (the ECS-untouched proof), especially `test_onboarding_callback.py`, `test_provisioning_reliability.py`, `test_mock_session.py`, `test_reaper.py`.
- `test_terraform_config_generation.py` (**new**, closes an existing gap) — ECS builder output **string-identical to today's**; EKS output contains the eks module, `authentication_mode = "API"`, restricted CIDRs, access entries, no alb/ecs modules, `enable_elb_subnet_tags = true`.
- `test_eks_bootstrap.py` — get-or-create idempotency, ALB poll success/timeout→ERROR, reprovision re-run safe.
- `test_pre_destroy_eks.py` — deletion ordering, ELBv2 reap, fallback fires only on positive signal and only with a VpcId match, and **is invoked from the rollback path and from `delete_infrastructure`**.
- `test_k8s_token.py` — `X-Amz-Expires=60` and `x-k8s-aws-id` present in `X-Amz-SignedHeaders`.
- `test_mock_eks_deploy.py` — copies the `test_mock_session.py` "SEAM 3" pattern: real `EKSDeployer`/`EKSClient`/`CodeBuildClient` against `MockSession` + `mock_k8s`; every wait loop terminates; rollout failure harvests events; unwind deletes in reverse.
- `test_eks_cleanup.py`, `test_image_tag.py`, `test_retry_snapshot.py`, `test_serializer_compute_gating.py`, `test_compute_type.py`; consumer test for `environment.updated` with and without the key.
- `test_aws_fixtures.py:34` — the "all eight fields" assertion becomes per-orchestrator field sets.
- `templates.test.ts` buckets; `tsc --noEmit` + lint in frontend and identity-services; `terraform fmt -check` + `validate` in CI.

**Manual — mock mode** (via the existing local multi-tenant harness): create with the compute selector → onboard → provision → ACTIVE with `alb_dns` → deploy → ACTIVE at `/{slug}` → retry → delete app → destroy infra. Same run for an ECS infra to confirm nothing moved.

**Manual — real mode, sandbox account, before the flag flip**
- Cluster ACTIVE ≤ ~20 min; `curl http://{alb_dns}/{slug}` returns the app with the prefix stripped; a second app shares the same ALB.
- **A denied NetworkPolicy connection actually fails** (pod in app A cannot reach app B's ClusterIP).
- `aws iam get-role` on a fresh onboard shows `MaxSessionDuration: 7200`; an ECS-only customer's refreshed policy shows **no** EKS statements.
- Permanent provision failure *after* bootstrap → inline rollback cleans the ALB and cluster (the trap case).
- Destroy leaves zero ALBs/ENIs/SGs/tagged resources; `delete_infrastructure` on an `ERROR` EKS infra leaves no orphaned cluster.
- `select metadata from infrastructure` contains no credential keys after a provision.
- No `kubectl`/`helm`/`awscli` in any built image.
- `EKS_ENABLED=false` rejects `compute_type=eks` at create **and** stops reaper re-enqueue of an existing EKS infra.

---

## Risks

- **Destroy wedges on VPC teardown** (controller ALB/ENIs outside TF state) — high likelihood if unmitigated. Mitigated by the reap + guarded fallback across all three teardown paths.
- **ALB never materializes at bootstrap** (controller lag, subnet tag mistakes, ELB quota) — infra ERRORs with a healthy cluster. Generous timeout, distinct error string → notification bucket, rollback cleans up.
- **`environment.updated` cross-service drift** — the widest blast radius. Additive-only payload, consumer deployed first, both-shapes test.
- **Cross-namespace Ingress groups**: the AWS LBC lets any namespace join a named group. All namespaces here are Launchpad-created, so this is contained — but it becomes a real issue the moment a customer gets namespace-create rights on these clusters. Document the assumption.
- **Cost**: EKS Auto Mode bills ~$0.10/hr control plane plus an Auto Mode compute premium versus Fargate. Customer-borne, but it must be stated in onboarding docs.
- **Provision time** is ~3× ECS. Queue TTLs adjusted; per-infra worker serialization already prevents pile-ups.
- **Rollback**: every phase sits behind `EKS_ENABLED`; flag off restores current behavior; all migrations additive except the slug-uniqueness constraint, which needs the duplicate scan first.
- **Observability**: no log viewer exists. `Environment.logs` gets timestamped phase markers (`apply`, `ingress-bootstrap`, `alb-wait`, `k8s-reap`); deploy failures harvest pod events into `Application.error_message` (the one surface the UI polls). Serializing `Environment.logs` to the dashboard is the recommended fast-follow.
- **Mock/real divergence** — paired hard-gate assertions preserved at every seam; `NotImplementedError`-on-unstubbed forces mocks to grow with usage.

---

## Open questions (non-blocking)

1. **Kubernetes version policy** — pin `cluster_version` at `1.31`; who owns bumping it, and is Auto Mode's control-plane auto-upgrade acceptable for customer clusters, or do upgrade windows need surfacing? Product call.
2. **`kubernetes` pip pin** — exact version satisfying ±1 minor client skew against the pinned cluster version. Resolve at Phase 3, then run `pip-audit` on both requirements files.
3. **EKS pricing disclosure** — where in onboarding does the ~$72/mo control plane + Auto Mode premium get communicated? Docs owner needed.
4. **Notification recipient** — infra events go to the globally-first `super_admin`, not the infra owner. Pre-existing, but EKS provisioning is longer and noisier and the leaked strings are more sensitive. Fix in scope or separate ticket?
5. **Service quotas** — pre-flight `service-quotas` check (100 clusters, 50 ALBs per account) before enqueueing, or rely on the sanitized quota-failure email? Recommend the latter; confirm.
6. **`-var-file` refactor** — deliberately declined. The per-orchestrator f-string builders keep the single-artifact debuggability the worker relies on, and the new golden tests give the testability tfvars would have bought. Settled unless someone objects.
