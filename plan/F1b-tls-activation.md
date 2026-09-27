# F1b — TLS activation and custom domains

**Status:** Phase 1 plumbing done (#68), activation not started
**Depends on:** #68 · **Blocked by:** three decisions and one owner action — see below

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
