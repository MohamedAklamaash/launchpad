# Managed Database Provisioning — Implementation Plan

**Status:** Planning complete. Security review returned **BLOCK** — do not begin Phase 1
implementation until the "Must fix before Phase 0" items below are resolved.

Repo root: `/home/aklamaash/Desktop/launchpad`. Path prefixes:
**IS** = `deployment-services/infrastructure-service`, **AS** = `deployment-services/application-service`,
**SH** = `deployment-services/shared`, **GW** = `gateway-service`, **FE** = `launchpad-frontend`,
**NS** = `identity-services/services/notification-service`.

> Note: the live terraform modules are at `IS/infra/aws/modules/` — the repo-root `infra/aws/`
> referenced in the top-level `CLAUDE.md` does not exist; that doc is stale on this point.

## Locked decisions

1. **Engines in scope (all four, phased):** RDS PostgreSQL, RDS MySQL, ElastiCache Redis, DocumentDB.
2. **Provisioning approach:** extend the existing per-environment Terraform stack — same generated
   root config, same customer-account S3 state, same worker/queue/lock/heartbeat/reaper. No second
   provisioning path.
3. **Network access:** private-only. DBs in private subnets, `publicly_accessible = false`, ingress
   restricted to the per-infrastructure Fargate app security group. No public endpoint, no bastion in v1.
4. **Credentials:** AWS Secrets Manager + ECS task-definition `secrets` injection. Connection secrets
   live in the customer's account; the ECS task def references them by ARN via `valueFrom`. Credentials
   must never be stored in the control-plane DB, `Application.envs`, AMQP payloads, or API responses.

## Decision summary

- New `Database` desired-state model in infrastructure-service. Every DB create/delete is
  reconciled by the existing per-environment terraform apply through the existing worker, Redis
  queue, env lock, heartbeat, and reaper. A new env status `UPDATING` distinguishes applies on an
  already-ACTIVE environment; a failed UPDATING apply returns the env to ACTIVE and marks only the
  pending Database rows ERROR — the auto-destroy rollback is gated on a new
  `Environment.first_activated_at IS NULL`, so it can never destroy a live environment again.
- Three new terraform modules (`rds`, `elasticache`, `docdb`) emitted as one module block per
  Database row into `_generate_config`; per-DB root outputs parsed by `_save_outputs`. State stays
  in the customer's S3 backend.
- Credentials: RDS/DocDB use `manage_master_user_password` (AWS-owned secret, never enters
  terraform state). ElastiCache uses a `random_password` auth token + TLS, written to a
  terraform-created secret (value lands in customer-owned state — accepted, documented). Only
  **secret ARNs** are persisted on rows, returned by APIs, and published on `environment.updated` v3.
- SG ordering: the worker get-or-creates the per-infra Fargate app SG in Python (shared helper,
  same deterministic name application-service already uses) *before* generating config, and
  interpolates its id into the DB SG ingress rule. Works identically for new and already-provisioned
  environments; no terraform import, no split ownership of the DB SG.
- Apps consume DBs via an explicit `attached_database_ids` list on `Application`; deploy builds
  ECS `secrets` (`valueFrom` with JSON-key addressing into the master-user secret) plus plain env
  for host/port/dbname. Execution role gains scoped `secretsmanager:GetSecretValue`.
- IAM rollout: policy heredoc gains `rds:*`, `elasticache:*`, `secretsmanager:*`. `POST /databases`
  runs a synchronous `iam:SimulatePrincipalPolicy` precheck and returns the refresh snippet on
  deny. Shipping the currently-caller-less refresh UI and bumping
  `NEXT_PUBLIC_LAUNCHPAD_SCRIPT_REF` are Phase 0 blockers, not cleanup.

## Goals

1. A customer can create and delete PG/MySQL/Redis/DocDB in their own account from the dashboard;
   every instance lands in the existing private subnets with `publicly_accessible = false` and
   ingress only from the per-infra app SG.
2. Zero credentials in the control-plane DB, API responses, AMQP payloads, or logs — only secret
   ARNs; enforced by tests that grep serialized rows/payloads (and `Environment.logs` — see audit).
3. An app reaches an attached DB after one attach action + one redeploy, via ECS `secrets` injection.
4. No automation path can destroy customer data: auto-destroy is impossible once an env has
   activated; every RDS/DocDB delete takes a final snapshot; env destroy is blocked while any
   Database row is not DELETED (ERROR included).
