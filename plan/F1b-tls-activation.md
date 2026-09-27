# F1b — TLS activation and custom domains

**Status:** Phase 1 plumbing done (#68); decisions + zone terraform done (#75); **part 1
(platform DNS writer + ledger + teardown) done, mock-verified**; **part 2 (cert bootstrap,
ACM policy grants, 443 listener, nginx host mode groundwork, EKS group-name fix) done,
mock-verified — see Part 2 below for what shipped vs. what's scaffolded-but-not-wired**;
part 3 (custom domains, full host-routing runtime wiring, host URL publish) not started.
**Depends on:** #68, #75 · **Blocked by:** the owner action below (zone not yet applied to
real AWS, so parts 1–2 are mock-verified only) and part 3 not yet built.

## Part 2 — what shipped (mock-verified)

Scope: pre-review §2 (cert bootstrap), §3 steps (2)–(4) (teardown), §5 (ALB/nginx/EKS), §6
test 6 (golden tests).

- **Policy v4.** `policy.json` gains `acm:RequestCertificate` (conditioned on
  `aws:RequestTag/ManagedBy=launchpad`), `acm:DescribeCertificate`/`ListCertificates`/
  `ListTagsForCertificate` (explicit actions, not `Describe*`/`List*` wildcards — `grants()`
  only models the `service:*` wildcard shape, and least-privilege explicit actions are
  cheap here), `acm:AddTagsToCertificate`, and `acm:DeleteCertificate` (conditioned on
  `aws:ResourceTag/ManagedBy=launchpad`). No new `elasticloadbalancing:*` actions —
  the existing account-wide wildcard already covers the 443 listener and host-header
  rules. `required_version_for` is now 4 for both compute types.
  **Release action:** bump `NEXT_PUBLIC_LAUNCHPAD_SCRIPT_REF` in the frontend's deploy
  environment in the same release (it is not a repo file — see plan/README.md's Standing
  release actions).
- **Cert bootstrap** (`api/services/cert_bootstrap.py`), called from
  `TerraformWorker._save_outputs` after a successful apply (inside the dispatched
  provisioning job's DB lock, so the bounded ACM poll runs under the 60s
  `LockHeartbeat`, never on its own thread). Gate order: `policy_version < 4` →
  `tls_status=POLICY_STALE` (new `InfrastructureCertificate` choice), no ACM call at all;
  already `ISSUED`/`PENDING` → no-op; `FAILED` → best-effort `DeleteCertificate` then
  re-request. Domain is always `*.{dns_label}.{base}`, never the bare two-label name.
  `tls_requested_at` is written immediately before `RequestCertificate` and moves forward
  on every attempt (first request or a post-timeout retry) — never cleared, matching the
  model's documented monotonic invariant. Reuse checks `list_certificates` +
  `list_tags_for_certificate` for an existing `ManagedBy=launchpad`-tagged cert for the
  exact domain before requesting; `IdempotencyToken=sha256(infra_id)[:32]` is the backstop.
  A bounded `DescribeCertificate` poll (~2min) waits for the DNS `ResourceRecord`, asserts
  `DomainName` and record shape against `api.services.platform_dns.naming`'s own
  validators (the same allow-list the DNS writer enforces), persists the row, and requests
  a DNS reconcile. Never raises out of the caller — a certificate problem is reflected in
  `tls_status`, not a failed provision.
- **ISSUED re-check** (`run_worker.py:check_pending_certificates`), on its own ~30s timer
  in the worker's main loop (fleet-rate-limited by a short Redis lock, mirroring the
  reaper) — never inside a dispatched job's lock, and never in `run_dns_writer` (which
  holds no customer credentials; this is a customer-account `DescribeCertificate` call).
  `ISSUED` → stamps the row and calls `InfraQueue.enqueue_provision` so the next apply
  renders the 443 listener; `FAILED`/`VALIDATION_TIMED_OUT`/`REVOKED` → `tls_status=FAILED`;
  past `ISSUED_CHECK_TIMEOUT` (~30min) → `tls_status=FAILED` without ever touching
  `Environment.status` — the environment stays `ACTIVE` on its path URL.
