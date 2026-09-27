# F1b — TLS activation and custom domains

**Status:** Phase 1 plumbing done (#68); decisions + zone terraform done (#75); **part 1
(platform DNS writer + ledger + teardown) done, mock-verified**; parts 2 (cert bootstrap,
ACM policy grants, 443 listener, nginx host mode, EKS ingress, host URLs) and 3 (custom
domains) not started.
**Depends on:** #68, #75 · **Blocked by:** the owner action below (zone not yet applied to
real AWS, so part 1 is mock-verified only) and parts 2–3 not yet built.

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