5. Existing customers get a clean, actionable "refresh your IAM policy" error (precheck) instead
   of a half-failed apply; the refresh flow is reachable from the UI.

## Non-goals

- **Billing/metering: OUT.** payment-service untouched; no plan/quota model beyond a simple
  max-databases-per-infra constant.
- IAM database auth (no `taskRoleArn` exists; out of v1). Public endpoints, bastions, or
  customer-network peering.
- Multi-AZ/HA tiers, read replicas, in-place scaling/modify (create/delete only in v1), custom
  backup windows, Redis auth-token rotation, Redis clustering (single node), dedicated DB subnets
  (existing two private /24s suffice).
- Fixing the pre-existing `_get_super_admin_email()` wrong-recipient bug was originally scoped as
  a non-goal — **the security review overrides this: see "Must fix before Phase 0" below.**

## Options considered

**Module wiring shape**
- *A: one "databases" umbrella module with `for_each` over a map.* Fragile HCL map serialization
  from Python; mixed-engine conditionals inside HCL; awkward output addressing. Cost: M.
- *B (chosen): one generated module block per Database row* (`module "db_<id8>"`), engine-specific
  module source. Matches the existing f-string generation style exactly; trivial per-DB outputs;
  removing a row removes the block and terraform plans the destroy. Cost: S.

**Job/record model**
- *A (chosen): Environment stays the job row; add `UPDATING` status + reaper coverage.* Reuses
  lock/heartbeat/reaper untouched in semantics. Cost: S.
- *B: separate ProvisionJob model.* A second locking/reaping path — exactly what the "no second
  provisioning path" decision forbids. Cost: L.

**App SG ordering**
- *A: terraform owns the app SG; adopt existing ones.* Requires `terraform import` or conditional
  resource-vs-data emission per env — stateful branching in generated config, drift pain.
- *B: DB SG created empty; application-service adds ingress at deploy.* DB unreachable until next
  deploy; split ingress ownership.
- *C (chosen): worker get-or-creates the app SG* (shared boto3 helper, identical name derivation)
  before config generation, passes the literal sg-id into the module. SG exists at apply time for
  new *and* existing envs; application-service's lazy creator finds and reuses it; terraform fully
  owns the DB SG + its ingress rule. Cost: S.

**Password management**
- *A: `random_password` everywhere.* Secrets in state and `terraform output` handling; rotation debt.
- *B (chosen): `manage_master_user_password` for RDS + DocDB; `random_password` auth token only for
  ElastiCache* (no AWS equivalent exists), written to a terraform-created secret. Token value is in
  state — in the customer's own private, versioned bucket, which the platform can already read via
  AssumeRole anyway. Requires `transit_encryption_enabled = true` → `rediss://` (documented, Phase 2).
- *C: platform generates and pushes secrets via API.* Rejected: credentials transit the platform.

**Attachment model**
- *A: auto-inject every infra DB into every app.* Every app gets every credential; name collisions.
- *B (chosen): explicit `attached_database_ids` on Application*; injection uses per-DB name prefixes
  (`<NAME>_HOST/_PORT/_DB/_USERNAME/_PASSWORD`); injected names win over `Application.envs`
  collisions (stripped); attach does **not** auto-redeploy (consistent with existing PATCH
  behavior — UX copy must say "redeploy to apply"). Cost: S.

**Deletion protection**
- *A: `deletion_protection = true` + two-phase disable-then-destroy applies.* Two serialized applies
  per delete through the env lock; poor UX; retry complexity.
- *B (chosen): `deletion_protection = false`, `skip_final_snapshot = false`*, typed-name
  confirmation in the UI. **Note (from security review): the snapshot identifier must be fixed at
  create time from the Database row UUID, not computed at delete time — see finding below.**

## Chosen approach + rationale

**Create flow:** `POST /api/v1/databases` (JWT; infra owner) → validate: env exists and is ACTIVE,
engine/instance-class/engine-version/storage against settings allowlists, name unique per env and
DNS/identifier-safe, per-infra DB quota → run `iam:SimulatePrincipalPolicy` on the assumed role for
the exact create actions (`rds:CreateDBInstance`, `secretsmanager:CreateSecret`,
`elasticache:CreateReplicationGroup`) — on deny, 422 with a machine-readable
`policy_refresh_required` code → create `Database` row PENDING → enqueue on the existing
`infra:provision` queue → **202** with the row.