- **Mock ACM** (`cert_bootstrap.FakeAcmClient`), process-local like part 1's
  `FakeRoute53Zone`, gated by the same `infra_is_mock`/`dev_mode` mismatch check as
  `route53_client.get_route53_client`. Emits validation records that pass the real
  writer's `naming.py` validators unchanged, and "issues" after
  `ISSUE_AFTER_DESCRIBE_CALLS` (2) `describe_certificate` calls so the full
  request → poll → ISSUED → re-provision loop runs end-to-end in dev mode.
- **Teardown steps (2)–(4).** `delete_acm_certificate_after_listener_removed` (the part-1
  hook) is implemented: best-effort `acm:DeleteCertificate` using the customer's
  already-authenticated credentials, called from `TerraformWorker.destroy()` right after
  the `terraform destroy` call (both success and failure branches — best-effort either
  way; a listener terraform failed to remove just makes `DeleteCertificate` fail with
  `ResourceInUse`, caught and logged, and the reaper retries the whole destroy). **The
  two-phase teardown split part 1 flagged as "part 2's job" was not built** — re-reading
  the pre-review, (4)'s "validation CNAME deleted unconditionally, never gated on (3)"
  does not require (4) to run *after* (3), only that it never depends on (3) succeeding.
  Today's single-phase teardown (`dns_teardown_requested_at` set → desired state `[]`)
  already deletes the validation CNAME immediately, in the same reconcile as
  wildcard+edge, at step (1) — before `_pre_destroy_cleanup`/`AssumeRole` even run. That is
  a strict superset of the requirement with a *shorter* window for a torn-down customer to
  keep renewing a validation record than a real two-phase split would leave. No new
  `desired_state.py` signal was needed.
- **443 listener** (`infra/aws/modules/alb/main.tf`): `aws_lb_listener.https`, `count =
  var.enable_https ? 1 : 0`, `ssl_policy = "ELBSecurityPolicy-TLS13-1-2-2021-06"`, default
  action a fixed `404` response (never a forward — every real app is reached only via its
  own host-header rule). `terraform_worker.py`'s `_generate_config_ecs` emits the extra
  `enable_https`/`certificate_arn` module args and the `https_listener_arn` output *only*
  when `tf_vars` carries them (set in `provision()` only when an `InfrastructureCertificate`
  row is `ISSUED`) — with them absent, the generated HCL is byte-identical to before this
  feature (golden-tested). `certificate_arn` is validated at the interpolation sink
  (`_validate_certificate_arn`, bound to the infra's own region+account) exactly like the
  EKS `account_id`/`cluster_version` sinks. Reconcile-apply (`is_update`, keyed on
  `first_activated_at`) was not touched — the re-provision the ISSUED re-check triggers
  goes through the existing path unchanged.
- **EKS group name.** `eks_bootstrap.py`'s `_ingress_group_name` uses `infra.dns_label`
  (falling back to the old `str(infra.id)[:8]` only when no label exists) instead of a
  UUIDv7 prefix — CLAUDE.md bans truncated-UUIDv7 namespace keys since the leading 48 bits
  are a millisecond timestamp, forceable from the row's own `created_at`. **Migration
  behavior:** `_ensure_ingress_class` only *creates* the `IngressClassParams` object
  (`_get_or_create` swallows a 409 and never updates it), so an already-bootstrapped
  cluster's group name is unaffected regardless of what this function computes on a later
  call — no ALB recreation, no edge cutover, no downtime. Only a cluster bootstrapping for
  the first time after this ships gets the new label-based group.

### Security review fixes (post-push, same branch)

An independent review of the first part 2 push (`33cfb37`) returned BLOCK. All findings
addressed on the branch before it was force-pushed:

- **B1 (resurrection via background ISSUED transition).** `run_worker.py`'s TLS re-check
  ran outside any lock and, on ISSUED, called `enqueue_provision` with no check that the
  infrastructure hadn't since started tearing down — a certificate issuing after teardown
  timed out, or after a failed destroy parked the environment in ERROR, could resurrect it
  via a fresh terraform apply. Fixed with a shared `_cert_recheck_eligible(infra, env)`
  gate (`dns_teardown_requested_at is None` and `env.status == 'ACTIVE'`) applied to every
  action the re-check tick can take, plus a defense-in-depth check inside `provision()`
  itself that no-ops (logged) when the teardown marker is set or status is
  DESTROYING/DESTROYED.
- **R1 (orphaned cert on any failure between RequestCertificate and the poll succeeding).**
  `cert_arn` is now persisted immediately after `RequestCertificate`, before the
  DescribeCertificate poll — a timeout/AccessDenied/crash during the poll leaves a row the
  periodic re-check can still find and eventually time out, instead of a null-ARN row it
  silently ignores forever. A `CertificateShapeError` specifically (ACM handing back a
  wrong `DomainName` or a malformed validation record — not transient, never fixable by
  retrying the same cert) now deletes the certificate and marks `FAILED` immediately.
- **R2 (constant IdempotencyToken).** `sha256(infra_id)` handed a post-timeout retry the
  just-deleted certificate's ARN back within ACM's ~1h dedup window. Now
  `sha256(f"{infra_id}:{tls_requested_at.isoformat()}")`, unique per attempt since
  `tls_requested_at` advances (monotonically) on every attempt.
- **R3 (unconditioned `acm:AddTagsToCertificate`).** Could tag any certificate in the
  account `ManagedBy=launchpad` and then delete it under `DeleteCertificate`'s own
  condition. Now conditioned on `aws:RequestTag/ManagedBy=launchpad` +
  `ForAllValues:StringEquals aws:TagKeys=["ManagedBy"]`; the policy note is rewritten to
  say plainly that this cannot be restricted to only Launchpad's own certificates (IAM has
  no usable condition key for that here) and is defense-in-depth, not a hard boundary,
  given the account-wide `iam:*` grant already in the base policy. `acm:ValidationMethod`/
  `acm:DomainNames` conditions on `RequestCertificate` were considered and NOT added —
  AWS's IAM reference does not document ACM-specific condition keys, and a nonexistent key
  in a condition evaluates false, which would deny the grant outright; tracked as a
  REAL-AWS-VALIDATION item instead of guessing. v4 was unreleased, so its hashes were
  re-bound via `generate.py --write` rather than bumped to v5.
