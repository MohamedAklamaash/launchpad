# F1b — TLS activation and custom domains

**Status:** Phase 1 plumbing done (#68), activation not started
**Depends on:** #68 · **Blocked by:** three decisions and one owner action — see below

## Blocked on, before any code

These are not research questions. Nothing in this feature can be designed until they are
answered.

1. **Which AWS account holds the platform DNS zone, and where do its credentials live?**
   H1 constrains the answer: it must be a **separate IAM principal with no
   `sts:AssumeRole`**, scoped to `ChangeResourceRecordSets` / `GetChange` /
   `ListResourceRecordSets` on one hosted zone, with a `Deny` unless the record name
   matches the per-infra label pattern. Putting Route53 write on the existing AssumeRole
   user would mean one key compromise yields both every customer account *and* authority
   to mint valid certs for any platform hostname. The writer should also live outside the
   provisioning worker's address space — that process already holds `JWT_SECRET`,
   `INTERNAL_API_TOKEN`, DB credentials and the platform AWS keys.
2. **URL shape.** Accept two-label `{slug}.{dns_label}.launchpad.app`? Single-label
   requires accepting a cert valid for *every* tenant's hostname sitting in *every*
   tenant's account.
3. **Is the platform domain literally `launchpad.app`?** Everything parameterises on
   `PLATFORM_BASE_DOMAIN` either way.

**Owner action, start now regardless:** create the public hosted zone and delegate NS.
That is wall-clock, not work, and `dig NS <domain>` must resolve publicly before anything
here can be tested.

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