Worker: lock env; status → `UPDATING` if `first_activated_at` set, else normal PROVISIONING;
get-or-create app SG (skipped/synthesized in dev mode); `_generate_config` emits one module block
per live Database row + per-DB outputs; apply; `_save_outputs` sets endpoint/port/secret_arn and
marks **every** row whose outputs appear ACTIVE (one apply may satisfy several PENDING rows),
stamps `first_activated_at` if null, publishes `environment.updated` **v3** (v2 fields +
`databases[]` of `{id, name, engine, host, port, secret_arn, status}`; consumer accepts 2 and 3).

**Failure:** transient → existing retry. Permanent on UPDATING → env back to **ACTIVE**, pending
rows → ERROR, error summarized for email; **no destroy**. Permanent on first-time provision with
`first_activated_at IS NULL` → auto-destroy remains (still only ever empty infra). Reaper
exhaustion during UPDATING parks the *rows*, returns env to ACTIVE.

**Delete flow:** `DELETE /databases/<id>` (typed-name confirm enforced client-side, `confirm_name`
server-side) → row DELETING → enqueue provision job → config regenerated without that module,
final snapshot id recorded on the row → apply → row DELETED (soft, kept for audit). Delete must
work from ERROR. **Env/infra destroy is refused while any Database row is not DELETED** — the API
returns the list of live DBs.

**Additional traps found by the planner (beyond the four locked decisions):**
- **Gateway rate limiter kills polling:** 10 req/300s per client IP would break DB status polling.
  Needs a scoped exemption for the status GETs (see security finding on how *not* to do this).
- **infrastructure-service has no `InfrastructureUserRole` model** — per-infra roles live only in
  application-service. v1: DB create/delete is **owner-only** in IS; members can view. Role-based
  DB permissions are a follow-up requiring the role edge to reach IS.
- **DocDB requires TLS with the AWS global CA bundle** — customer-visible, belongs in Phase 3
  docs/UI copy.

## File-level breakdown

**Terraform (`IS/infra/aws/`)**
- `modules/rds/{main,variables,outputs}.tf` — new: `aws_db_subnet_group` (existing private subnet
  ids), SG + ingress rule from app-SG-id var on engine port, `aws_db_instance` (engine pg|mysql,
  `manage_master_user_password`, `publicly_accessible=false`, `storage_encrypted`, `multi_az=false`,
  `skip_final_snapshot=false`, `final_snapshot_identifier` var); outputs endpoint/port/secret-ARN/sg-id.
- `modules/elasticache/…` — new: subnet group, SG, single-node `aws_elasticache_replication_group`
  with `transit_encryption_enabled`, `auth_token` from `random_password`,
  `aws_secretsmanager_secret(+version)` named `launchpad/{env_name}/{db_name}` holding
  `{auth_token, host, port}`; outputs endpoint/port/secret-ARN.
- `modules/docdb/…` — new: `aws_docdb_subnet_group`, SG, `aws_docdb_cluster`
  (`manage_master_user_password`, `storage_encrypted`, final snapshot) + one
  `aws_docdb_cluster_instance`; outputs endpoint/port/secret-ARN.
- `modules/iam/main.tf` — **scoped** inline policy on the ECS execution role for
  `secretsmanager:GetSecretValue`, generated per-Database-row ARNs (see security finding — do
  **not** use `secret:rds!*` wildcard) + `kms:Decrypt` scoped via `kms:ViaService`.

**infrastructure-service (`IS/`)**
- `api/models/database.py` — new: uuid pk, environment FK, name, engine, engine_version,
  instance_class, allocated_storage, status (PENDING|PROVISIONING|ACTIVE|ERROR|DELETING|DELETED),
  host, port, secret_arn, final_snapshot_id, error_message, timestamps; `db_table='databases'`,
  index `(environment, status)`.
- `api/models/environment.py` — add `first_activated_at` (nullable) and `UPDATING` to status choices.
- `api/models/__init__.py` — export `Database`.
- `api/migrations/00XX_*.py` — additive table + columns (backward-compatible).
- `api/services/terraform_worker.py` — `_generate_config` gains db module blocks + per-DB outputs
  + provider pin `>=5.13,<6.0`; provision path: UPDATING vs PROVISIONING branch keyed on
  `first_activated_at` (not `status`), app-SG pre-create via shared helper, rollback gate,
  permanent-UPDATING-failure exit; `_save_outputs` parses `db_*` outputs, updates rows, stamps
  `first_activated_at`, builds v3 payload, **persists parsed/allowlisted output keys only — not
  raw `terraform output -json` stdout — into `Environment.logs`**; `destroy()` and
  `delete_infrastructure`'s fast path both refuse if any non-DELETED Database row exists, checked
  *before* any state mutation.