- **R4 (POLICY_STALE never recovers).** Neither policy-refresh callback re-enqueued
  provisioning. `cert_bootstrap.maybe_reenqueue_after_policy_refresh(infra_id)` re-reads
  current state and enqueues once `policy_version >= MIN_POLICY_VERSION_FOR_TLS` and the
  environment is ACTIVE (and not torn down); wired into both `views/script_api_key.py`'s
  and `views/infrastructure.py`'s policy-version-write paths.
- **Recommended, all applied:** `delete_acm_certificate_after_listener_removed` now
  retries `ResourceInUseException` with backoff (~6×10s) before giving up and logging the
  orphaned ARN at ERROR (the misleading "the reaper will re-drive this" comment was wrong
  — a failed destroy parks the environment in ERROR, which the reaper does not re-enqueue
  — and is corrected), and goes through the same `cert_bootstrap._acm_client` mock/dev
  gate as every other ACM call instead of a raw `boto3.client`. The re-check also sweeps
  ISSUED-but-`https_listener_arn`-still-null rows to recover a lost `enqueue_provision`
  call (e.g. a Redis dedup key already held), under the same B1 gate. The re-check now
  calls a new `assume_role_credentials_only` (authenticate.py) rather than
  `authenticate_infrastructure` — the latter writes `is_cloud_authenticated`/`metadata` on
  every call, which a transient failure in this background poll must not flip on the
  customer-facing row — and the tick has its own time budget
  (`CERT_RECHECK_TIME_BUDGET_SECONDS`, default 20s) so a long queue of PENDING rows can't
  block the worker loop indefinitely. Certificate reuse now skips non-`AMAZON_ISSUED`
  certificates and follows `NextToken` pagination. Host-mode nginx (unwired, see below) was
  redesigned from a single path-matched server block to two server blocks dispatched by
  Host header — the app's own hostname gets the real serving block (no redirect location
  at all, so a real app route starting with the app's own name, e.g. `/api/users` on app
  "api", can never be misrouted), and every other Host gets a redirect-only block that
  never proxies to the backend; `app_hostname` is validated against a strict hostname
  shape before being interpolated into the config (it is a config-injection sink, not just
  a cosmetic value), `app_name` is `re.escape`d in the regex location, and the redirect's
  capture group excludes `\r`/`\n` to close a response-splitting angle.