- `api/services/infrastructure.py` — the ERROR/PENDING immediate-delete fast path
  (`delete_infrastructure`) gets the same guard as `destroy()`, plus a refusal whenever
  `first_activated_at IS NOT NULL` regardless of Database rows.
- `run_worker.py` — reaper query includes UPDATING; UPDATING reap-exhaustion parks rows not env;
  startup recovery loop (`status__in=[...]`) includes UPDATING.
- `api/services/database_service.py` — new: validation (allowlists, uniqueness, quota), precheck
  call, enqueue, delete with `confirm_name`. **Fetches the environment via
  `InfrastructureRepository.get_by_id(user_id, infra_id)` — never `Database.objects.get(id=...)`
  directly — to close the cross-tenant IDOR path.**
- `api/cloud_providers/aws/iam_precheck.py` — new: `SimulatePrincipalPolicy` against
  `LaunchpadDeploymentRole` for the engine's create actions; dev-mode short-circuit.
- `api/serializers/database.py`, `api/views/database.py`, `core/urls.py` — new CRUD-minus-update
  endpoints (`POST/GET list/GET detail/DELETE` under `/api/v1/databases`); JWT-authed, owner-only
  via existing IS permission class, row-scoped per above.
- `api/messaging/producer.py` — `environment.updated` `metadata.version=3` with `databases[]`
  (ARN pointers only; keep the sg-id regex validation).
- `api/services/notification.py` — dispatch `database_create_success/failure`,
  `database_delete_success/failure` — **blocked on fixing `_get_super_admin_email()`, see below.**
- `api/mock/aws_fixtures.py` — synthesize per-DB outputs; dev-mode skips the SG boto3 call.
- `core/settings.py` — engine/instance-class/version/storage allowlists (exact-match, not regex),
  `MAX_DATABASES_PER_INFRA`.

**shared (`SH/`)**
- `aws/app_security_group.py` — new: `get_or_create_app_security_group(ec2_client, infra_id,
  vpc_id, alb_sg_id)` with the exact name derivation from AS's `_get_app_sg_name`; AS's
  `_get_or_create_app_security_group` refactored to call it.

**application-service (`AS/`)**
- `api/models/database.py` + migration — new read-model, populated only by AMQP.
- `api/messaging/consumers/environment.py` — accept payload versions 2 and 3; upsert read-model.
- `api/models/application.py` + migration — `attached_database_ids` JSONField (default `[]`).
- `api/services/application_service.py` — validate attach ids against read-model (same infra, ACTIVE).
- `api/services/infrastructure_permissions.py` — `can_attach_database` (SUPER_ADMIN|ADMIN).
- `api/services/application_deployment_service.py` — build injection set from attached DBs: plain
  env `<NAME>_HOST/_PORT/_DB`, `secrets` entries for `<NAME>_USERNAME`/`_PASSWORD` via
  `{secret_arn}:username::`/`:password::` JSON-key addressing (Redis: `<NAME>_AUTH_TOKEN`,
  `<NAME>_TLS=true`); strip colliding keys from `application.envs`.
- `aws/ecs.py` — task definition gains the `secrets` key alongside `environment`.
- `api/mock/mock_session.py` — `register_task_definition` accepts `secrets`.
- Application serializer — expose `attached_database_ids`; new attach endpoint or PATCH-field.

**gateway-service (`GW/`)**
- `app/api/endpoints/database.py` — new: proxy handlers for create/list/detail/delete + attach.
- `app/api/router.py` — include the router.
- `constants.py` (`EXEMPT_PATHS`) — **method-and-path-scoped** exemption for the DB status GET
  endpoints only (the exact-string matcher here cannot express a prefix — see security finding;
  do not exempt POST `/api/databases`, which must stay rate-limited, tighter than default).

**Script + docs**
- `app_scripts/create_aws_role.sh` — single heredoc gains `rds:*`, `elasticache:*`,
  `secretsmanager:*` (scoped appropriately per the security finding — track broader `iam:*`/`kms:*`
  scoping as a separate workstream, not a blocker).
- `docs/IAM_POLICIES.md` — both mirrored policy blocks updated.
- Bump `NEXT_PUBLIC_LAUNCHPAD_SCRIPT_REF` to the merged SHA (release step).