### Scaffolded but not wired into the live deploy path (honest deferral, not an oversight)

The following are real, tested code — not stubs — but are not yet called from
`application_deployment_service.py`'s deploy flow, because doing so needs a cross-service
contract (`dns_label`, `PLATFORM_BASE_DOMAIN`, TLS/listener readiness) that
application-service's `Environment` model does not yet mirror, and a decision on how a
per-app "routing mode" is chosen and persisted per deploy. Building that contract and
wiring these together is part 3's job alongside custom domains:

- `container_config.generate_nginx_config(..., host_mode=True, app_hostname=...)` and
  `inject_routing_envs(..., host_mode=True)` — host-mode nginx config (dedicated
  `/_lp_health`, no rewrite/301/`X-Forwarded-Prefix`/`ROOT_PATH`, `X-Forwarded-Proto` from
  `$http_x_forwarded_proto`, and a redirect from the old path URL to the host URL) is
  fully implemented and tested; path mode (the default) is untouched and golden-tested
  byte-identical.
- `aws/alb.py`'s `create_host_forward_rule` (443, host-header → forward),
  `create_host_redirect_rule` (:80, host-header → redirect-only, never forward), and
  `modify_target_group` (existing-TG health-check-path cutover) — implemented and tested,
  not yet called from the deploy flow.
- k8s readiness probe path lockstep and per-app EKS `Ingress` host rules
  (`rules[].host` exact match, `certificateARNs`/`listenPorts` on `IngressClassParams`) —
  not started; `eks_bootstrap.py`'s scope in part 2 was the group-name fix only.
- Host URL publish gating (API + dashboard, gated on Route53 INSYNC + `tls_status=ISSUED`
  + `Environment.https_listener_arn` set) — `Environment.https_listener_arn` and
  `InfrastructureCertificate.tls_status` are the building blocks; no endpoint or dashboard
  change surfaces them yet. `PlatformDnsRecord.synced_at` (new field, part of the INSYNC
  signal) is defined but not yet populated — `converge.py`'s `change_resource_record_sets`
  call doesn't poll `get_change`/stamp it. This is the clearest remaining gap against the
  pre-review's line 19 ("host URL only after Route53 INSYNC + cert ISSUED + 443 applied")
  and should be the first thing part 3 picks up.

## Part 1 — what shipped (mock-verified)

Everything in the security pre-review's §1, §3, and the RECOMMENDED CAA item in §5, split
out as its own PR ahead of the ACM/listener/nginx work in part 2:

- **Process isolation.** `LAUNCHPAD_PROCESS_ROLE=dns_writer` makes JWT_SECRET,
  INTERNAL_API_TOKEN, AWS_ACCESS_KEY_ID/SECRET optional and asserts them ABSENT at startup
  (`api/common/envs/application.py`); `AppConfig.ready()` starts no consumer threads in
  that role (`api/apps.py`). The mirror tripwire — every other process refuses to start if
  `PLATFORM_DNS_SECRET_ACCESS_KEY`/`PLATFORM_DNS_ACCESS_KEY_ID` leak into its env — lives in
  `shared/process_role.py` and is called from both infrastructure-service's and
  application-service's `ApplicationConfig.from_env()`.
- **Writer entrypoint.** `manage.py run_dns_writer`, a `ResilientPikaConsumer` on its own
  queue (`infrastructure-service.platform-dns-writer`), asserting `sts:GetCallerIdentity`
  resolves to the dedicated writer user in `PLATFORM_DNS_ACCOUNT_ID` before consuming
  (skipped in dev/mock). `manage.py sweep_platform_dns` is the periodic orphan sweep.
  Message handling's testable core is `platform_dns/dispatch.py:process_reconcile_message`;
  it distinguishes a data problem (`InvalidDnsRecordError`/`MockRealMismatch`/
  `PlatformDnsMisconfigured` — e.g. a hostile or wrong-region `alb_dns` on an EKS infra,
  whose Ingress status is customer-writable) from a transient one: the former is a discard
  (`requeue=False`), never a requeue, which with `prefetch_count=1` would otherwise
  redeliver forever and starve every other infra's reconcile behind it. The coalescing
  dedup key (see below) is cleared at the *start* of processing, before converging — clearing
  it after would drop a second trigger that arrived mid-processing.
- **Message contract.** `{infrastructure_id}` only, published by
  `api/services/platform_dns/producer.py:request_dns_reconcile`. The writer derives every
  name and value from the DB (`desired_state.py`) — a forged or replayed message can only
  re-converge that one infrastructure's own current state.
- **Validation before any Route53 call.** `api/services/platform_dns/naming.py`: exact
  name shapes for edge/wildcard/validation records, the `[0-9a-f]{16}` label regex, the
  ELB-hostname regex bound to the infra's own AWS region (blocks a claimable/wrong-region
  edge target — relevant on EKS, where `alb_dns` is a customer-writable Ingress status),
  and the `.acm-validations.aws.` validation-value suffix.
- **`PlatformDnsRecord` ledger.** Plain `infrastructure_id` field (not a FK), inserted
  before every UPSERT, deleted only after a confirmed Route53 DELETE. Survives an
  `Infrastructure` hard delete by design — `delete_infrastructure` now refuses a hard
  delete while any ledger row (or a set `InfrastructureCertificate.tls_requested_at`) is
  still live, checked at both hard-delete sites (`api/services/infrastructure.py` and
  `run_worker.py`'s post-DESTROYED cleanup). `InfrastructureCertificate` is created now,
  populated by part 2.
- **Teardown ordering.** `TerraformWorker.destroy()` calls
  `request_and_await_dns_teardown` — which sets the monotonic
  `Infrastructure.dns_teardown_requested_at` marker, publishes the reconcile, and polls the
  ledger — before `_pre_destroy_cleanup` and before the `AssumeRole` call. If teardown
  isn't confirmed within the timeout it raises, and `destroy()`'s outer exception handler
  only logs (does not touch `Environment.status`), so the environment stays `DESTROYING`
  for the reaper to re-enqueue. `_handle_provision_failure`'s rollback-destroy branch calls
  only `request_dns_reconcile` (best-effort, not `request_and_await_dns_teardown`) — that
  branch parks a never-activated infra in ERROR with the row still alive, so it must not
  set the monotonic `dns_teardown_requested_at` marker: a later retry that re-provisions
  from ERROR needs DNS again, and that marker never clears once set. Desired state is
  gated on `dns_teardown_requested_at`, never `Environment.status`,
  because the reaper rewrites status (see `infra-worker-lock-invariants` memory) and status
  is gone entirely once the row is deleted.
- **Mock/real gate.** `route53_client.get_route53_client` mirrors the existing
  `is_mock`/`dev_mode` gate in `TerraformWorker`: both mismatches raise, production with an
  unconfigured zone raises. Mock/dev gets an in-memory `FakeRoute53Zone`, process-local
  only (see Decisions below).
- **Terraform.** `infra/platform-dns`: added the
  `route53:ChangeResourceRecordSetsRecordTypes = ["CNAME"]` condition (+ matching Null
  guard) as a second, independent backstop alongside the name-shape condition; CAA records
  at the apex (`issue`/`issuewild` "amazon.com"); a CloudTrail trail (`cloudtrail.tf`,
  `include_global_service_events = true`) feeding an EventBridge rule + SNS topic alerting
  on a denied `ChangeResourceRecordSets`. The rule/target/topic are pinned to an aliased
  `aws.us_east_1` provider regardless of `var.aws_region` — global-service CloudTrail
  events are only ever delivered to EventBridge's default bus in us-east-1, so a rule in
  any other region would silently never fire. README and `REAL-AWS-VALIDATION.md` updated.

**Deferred to part 2/3, explicitly:**

- The two-phase teardown split (wildcard/edge deleted immediately vs. the validation CNAME
  deleted only after `acm:DeleteCertificate`, per §3's exact ordering) isn't implemented —
  there is no code path that creates a validation record yet, so a single-phase "desired
  state is empty" teardown is equivalent today. `teardown.py:delete_acm_certificate_after_listener_removed`
  is the named hook part 2 fills in; when it does, `desired_state.py` needs a second signal
  (something like "cert confirmed deleted") so a teardown reconcile can delete
  wildcard+edge in one pass and defer validation to a second pass.
- `core/settings.py`'s `RESERVED_DOMAIN_SUFFIX` fail-open default (`launchpad.app` when
  `PLATFORM_BASE_DOMAIN` is unset) is *not* removed in part 1 — that's §4/H3 (custom
  domains), where it was originally flagged as a spoofing risk. Part 1 only consolidated it
  onto one settings constant (`settings.PLATFORM_BASE_DOMAIN`) shared with the writer, per
  the "one settings source" ask in the pre-review's part-1 scope; the fail-closed removal
  is part 3's job.