**frontend (`FE/`)**
- `lib/api/databases.ts` — new resource module + types; status polling helpers.
- `app/dashboard/infrastructures/[id]/page.tsx` — new "Databases (n)" section cloned from the
  Applications pattern: create dialog, status badges with polling, typed-name delete confirm,
  secret-ARN display (never a credential), attach control with "redeploy to apply" copy.
- New component: policy-refresh dialog — first caller of `infrastructureApi.issueScriptApiKey` +
  `resolveOnboardingScript('refresh')`; shown when create returns `policy_refresh_required`.

**notification-service (`NS/`)**
- `src/templates/infra-email.template.ts` — four new `InfraEvent` members
  (`database_create_success/failure`, `database_delete_success/failure`; TS `Record` exhaustiveness
  enforces labels/subjects) + `ERROR_SUMMARIES` regexes for RDS/ElastiCache/DocDB/SecretsManager
  failure classes. **Escape `${name}` interpolation (currently unescaped — HTML injection via a
  customer-supplied DB/infra name) and extend the hard-coded `isFailure` check beyond
  `provision_failure || destroy_failure`.**

## Phasing

**Phase 0 — enablers (ships alone, no DB feature visible):**
- IAM heredoc widening (scoped) + `IAM_POLICIES.md` + script-ref bump; refresh-policy dialog in
  the frontend.
- `first_activated_at` + rollback gate; `UPDATING` status + reaper + startup-recovery coverage.
- Exec-role secrets policy (scoped, per-row ARNs).
- Gateway rate-limit exemption (method-scoped, GET only).
- Producer/consumer v3 scaffolding with empty `databases[]`.
- Provider pin `>=5.13,<6.0` + committed `.terraform.lock.hcl`.
- **Security-mandated additions (see "Must fix" below):** `_exec_tf` environment allowlist,
  `delete_infrastructure` fast-path guard, `_save_outputs` sensitive-output redaction,
  `_get_super_admin_email()` recipient fix (or ship Phase 1 with no notification events).

**Phase 1 — RDS PostgreSQL + MySQL:** `rds` module, `Database` model + API + worker wiring +
`_save_outputs`, SG shared helper, precheck, ECS secrets injection + attach, mock-mode outputs,
notification events, full frontend section.

**Phase 2 — ElastiCache Redis:** `elasticache` module (auth token + mandatory TLS — `rediss://`
is customer-visible), single-node default, `AUTH_TOKEN` injection variant, Redis-specific error
summaries and mock fixtures.

**Phase 3 — DocumentDB:** `docdb` module (cluster + one instance, managed master password), TLS
CA-bundle requirement in docs/UI copy, DocDB error summaries, verify `rds:*` covers all
control-plane calls via simulate before enabling the engine in the allowlist.

## Test strategy

- **IS pytest** (`test_settings.py`, MODE=dev): `test_database_api.py` — validation/allowlists/
  quota, owner-only authz + cross-tenant 404 test, precheck-denied → 422, 202 + row shape,
  delete-from-ERROR, destroy-refused-with-live-DBs, **serializer/payload greps asserting no
  credential-shaped fields, extended to cover `Environment.logs`**. `test_terraform_worker_databases.py`
  — config-generation snapshot per engine, rollback gate keyed on `first_activated_at` (not status),
  permanent-UPDATING failure → env ACTIVE + rows ERROR, multi-row `_save_outputs`. Extend
  `test_provisioning_reliability.py` — reaper reaps UPDATING; startup recovery includes UPDATING.
  Extend `test_aws_fixtures.py`/`test_terraform_worker_dev_mode.py` — synthesized DB outputs,
  dev-mode SG skip.
- **AS pytest:** consumer accepts v2 and v3; task-def builder emits `secrets` with correct
  JSON-key ARNs and strips env collisions; MockSession accepts `secrets`.
- **gateway:** `pytest gateway-service/tests/` — new endpoint proxying, internal-token injection,
  rate-limit exemption scoped to GET only (explicit test that POST stays throttled).
- **TS:** `InfraEvent` Record exhaustiveness; unit test for `resolveOnboardingScript('refresh')`.
- **Integration:** MODE=dev end-to-end via the multi-tenant harness (create → poll → attach →
  deploy → delete); one real-AWS sandbox run per phase, including an empirical check of whether
  RDS-created ENIs inherit terraform `default_tags` (drives the `_pre_destroy_cleanup` risk).
- CI: `python -m compileall` + `ruff check`; prettier is the known break in identity-services.

## Risks and mitigations

- **Customer data destroyed by automation** (catastrophic): `first_activated_at` gate on both
  `destroy()` and the `delete_infrastructure` fast path + destroy-refusal while live DBs exist +
  mandatory final snapshots with a creation-time-fixed identifier.
- **HCL injection → control-plane compromise** (see security finding — this exists *today*,
  independent of this feature): `_exec_tf` env allowlist + boundary validation of all
  string-interpolated fields (not just the new ones).
- **Existing customers hit AccessDenied**: synchronous precheck with actionable error + refresh dialog.
- **Env-apply blast radius**: all DB ops for an env serialize behind one lock; mitigated by
  UPDATING isolation (deploys never blocked), validation-before-enqueue, and the reaper. Accepted for v1.
- **Long applies vs locks**: RDS/DocDB creates run 10–20 min — within `LOCK_TTL=3600`; verify in sandbox.
- **Cross-tenant secret read via shared execution role / shared app SG**: accepted risk, must be
  stated explicitly to users/docs — see security findings.
- **Cost surprise** (DocDB ~$60/mo smallest instance): show indicative cost in the create dialog.
- **Provider drift**: pin `>=5.13,<6.0` + committed lock file.
- **Cross-tenant notification misdelivery**: fix recipient resolution before enabling DB emails.

## Validation checklist

1. Phase 0 sandbox: refresh script grants all required actions under `SimulatePrincipalPolicy`.
2. Un-refreshed account: `POST /databases` returns 422 with the refresh snippet; nothing enqueued.
3. Create PG: private subnets, `PubliclyAccessible=false`, DB SG ingress solely from the app SG;
   secret is `rds!`-prefixed AWS-managed; `terraform output` and `Environment.logs` contain no password.
4. Dump the control-plane Postgres and one `environment.updated` v3 payload: no credential-shaped field.
5. Attach + redeploy: task definition shows `secrets` entries, no duplicate names vs `environment`.
6. Delete with wrong `confirm_name` → rejected; correct → final snapshot exists with the row's
   recorded id; retry-after-failed-delete does not collide.
7. Env destroy with a live DB → refused; after deleting all DBs → destroy completes.
8. Kill the worker mid-UPDATING apply: reaper re-enqueues; env never parks in ERROR while
   resources are live; also verify a worker *restart* (not just reap) recovers an UPDATING env.
9. Attempt HCL injection via `metadata.vpc_cidr` / a crafted database `name` — confirm rejection
   at the API boundary, not just at generation time.
10. Delete an infra whose environment is in ERROR with live Database rows — confirm the fast path
    now refuses instead of silently orphaning the AWS resources.
11. MODE=dev: full harness pass — create/poll/attach/deploy/delete with synthesized outputs, zero
    AWS calls.
12. Frontend polls DB status for 10 minutes without hitting the rate limit; confirm POST
    `/databases` is still throttled.

## Open questions

- Per-infra DB quota and default instance-class ceiling?
- Role-based DB permissions — is owner-only acceptable for v1, and should the
  `InfrastructureUserRole` edge be replicated to infrastructure-service later?
- Retention of DELETED rows / snapshot bookkeeping after infra destruction (the worker currently
  hard-deletes Environment+Infrastructure rows) — keep an audit trail elsewhere?
- Default backup retention for RDS/DocDB (7 days proposed) — cost/durability sign-off?
- `CLAUDE.md`'s `infra/aws` path is stale (real path: `IS/infra/aws/`) — correct alongside Phase 0?
- Fix `_get_super_admin_email()` in Phase 0, or hold all DB notification events until it's fixed
  as a separate piece of work?

---

## Security pre-review — verdict: BLOCK

Full independent audit against the plan above and the live codebase. **Do not begin Phase 1 as
written; Phase 0 needs three additions this plan did not originally contain.**

### Must fix before implementation