- The in-memory `FakeRoute53Zone` is process-local, not DB-backed — a dev-mode reconcile
  from one process (a shell, a test) is invisible to another (`run_dns_writer`). Documented
  as a known limitation rather than built out, since nothing in part 1's tests needs
  cross-process mock state; revisit if that changes in part 2/3.
- Coalescing is producer-side only (a short Redis debounce, bypassed with `coalesce=False`
  on the destroy path) — no consumer-side coalescing/batching across multiple queued
  messages for the same infra. Convergence is idempotent either way; this only affects
  Route53 call volume under a burst, not correctness.

## Decisions (settled by #75)

## Decisions (settled by #75)

1. **Platform DNS zone:** a dedicated AWS account, applied from `infra/platform-dns/`. The
   writer is an IAM user with no `sts:AssumeRole`, scoped to one zone.
2. **URL shape:** two-label, `{slug}.{dns_label}.{PLATFORM_BASE_DOMAIN}`, so each
   infrastructure's wildcard certificate covers only its own hostnames.
3. **Domain:** `launchpad.aklamaash.me`, a delegated subdomain — the root zone and its mail
   are not in the platform zone at all.

**Owner action, still pending:** apply the terraform and add the `launchpad` NS record to
`aklamaash.me` — see `REAL-AWS-VALIDATION.md`.

## What the shipped IAM guard does *not* do — and so the writer must