**[CRITICAL] HCL injection in `_generate_config` is control-plane RCE — and it already exists today,
independent of this feature.**
Boundary crossed: authenticated tenant → Launchpad control plane (all tenants).
`metadata.vpc_cidr` and `metadata.aws_region` are caller-supplied at infra-create
(`infrastructure.py:65-72` filters only credential keys) and land unvalidated in generated HCL at
`terraform_worker.py:212,218` — a tenant can already close the `module "vpc"` block and append
arbitrary HCL. The escalation: `_exec_tf` runs terraform with the worker's **entire process
environment** (`env = {**os.environ, ...}`, `:132-146`), which per `env.example` holds
`JWT_SECRET`, `INTERNAL_API_TOKEN`, `DATABASE_PASSWORD`, and the **platform's own AWS keys** — the
principal that can AssumeRole into *every* onboarded customer account. Injected HCL gets a
`terraform_data` + `local-exec` provisioner (builtin, no provider download) executing with that
environment. One authenticated tenant → forge JWTs, bypass `X-INTERNAL-TOKEN`, assume into every
customer AWS account.
*Fix, Phase 0:* (1) `_exec_tf` passes an explicit env allowlist, never `**os.environ`. (2) Validate
`metadata.vpc_cidr` (anchored CIDR regex) and `metadata.aws_region` (region allowlist) at the API
boundary in `infrastructure.py`. (3) New DB fields: exact-match membership against settings
allowlists (not regex-shaped), `allocated_storage` as `int` with bounds, `name` matching
`^[a-z][a-z0-9-]{2,30}$`. (4) `name` reaches three sinks (HCL block, secret name, snapshot id) —
validate once at the boundary, never re-derive. (5) Longer-term: generate HCL via
`terraform.tfvars.json`/`json.dumps` instead of f-string interpolation.

**[CRITICAL] `delete_infrastructure`'s ERROR/PENDING fast path bypasses the destroy guard entirely.**
Boundary crossed: control-plane state → live customer AWS resources.
`infrastructure.py:176-183` calls `env.delete()` directly on the comment "no live AWS resources" —
true today, false once stateful resources exist. Chain: UPDATING apply → worker crash → reaper
re-enqueues → `provision()` overwrites status to PROVISIONING (`:319`), erasing the UPDATING
marker → reaper parks ERROR after `MAX_REAP_ATTEMPTS` → owner deletes → `Database` rows cascade
away while the RDS instances keep running, untracked and unreachable by any teardown path.
*Fix:* guard at both entry points, before the status branch in `delete_infrastructure`; refuse
whenever `first_activated_at IS NOT NULL` regardless of Database rows; in `destroy()` the guard
must run before the `DESTROYING` status update and before `_pre_destroy_cleanup` runs.

**[HIGH] The failure/rollback gate must key on `first_activated_at`, never on `status == UPDATING`**
— `provision()` overwrites status to PROVISIONING on any reaped run, so a status-keyed branch
silently stops working after one reap cycle. Also add `UPDATING` to the startup recovery loop
(`run_worker.py:205`, currently `PENDING`/`PROVISIONING` only) — an UPDATING env that survives a
worker restart is otherwise stuck indefinitely with no job.

**[HIGH] `secret:rds!*` on the shared ECS execution role reads the customer's unrelated RDS/Aurora
master secrets** — anything in the account, not just Launchpad-managed ones. And
`Infrastructure.code` has no uniqueness constraint (`unique_together` is only `('user','name')`),
so two infrastructures can share one AWS account and `secret:launchpad/*` lets infra A's tasks
read infra B's Redis auth tokens. Scoping is viable with no dependency cycle — the rds/docdb
modules consume nothing from the iam module, so a separate `aws_iam_role_policy` taking
`db_secret_arns` per Database row is a clean DAG. Scope `kms:Decrypt` via `kms:ViaService` on
`secretsmanager.<region>.amazonaws.com`.

**[HIGH] Four new notification events routed through a known cross-tenant misdelivery bug.**
`notification.py:29-37` picks the globally-first `super_admin`, not the infra owner. The plan
listed fixing this as a non-goal while adding four DB events through the same channel — tenant A's
database and infra names would be emailed to tenant B's admin, a fresh instance of exactly what
commit `b896420` ("close cross-tenant isolation gaps") was written to fix. *Either fix the
recipient resolution in Phase 0, or ship Phase 1 with no DB notification events.*

**[HIGH] The rate-limit exemption is not implementable as originally scoped.**
`gateway-service/app/core/constants.py` does not exist — `EXEMPT_PATHS` lives at
`gateway-service/constants.py` and matching is exact-string membership, not prefix, so
`/api/databases/<uuid>` cannot be exempted as written. Making the matcher prefix-based to fix that
would also exempt **POST** `/api/databases` — unthrottling the synchronous
`SimulatePrincipalPolicy` + STS endpoint, a cost-amplification vector. *Fix:* method-and-path
scoped exemption, GET only; keep POST rate-limited, tighter than the default; order the precheck
strictly after JWT auth, the owner check, and validation; cache the simulate result per
(infra, action-set). Note `proxy.py:32`'s hard **10s** timeout — AssumeRole + Simulate can exceed
it, returning a 504 to the user while the Django request completes anyway.
*Reassuring finding:* `EXEMPT_PATHS` gates the rate limiter only — the gateway performs no JWT
verification at all (it forwards `Authorization` through), so exempting a path from rate limiting
does not disable authentication. The limiter also fails **open** on Redis error and is IP-keyed only.

**[MEDIUM] `_save_outputs` persists raw `terraform output -json` stdout into `Environment.logs`.**
The `-json` form emits `sensitive`-marked values in cleartext, unlike human-readable
`terraform output`. Not an active leak today — `Environment.logs` is exposed by no serializer,
endpoint, AMQP payload, or admin registration, and the planned outputs (endpoint/port/ARN) aren't
secret — but goal 2's stated enforcement ("grep serialized rows/payloads") doesn't cover it, so one
future output addition silently breaks the invariant with nothing to catch it. *Fix:* persist
parsed, allowlisted output keys only, never raw stdout; extend the grep test to `Environment.logs`.

**[MEDIUM] `final_snapshot_identifier` is frozen in *state* at create time**, but the plan's delete
flow describes recording it at delete time — a value computed then never reaches the destroy, and
a delete-then-recreate at the same name collides on a static identifier. *Fix:* set the identifier
at creation from the immutable `Database` row UUID.

### Tenant isolation — one gap found

The IS/AS split (owner-only in IS, per-infra roles in AS) is otherwise clean — verified IS
mutations are owner-only, AS correctly demotes a non-owner `SUPER_ADMIN` row to `ADMIN`, and the
AS read-model carries no per-user data. The one gap: **`database_service` must fetch the
environment via `InfrastructureRepository.get_by_id(user_id, infra_id)`** (the scoped
`Q(user_id=...) | Q(invited_users__id=...)` predicate), not by `environment_id` directly — a bare
`Database.objects.get(id=...)` in the detail/DELETE handler is a textbook cross-tenant IDOR.
Mandate the predicate and a test asserting a second tenant gets 404 on another tenant's database id.

### State explicitly as accepted risk (not blockers, but must be documented)

- The per-infra app SG is **shared across every app in the infrastructure** (name derives from
  `infrastructure_id` alone) — "ingress solely from the app SG" means *any app in the infra can
  reach any database*, not just the attached one. Reasonable v1 default; state it as weaker
  isolation than it may appear.
- A shared execution role means any `ADMIN` can read every DB credential in the infra by attaching
  it to their own app — bounded correctly by the role model, not an escalation past it, but worth saying.
- Egress is unrestricted on the app SG (ingress-only rules today; AWS default allow-all egress) —
  a compromised container can exfiltrate database contents anywhere.
- The Redis auth token lands in terraform state in the customer's **versioned** S3 bucket, so it
  persists across object versions even after rotation.
- `_pre_destroy_cleanup` force-deletes ENIs by `tag:InfraID` — verify empirically whether
  RDS-service ENIs inherit terraform `default_tags` before Phase 1; a force-detach of a live RDS
  ENI is a customer-visible outage if they don't.
- `infra-email.template.ts:145` interpolates `${name}` unescaped — a customer-supplied database
  name becomes stored HTML injection into an inbox if passed through as `infraName`.

### Pre-existing, not caused by this plan

- `iam:*` and `kms:*` at `Resource: "*"` already make `LaunchpadDeploymentRole` effectively
  root-equivalent in the customer account. Adding `secretsmanager:*` doesn't change the worst case
  for an attacker who already reaches the control plane — it removes an escalation *step*. Scoping
  the new grant is cheap and should happen now; scoping `iam:*`/`kms:*` is a separate, larger
  workstream to track, not block on.
- `Application.envs` plaintext/republished-on-AMQP gets *better*, not worse, with this feature —
  credentials move from `envs` to Secrets Manager ARNs.
- The gateway rate limiter fails open and is IP-only — pre-existing, relevant context for the
  precheck-DoS question above but not introduced by this plan.
- The earlier explorer note that STS credentials are "re-serialized into API responses" is
  **incorrect** — `_redact_metadata` and the AMQP producer both strip them independently. The real
  residual issue is narrower: plaintext at rest in Postgres only.

### Recommended follow-ups (not blockers)

- Commit `.terraform.lock.hcl` and copy it into the work dir alongside modules in `_exec_tf` — the
  provider pin bump (`~>5.0` → `>=5.13,<6.0`) with no lock file is real supply-chain drift.
- Before Phase 1, empirically confirm RDS-service ENI tagging behavior (drives the
  `_pre_destroy_cleanup` risk above).