H1 asked for a `Deny` unless the record name matches **the per-infra label pattern**. IAM
cannot express that: the policy (#75) permits any name two or more labels below the apex,
which covers every tenant alike. It protects the apex and single-label records; it does
**not** stop the credential writing tenant A's `edge.` record on behalf of tenant B.

**Cross-tenant isolation is therefore the DNS writer's job, and it is the centre of this
feature:**

- **Own process.** The writer runs as its own process (a dedicated RabbitMQ consumer /
  management command in infrastructure-service) and is the *only* process given
  `PLATFORM_DNS_*` credentials. The provisioning worker and the web process never hold them.
- **Names are derived, never accepted.** Requests carry `(infrastructure_id, kind, value)`
  with `kind ∈ {edge, acm_validation}`. The writer loads the infrastructure, reads its
  `dns_label` itself, and builds the record name. A caller cannot supply a name.
- **Every built name is asserted** to end in `.{dns_label}.{PLATFORM_BASE_DOMAIN}` before
  any Route53 call; ACM validation names are accepted only if they are exactly what ACM
  returned for that infrastructure's own certificate request.
- **Fail closed in production** when the zone is unconfigured; an in-memory fake zone when
  `LAUNCHPAD_MOCK` is set or the infrastructure `is_mock`.
- **Alerting on denied writes** — CloudTrail → EventBridge rule on
  `ChangeResourceRecordSets` with `errorCode = AccessDenied` in the DNS account. The #75
  README calls this required and it was owned by nothing; it is a terraform addition to
  `infra/platform-dns` in this feature.

## Verified on main at `9c8743d`

**Done (#68):**

- `Infrastructure.dns_label` — `secrets.token_hex(8)`, unique, minted at create with
  collision retry, never derived from `id`.
- `ReservedDnsLabel` tombstone — a plain UUID field, not a FK, so it survives the
  deliberate hard-delete of an infrastructure row. Labels are never reissued.
- Backfill migration for pre-existing rows.
- `CustomDomain` with the H3 state machine: `PENDING`/`VALIDATED`/`DISABLED`, exclusivity
  only at `VALIDATED` enforced by a partial unique index, 72h claim expiry, suffix
  rejection on the normalised punycode-decoded hostname, `last_verified_at`.

**Not built — all of it:** cert bootstrap, the platform DNS writer, the `edge.` indirection,
teardown, the re-validation job, nginx dual-mode, the 443 listener, the health-path change.

**One roadmap correction:** it says `container_config.py` gains a `routing_mode`. That file
does not exist. The nginx sidecar config is inline in
`application-service/aws/ecs.py`.

## Design

Per-infrastructure wildcard `*.{dns_label}.{PLATFORM_BASE_DOMAIN}`, issued **in the
customer's account**, DNS-validated via a CNAME Launchpad writes into its own zone. An
`edge.{dns_label} → {alb_dns}` indirection sits between the wildcard and the ALB so an ALB
recreation is a one-record platform fix, not a customer ticket.

**The 443 listener** goes in `modules/alb` as a conditional resource, default off so ECS
plans stay byte-identical — not created via boto3, which would drift and poison every
future apply. Reconcile-apply already exists (built by the managed-database work:
`is_update` in `terraform_worker.py`, keyed on `first_activated_at`, not status, because
the reaper overwrites status). **Do not rebuild it.**

**EKS uses Ingress annotations**, not the ALB path — EKS environments have
`alb_arn = None` and the load balancer is controller-owned.

**The health-path trio ships in one deploy or targets go unhealthy:** the nginx `location`,
the ALB target-group health-check path, and the k8s readiness probe. Host mode must also
drop the 301, the rewrite, `X-Forwarded-Prefix` and the `ROOT_PATH` injection in lockstep,
or frameworks keep emitting prefix-qualified URLs and every link breaks.

**Teardown (H2)** at all three destroy entry points, keyed on a monotonic column, delete
order wildcard → `edge` → cert → validation CNAME. A dangling `edge.` CNAME to a deleted
ALB is textbook subdomain takeover; a retained validation CNAME permanently authorises a
*former* customer's account to issue certs for a Launchpad hostname. Fail closed in prod
when the zone is unconfigured rather than marking an environment ACTIVE with a URL that
never resolves.

## Files

Terraform `modules/alb` (conditional 443 listener) · a platform DNS writer behind its own
process or internal endpoint · cert bootstrap service · `application-service/aws/ecs.py`
(nginx dual-mode) · teardown at three entry points · a periodic re-validation job ·
`CustomDomain` API + UI.

## Tests

`test_cert_bootstrap.py` (idempotency, ISSUED poll success/timeout, validation CNAMEs
never orphaned, platform-DNS paired hard gate) · `test_listener_rules_host.py`
(`get_listener_arn` returns :80 when :443 exists — the fix is already in, this pins it;
SNI cap) · teardown at each entry point · re-validation → `DISABLED` pulls the listener
rule. **Golden non-regression:** ECS terraform output string-identical with `enable_https`
unset; nginx path-mode config string-identical.

## Security pre-review

**Required, and this is the highest-risk feature remaining.** The platform DNS zone is the
first shared all-tenant asset in a product sold on "nothing of yours lives with us". H1,
H2 and H3 are all live here. Review the design before writing the DNS writer, not after.

## Out of scope

Apex/root custom domains (CNAME-only, documented). Migrating existing apps off path URLs —
host URLs are additive. Any traffic transiting platform infrastructure: DNS and certs only.
