> **Historical document — read `plan/README.md` first for current status.**
>
> This was written against the unmerged `feat/eks-deployment-target` branch, which merged
> last (#65). For the whole implementation run it described a codebase that did not exist,
> and its factual claims aged badly: **every line number is wrong**, the truncation gap was
> four sites and not three, the immutable image tag was never built rather than built-and-
> ignored, `container_config.py` did not exist, reconcile-apply (Open Question 1) had
> already been built by the managed-database work, and the `policy_version` delivery
> mechanism already existed.
>
> Its *strategy* held up well — the audit-forced reorder is what made the EKS merge
> possible, and the allowlist-over-denylist call was later vindicated by a real leak. Its
> **security pre-review is the most valuable part of this file** and is reproduced below
> unchanged.
>
> Verify every factual claim against `main` before acting on it. The per-feature files in
> this folder carry claims that were re-verified at `9c8743d`.

# Platform roadmap: TLS/domains, logs, rollback, cost, compliance, exit export

Six features, sequenced into phases. Each preserves the BYOC invariant that makes the
product defensible: **certs, keys, logs, Terraform state and cost data all stay in the
customer's account; the platform holds only DNS and metadata.**

Status: plan only, nothing implemented. Companion docs: `docs/EKS_DEPLOYMENT_TARGET_PLAN.md`,
`docs/EKS_IMPLEMENTATION_STATUS.md` (EKS shipped in #65).

---

## Decision summary 

- **Cert topology: one wildcard ACM cert per infrastructure**, `*.{dns_label}.launchpad.app`,
  issued *in the customer's account*, DNS-validated via a CNAME the platform writes into its
  own zone. App URLs become `https://{slug}.{dns_label}.launchpad.app`. `dns_label` is a
  **random unique column, never a truncated `Infrastructure.id`** — see the security review.
- **The nginx sidecar stays**, in a dual mode. It is not just a path-stripper.
- **Rollback rides on a new append-only `Deployment` model.** Two pre-existing bugs are
  prerequisites, not optimizations.
- **Cost is hybrid**: tags + Cost Explorer actuals for ECS; requests×pricing estimates for
  EKS, clearly labelled as estimates.
- **One consolidated customer IAM policy bump**, plus `Infrastructure.policy_version`.
- **Recommended order deviates from the obvious 1→6.** See *Sequencing*.

## Goals

- Every app reachable at a stable HTTPS URL immediately after deploy; custom domains
  attachable with ACM certs validated in the customer's account.
- Provisioning and runtime logs in the dashboard, redacted, without storing customer log
  data on the platform.
- One-click rollback to any previously deployed image, restoring the config that shipped
  with it, skipping CodeBuild.
- Per-app monthly cost: actuals for ECS, labelled estimates for EKS.
- A machine-generated, honest evidence pack per infrastructure, from one policy source of
  truth with a CI drift gate.
- An exit export that leaves a customer fully operational with Launchpad's role deleted.
- All migrations additive; mock/real hard gate at every new AWS/k8s seam; no new binaries;
  existing ECS behaviour unregressed.

## Non-goals

- No traffic ever transits platform infrastructure — DNS and certs only, no proxy/CDN.
- No apex/root custom domains in v1 (CNAME-only, documented rather than engineered around).
- No migration of existing apps off path URLs; host URLs are additive.
- No log storage or indexing on the platform; tail-on-demand only.
- No rollback of customer databases (nothing runs migrations today — keep it that way).
- No CUR/Athena pipeline; no feeding cost data into Stripe amounts in v1.
- No PDF renderer (evidence pack is JSON + Markdown; browser print covers PDF).
- Not resurrecting `modules/security` / `secrets` / `cloud_optimizer` — see the compliance
  section for why they must be *excluded*, not revived.

---

## Three corrections to the original pitch

The exploration disproved three assumptions the roadmap was pitched on. They change effort
estimates materially.

**1. The nginx sidecar does not delete itself.** It does four things and only one is
prefix-related: it serves the health endpoint at `location = /` (which *is* the ALB target-group
health check and the k8s readiness probe), maps WebSocket `Upgrade`/`Connection`, sets
`proxy_intercept_errors` so a down app returns 503 not 502 (this is what makes the ALB mark
targets unhealthy), and enforces body/timeout limits. Retiring it would force every customer
app to expose its own health endpoint — a breaking demand. It stays, dual-mode. TLS is
"one feature, net-neutral complexity, much better URL", not negative complexity.

**2. Rollback is not nearly free.** `application_deployment_service.py:295` hardcodes
`{slug}-latest` on ECS, so the immutable commit tag is built, pushed, and then ignored —
ECS rollback is impossible today. The deployed tag is never persisted. And
`project_commit_hash` is written once at create and never advanced by the GitHub webhook, so
every rebuild regenerates the *same* tag and overwrites it in ECR. **The per-deploy tag only
behaves immutably on EKS, and even there it repeats across pushes.** That is a live defect in
the shipped EKS work and should be fixed whether or not rollback ships.

**3. Cost attribution is not "mostly an API call".** `default_tags` reaches only
Terraform-created resources — the shared VPC, ALB, cluster, ECR. Every per-app resource is
untagged: ECS task definitions and services, ALB target groups and listener rules, the
CodeBuild project and its IAM role. On EKS the unit of attribution is a Kubernetes namespace,
which Cost Explorer cannot see at all. It is a cross-service tagging change plus an EKS
estimation strategy, with **no retroactive fix** for existing deployments. The missing `ce:*`
grant is the easy part.

---

## F1 — TLS, custom domains, host-based routing

### Options considered

| Option | Verdict |
|---|---|
| Platform-wide wildcard `*.launchpad.app`, single-label URLs | **Rejected.** ACM keys are non-exportable, so this cert must be requested *in each customer account* — placing a cert valid for **every other tenant's hostname** in every tenant's account. Cross-tenant TLS impersonation surface. |
| **Per-infrastructure wildcard `*.{dns_label}.launchpad.app`** | **Chosen.** Impersonation scoped per tenant; 3 platform DNS records per infra and zero per-app; 1 SNI slot; unlimited apps. Cost: two-label URLs (cosmetic). |
| Per-app SAN certs | Rejected. Tightest scoping but a ~24-app ceiling per infra against the ~25 SNI limit, plus per-app validation churn. |

An `edge.{dns_label} → {alb_dns}` indirection sits between the wildcard and the ALB, so an ALB
recreation becomes a one-record platform fix rather than a customer ticket.

### Getting 443 onto existing ALBs

The `:80` listener is Terraform-managed; per-app listener *rules* are boto3-created outside
state. Creating the 443 listener via boto3 would drift and poison every future apply.

**Chosen:** a conditional `aws_lb_listener.https` in `modules/alb` (default off, so ECS plans
stay byte-identical) plus a new **reconcile-apply job type** (`ACTIVE → UPDATING → ACTIVE`,
failure returns `ACTIVE`). The provisioning queue only runs on create/destroy today. This is
the same mechanism the managed-DB initiative needs — build it once.

### The sidecar's dual mode

`container_config.py` gains a `routing_mode`. `path` stays byte-identical to today. `host`
drops the 301, the rewrite, `X-Forwarded-Prefix`, and the `ROOT_PATH`/`UVICORN_ROOT_PATH`
injection — that unwind is **lockstep**, or frameworks keep emitting prefix-qualified URLs
and every link breaks.

Health moves to `/_lp_health` because in host mode the app's real `/` must pass through.
**Three things change in one deploy or targets go unhealthy:** the nginx location, the ALB
target-group health-check path, and the k8s readiness probe.

### Two mechanisms, split by compute_type

EKS environments have `alb_arn = None` and `target_group_arn = None` — the ALB is
controller-owned. Anything keyed on `alb_arn` cannot work there; EKS uses Ingress
annotations, with cert/listen-port config centralised on the bootstrap Ingress to avoid
load-balancer-controller group merge fights.

### Quotas that bound the design

~25 SNI certs per ALB (1 platform cert + a proposed cap of 20 custom domains per infra) and
~100 rules per listener (2 rules per app during dual-mode ≈ 48 apps per infra).

---

## F2 — Logs

**Chosen: on-demand proxy tail. Nothing stored on the platform** — storing customer log data
would undercut the BYOC pitch.

- Provisioning: a new owner-scoped endpoint serving redacted, capped `Environment.logs` plus
  the environment-level `error_message` — which today carries the real terraform failure
  reason and *never reaches the dashboard*.
- Runtime: CloudWatch `filter_log_events` on `/ecs/{family}` for ECS, `read_namespaced_pod_log`
  for EKS, both through the existing assumed-role session. **No new IAM needed** — task
  definitions already ship both containers' logs to CloudWatch and the policy already grants
  `logs:*`.

**Two preconditions before the endpoint ships.** `Environment.logs` holds raw terraform
stdout/stderr in `[INIT]`/`[COMMAND]` blocks and there is no log redaction helper today —
`_redact_metadata` covers metadata only. And truncation is inconsistent: `MAX_LOG_CHARS` is
applied at four write sites but missed at three (`terraform_worker.py:549`, `:590`, `:877`),
so an ERROR-path environment can hold an unbounded blob.

---

## F3 — Rollback

**Chosen: an append-only `Deployment` model** — app FK, `image_tag`, `commit_sha`, `status`,
`config_snapshot` (envs/cpu/memory/port), `triggered_by`, timestamps. A tag-pointer field on
`Application` was rejected: without a config snapshot, rolling the image back while today's
env vars stay applied is a silent-drift trap.

Rollback becomes a new branch that skips CodeBuild, restores the snapshot, and registers a
task definition pinned to the old tag (ECS) or patches image+env (EKS).

**Prerequisites, in order:**
1. The webhook writes the pushed SHA to `project_commit_hash`.
2. ECS deploys the immutable tag (`:295`), while `-latest` keeps being pushed so ECS does not regress.
3. An ECR lifecycle policy keeps the last N tagged images and never expires `-latest`.

Rollback restores config all-or-nothing, and the UI shows the diff before confirming.

---

## F4 — Cost attribution

| | Approach |
|---|---|
| **ECS** | Per-app tags (`launchpad:infra`, `launchpad:app`) + `enableECSManagedTags` + `propagateTags=SERVICE`, then Cost Explorer actuals. |
| **EKS** | Pod requests × published Auto Mode pricing × runtime. **Labelled as estimates.** Pods are not taggable AWS resources. |
| **Infra-level** | Cost Explorer grouped by the existing `InfraID` default tag, once activated. |

Split cost allocation data would give pod-level EKS actuals but enablement is a *payer-account*
setting Launchpad's role cannot reach for org member accounts — offered as documented opt-in,
not the default.

**Per-app tagging lands in the Rollback phase, not this one.** Tags are non-retroactive, and
that phase already touches every resource-creation call site — so cost data accumulates before
the cost UI ships. Caveats to state in-product: tags are not retroactive, CE lags ~24h, and tag
activation is payer-only for org accounts.

---

## F5 — Compliance evidence pack

The blocker is not the pack, it is that **the IAM policy has no machine-readable source of
truth.** It exists only as a bash heredoc with a conditional splice, is hand-duplicated twice
in `docs/IAM_POLICIES.md`, and **both copies have already drifted** — they are ECS-only,
missing the EKS Allow/Deny entirely.

**Chosen: policy statements as JSON data + a generator** that renders the `create_aws_role.sh`
heredoc section and `IAM_POLICIES.md`, with a CI job that regenerates and fails on diff. The
script stays self-contained offline-auditable bash — fetching the policy from an API at
runtime was rejected for exactly that reason.

The pack itself zips: the rendered policy for the infra's compute_type plus its version, the
trust-policy shape (ExternalId), a live `iam:GetRolePolicy` expected-vs-actual drift diff, a
capability narrative, and **honest limitations**.

Two things it must state plainly or it becomes a liability in a security review:
- `iam:*` on `*` means the EKS scoping is **defense-in-depth, not a containment boundary**.
  The script comment claiming `iam:*` is "scoped to launchpad-* roles in code" is false — the
  only enforcement is a naming convention.
- `modules/security` (CloudTrail + KMS), `modules/secrets`, and `modules/cloud_optimizer` are
  in the repo but **never instantiated** by either generated config. A pack that enumerates the
  module directory would over-claim CloudTrail and KMS coverage to a security reviewer. Exclude
  them explicitly.

---

## F6 — Exit export

**Good news:** Terraform state, its bucket (`launchpad-tf-state-{account}-{region}`) and lock
table already live in the customer's account, and the root config is deterministically
regenerable. Nothing to migrate.

**The catch:** the entire application layer is imperative — ECS task defs/services/target
groups/listener rules, the CodeBuild project and its IAM role, the EKS bootstrap
IngressClass/namespace/CNI patch, and every per-app k8s object. A Terraform state dump hands
the customer **a cluster with no applications in it.**

**Chosen: a continuity export, not an IaC reconstruction.** Everything keeps running if
Launchpad simply stops touching it; the export's job is documentation and handover. Tarball:
README with a full inventory and ARNs, revocation instructions (delete `LaunchpadDeploymentRole`),
the generated root `main.tf` + modules + a `backend.hcl` pointing at their existing state
bucket, cleaned k8s manifest dumps / ECS task-definition JSONs, and the buildspec as a CI seed.

Full Terraform import codegen for the imperative layer is the ideal end state but is L–XL and
brittle; it is not needed for continuity.

---

## Sequencing

**Phase 0 — prework.** Webhook SHA fix; `get_listener_arn` port selection; log truncation caps
+ redaction; policy JSON + generator + CI drift gate + `policy_version` + one consolidated
grant bump (`acm:*`, `ce:GetCostAndUsage`); gateway rate-limit carve-out.
*In parallel, owner action:* create the public hosted zone and delegate NS.

**Phase 1 — TLS plumbing**, dark behind `HTTPS_ENABLED=false`.
**Phase 2 — Logs.**
**Phase 3 — Rollback + per-app tagging.**
**Phase 1b — TLS activation + custom domains** (unblocks once DNS/certs/policy refresh land).
**Phase 4 — Cost.** **Phase 5 — Compliance pack.** **Phase 6 — Exit export.**

### Why this differs from the 1→6 order

1. **Logs and rollback run between TLS plumbing and TLS activation.** TLS activation is blocked
   on wall-clock externalities — NS delegation propagating, cert issuance, a manual customer
   policy refresh. Logs and rollback are pure software with zero DNS or IAM dependency.
2. **Per-app tagging lands with rollback, not cost** — tags are not retroactive, so start
   accumulating data early.
3. **The policy bump goes in Phase 0.** The refresh-policy UI has zero callers today, so every
   bump is a manual ask of every existing customer. Make it once.
4. **Exit export stays last** — it depends on rollback's tag pinning and the compliance
   generator.

### Hard gates

- The `get_listener_arn` fix must be **live in production before any 443 listener exists on any
  customer ALB.** It currently takes `describe_listeners()[0]`, so a second listener silently
  routes new rules to the wrong one. This is a deployment-ordering constraint, not a commit
  order.
- Webhook SHA + immutable-tag fixes before the `Deployment` model means anything.
- Policy extraction + `policy_version` before the compliance pack and before any new grant ships.
- The health-path trio ships in a single deploy.
- Reconcile-apply exists before any existing infra gets HTTPS.

---

## Test strategy

**Golden non-regression spine:** ECS terraform builder output string-identical with
`enable_https` unset; nginx path-mode config string-identical.

Named tests: `test_cert_bootstrap.py` (idempotency, ISSUED poll success/timeout, validation
CNAMEs never deleted, platform-DNS paired hard gate) · `test_reconcile_apply.py` (state
machine, failure returns ACTIVE, lock/heartbeat) · `test_listener_rules_host.py`
(`get_listener_arn` returns :80 when :443 exists; SNI cap) · `test_domain_model.py` (suffix
rejection, global uniqueness) · `test_logs_endpoints.py` (cross-tenant 404, seeded secrets
never surface, truncation at the three previously-missed sites) · `test_rollback.py` (restores
snapshot + old tag with no `start_build`; `-latest` still pushed; webhook advances SHA) ·
`test_tagging.py` · `test_cost_service.py` (estimate math, source labelling) ·
`test_policy_generator.py` (rendered == committed; CI gate red on hand-edit) ·
`test_evidence_pack.py` (drift diff; dead modules absent) · `test_exit_export.py`
(`backend.hcl` targets the right bucket; credential scan clean).

Mocks: `MockSession` gains `acm`, `elbv2`, `logs`, `ce`, `iam` stubs; `mock_k8s` gains pod
logs; a new `mock_platform_dns`. The unstubbed-raises behaviour forces stubs to grow with usage.

**Real-mode sandbox smoke before activation:** cert ISSUED end-to-end; `curl https://{slug}.{dns_label}...`
serves the app at `/`; legacy path URL intact; custom domain validates and serves; live
rollback flips an image; CE shows tagged cost after 24h.

---

## Risks

- **The platform DNS zone is a new all-tenant critical path** — the first shared platform-owned
  asset in a product pitched on "nothing of yours lives with us". Scope platform Route53 creds
  to one zone ID, log every record write, and rely on the `edge.` indirection plus per-infra
  certs to bound blast radius.
- **`get_listener_arn` deployed late** → the first 443 listener breaks every path deploy on that
  infra. Phase 0 hard gate plus a regression test.
- **Reconcile-apply can break live infrastructure.** Reuse the worker lock/heartbeat/reaper
  invariants; `UPDATING` failure returns `ACTIVE`; the golden test proves byte-identity when off.
- **Health-path lockstep miss** → all targets unhealthy, i.e. an outage. Single-deploy rule;
  host mode activates per-infra at cert attach with new `/_lp_health` target groups while old
  path rules keep serving.
- **ACM validation stuck** on unpropagated delegation: resumable idempotent poll, distinct
  sanitized error string mapped to a notification bucket, infra stays ACTIVE on HTTP throughout.
- **Custom-domain abuse:** suffix rejection, global hostname uniqueness, customer-DNS ACM
  validation as ownership proof.
- **Log redaction gaps:** the redaction filter with seeded-secret tests is a merge precondition
  for the endpoint, not a follow-up.
- **Rollback restores stale config onto a changed world:** all-or-nothing snapshot, diff shown
  before confirm.
- **Rollback path:** every phase is flag-gated; all migrations additive; the export writes
  nothing to customer accounts.

## Validation checklist

- [ ] Both Django suites pass unmodified after every phase; nginx path-mode and ECS terraform golden tests byte-identical.
- [ ] `get_listener_arn` fix live in production before any cert attach.
- [ ] `dig NS <platform domain>` resolves publicly before Phase 1b.
- [ ] New infra: HTTPS URL live post-provision; legacy path URL intact; app `/` reaches the app, not the health stub.
- [ ] Existing infra: reconcile adds 443 with zero path-URL downtime.
- [ ] Custom domain end-to-end, including suffix and duplicate rejection.
- [ ] Logs: cross-tenant denied; seeded secrets never surface; the three uncapped write sites fixed.
- [ ] Rollback restores old image *and* old envs; `-latest` still pushed; webhook advances SHA.
- [ ] Tags visible in Cost Explorer after activation + 24h; EKS figures labelled "estimate".
- [ ] CI drift gate red on a hand-edited heredoc; evidence-pack drift diff correct against a live-mutated policy.
- [ ] Exit tarball: `terraform init` with the bundled `backend.hcl` succeeds in a sandbox; zero credentials in the archive.
- [ ] No new binaries in images; every new seam has paired mock/real hard-gate assertions.

## Open questions

1. **Reconcile-apply ownership** — does TLS build it (managed-DB inherits) or does managed-DB land first?
2. **URL shape sign-off** — accept two-label `{slug}.{dns_label}.launchpad.app`? Single-label requires explicitly accepting a cert valid for every tenant sitting in every tenant's account.
3. **Platform DNS credentials** — which AWS account holds the zone, and where do its credentials live? This is the product's first platform-account AWS dependency; `CLAUDE.md` currently states nothing provisions platform infrastructure.
4. **Custom-domain cap** (proposed 20/infra) and whether apex domains are documented-out or hard-blocked.
5. **Cost-tag activation for org member accounts** — ship "infra-level only unless the payer activates", or add a payer onboarding step?
6. **Rate-limit carve-out shape** — per-route limits vs per-user limiting for authenticated endpoints.
7. **Evidence pack format** — is Markdown + JSON sufficient, or is PDF a buyer requirement?
8. **Is the platform domain literally `launchpad.app`?** Everything parameterizes on `PLATFORM_BASE_DOMAIN` regardless.


---

# Security pre-review

The design was audited before implementation. **Verdict: BLOCK — 1 CRITICAL, 6 HIGH.**
F1 as originally drafted could not be built. The findings are folded in below; the plan text
above has been corrected where it was wrong.

## CRITICAL — a truncated infrastructure id is not a tenant identifier

The first draft keyed DNS names and cert SANs on `{infra8}`, the first 8 hex chars of
`Infrastructure.id`. That id is **UUIDv7**, which leads with a 48-bit Unix-millisecond
timestamp — so the first 8 hex chars are `floor(epoch_ms / 65536)` and advance only once every
**65.5 seconds**. Verified empirically in this repo's venv: eight rapid `uuid7()` calls yield
**one** distinct prefix, unchanged after a 1.1s sleep. Roughly 1,318 distinct prefixes exist
per day across the entire platform.

For any two infrastructures created in the same ~65-second window this is a certainty, not a
birthday risk:

- `edge.{prefix}` — the second write **overwrites** the first, silently routing tenant A's
  production traffic to tenant B's ALB over a TLS connection that validates cleanly.
- `*.{prefix}.launchpad.app` — tenant B holds, in tenant B's own AWS account, a private key
  and cert valid for every one of tenant A's hostnames. TLS-terminating MITM with no browser
  warning.
- Per-infra slug uniqueness provides nothing once the namespace collides.
- The ACM validation CNAMEs collide too, so one tenant's cert renewal depends on who won the
  write.

**It is attacker-forceable, not merely accidental**: `created_at` is on the API response, so
the prefix is computable from a timestamp. An attacker who can approximately time a victim's
infra creation lands in their namespace deterministically, at a cost of one infrastructure per
65 seconds.

**Required:** a dedicated `Infrastructure.dns_label` column — `unique=True`, minted at create
from `secrets.token_hex(8)` (≥64 bits of real entropy), explicit collision retry, **never
derived from `id`, never reused after destroy** (tombstone it). Every DNS name, cert SAN and
record keys off `dns_label`. The general rule matters more than the fix: **never use a
truncated UUIDv7 as a namespace key** — the same defect would appear in bucket prefixes,
cluster names, or cache keys.

## Two premises in the first draft were factually wrong

**"This introduces the product's first platform-account AWS credential" — false.** A long-lived
static IAM *user* already exists (`api/common/envs/application.py:37-38`) and is the AssumeRole
principal named in every customer's trust policy. The plan is not adding a new credential class;
it is proposing to add Route53 write to **the most dangerous credential that already exists**.

**"`Environment.logs` has no redaction" — half true.** One targeted control exists:
`terraform_worker.py:614` persists only output *key names*, deliberately, because
`terraform output -json` prints `sensitive` values in cleartext. Everything else is raw. What
has made this tolerable is precisely the property F2 removes — the field is currently reachable
from no serializer, endpoint, or AMQP payload.

## HIGH findings

**H1 — Route53 write must not go on the AssumeRole principal.** Compromise of one key set would
mean both AssumeRole into every customer account *and* authoritative DNS for the platform zone
(and therefore the ability to mint valid certs for arbitrary platform hostnames). Required: a
separate IAM principal with its own env vars and **no `sts:AssumeRole`**; policy limited to
`ChangeResourceRecordSets`/`GetChange`/`ListResourceRecordSets` on one hosted zone; a `Deny`
unless the record name matches the per-infra label pattern, so a name-construction bug cannot
rewrite the apex, MX/SPF/DKIM, or another infra's records; CloudTrail alerting on
out-of-pattern changes. Also: the Route53 write should live behind a separate process or
internal endpoint, not in the provisioning worker's address space — that worker already holds
`JWT_SECRET`, `INTERNAL_API_TOKEN`, DB creds and the platform AWS keys.

**H2 — dangling DNS and dangling ACM authorization on destroy.** The draft specified record
creation and explicitly refused one deletion, but never specified teardown. Teardown has
**three** entry points plus a reaper that overwrites `Environment.status`. A dangling
`edge.{label}` CNAME to a deleted ALB is the textbook subdomain-takeover condition. Worse,
"never delete validation CNAMEs" permanently authorizes a **former** customer's AWS account to
issue certs for a Launchpad-branded hostname — a phishing asset that outlives the contract.
Required: DNS teardown at all three entry points keyed on a **monotonic column** (not status —
the reaper erases status gates); delete order wildcard → `edge` → cert → validation CNAME; the
rule is "delete the validation record when the cert it validates is deleted", not "never";
a scheduled orphan-reconciliation sweep; and **fail closed** in prod when the zone is
unconfigured rather than marking an environment ACTIVE with a URL that never resolves.

**H3 — ACM validation is evidence of control, not a sufficient ownership state machine.** Three
gaps: the global hostname is reserved at row creation, so first-writer-wins is squatting-by-design;
proof is never re-checked, so a domain that changes hands keeps its old routing; and nothing
binds the validated hostname to the account that proved it. Required: exclusivity granted only
on `VALIDATED` with `PENDING` claims expiring (~72h); no routing/cert attach before validation;
suffix rejection on the *normalized, punycode-decoded, lowercased* hostname (not naive
`endswith`); periodic re-validation with `last_verified_at`, transitioning to `DISABLED` and
pulling the listener rule on failure; and binding to `(hostname, infrastructure, cert_arn,
aws_account_id)`.

**H4 — a regex log redactor will miss the secrets that matter here.** Redaction must happen
**at write, before truncation**, not in the endpoint — otherwise raw text stays in Postgres,
backups and replicas, and is re-exposed by the next consumer. And a denylist is the wrong
shape: the EKS bearer token is `k8s-aws-v1.<base64 of a presigned STS URL>` embedding the live
`X-Amz-Security-Token` — it matches no `AKIA`/`ASIA` pattern and is concatenated straight into
`env.logs`. Terraform masks only `sensitive`-marked values, wraps long strings across lines
(defeating anchored regexes), and the existing `[-MAX_LOG_CHARS:]` truncation slices mid-token.
Required: an **allowlist** shape — persist phase markers, resource addresses, error codes and
known-safe templates; drop raw stdout by default; match directly on the known values in the
terraform vars dict rather than guessing patterns; redact-then-truncate; and a test asserting
every known secret string is absent. Separately, "owner-scoped" must be defined: an invited
ADMIN can currently act on a shared infrastructure, and provisioning logs carry the whole
account's topology.

**H5 — runtime log tailing is a new data-handling boundary.** This is the first feature where
Launchpad reads *customer application output* — request payloads, PII, their end-users' data.
"Nothing stored" is necessary but not sufficient: the gateway returns `str(exc)` in 500 bodies,
so a failure mid-stream can put customer log content into an error response and into platform
logs. Required: an explicit data-handling statement in customer docs; a hard no-store path
excluded from any error-reporting integration; log group and namespace derived **server-side
from the authenticated app record, never a request parameter**; a written authorization
decision for invited ADMINs; bounded limit/window; and an access log of who tailed what.

**H6 — the export is a secrets-bearing archive.** Three of its four contents carry secrets
today: k8s manifest dumps (`Secret.data` is base64, not encryption; Deployment `env` is
plaintext), ECS task-def JSON (`environment` is plaintext by definition), and the buildspec
(`codebuild.py:128` has a plaintext GitHub-token fallback). `backend.hcl` points at the
Terraform state bucket, and state stores generated passwords in plaintext. On a shared
infrastructure an invited ADMIN could exfiltrate the env vars of **every app, including other
users' apps**. Required: owner-only with invited ADMINs refused, re-authentication for this
operation, redacted-by-default (key names with `<redacted>` values), never persisted (temp dir
`0600`, stream, delete in `finally`), **no request-supplied path component**, size cap and
timeout, and an audit log.

**H7 — `config_snapshot` multiplies a plaintext exposure.** `Application.envs` is plaintext
today — one row per app. Snapshotting it per deploy makes it one row per deploy, forever,
append-only. That breaks rotation as a remediation (old values persist in every prior
snapshot), leaves no purge path for erasure requests or incident response, and adds two new
readers (rollback UI, export). Required: **snapshot the shape, not the values** — key names,
CPU/memory/port, image tag, commit SHA, plus a content hash — and re-read current env values
at rollback time. That is also the behaviour customers want: rolling back code should not roll
back a rotated credential.

## MEDIUM findings

- **`ce:GetCostAndUsage` cannot be scoped.** Cost Explorer has no resource-level permissions;
  the grant is account-wide financial-data disclosure covering spend unrelated to Launchpad.
  Present it plainly in the policy diff and docs, or drop ECS actuals and estimate both runtimes.
- **`acm:*` must not be wildcarded.** `acm:DeleteCertificate` on `*` lets a bug delete any cert
  in the account. Grant `RequestCertificate`/`Describe`/`List`/`AddTagsToCertificate`, and
  condition `DeleteCertificate` on the Launchpad resource tag (so tag certs at creation).
- **`policy_version` with no delivery mechanism is silent-failure design.** Build the
  refresh-policy UI as part of this work, and add a **preflight capability check**
  (`iam:GetRolePolicy` vs required version) that fails fast with an actionable message instead
  of a terraform stack trace. F5's drift machinery *is* that check — build it first.
- **The rate-limit carve-out cannot mean what it says.** The gateway does not verify JWTs, so
  it cannot distinguish authenticated traffic; `EXEMPT_PATHS` is exact-match so an ID-bearing
  log path can never match (and prefix-matching would silently widen it); and exempt means
  *zero* limit on the most expensive endpoints — each triggers an AssumeRole plus a CloudWatch
  or k8s API call **in the customer's account**, billing them and exhausting their API
  throttle. The limiter also fails open on Redis errors. Required: a per-user/per-app budget
  enforced where identity is actually verified, keeping the IP limit as the outer bound. Add
  nothing to `EXEMPT_PATHS`.
- **`/_lp_health` becomes a platform-reserved path on every customer hostname.** Restrict it to
  the ALB/VPC source or it is an unauthenticated liveness oracle; document the collision.
- **Reconcile-apply re-enters the terraform generator on live infrastructure.** A customer-supplied
  custom domain now reaches the repo's highest-value injection sink — it must go through
  `validate_infra_metadata` and `json.dumps` like the EKS builder. And `UPDATING` must not be
  the safety gate: the reaper re-enqueues and overwrites status, so use a monotonic column plus
  the existing lock/heartbeat.

## What the audit confirmed clean

The terraform subprocess env allowlist, removal of STS credential persistence at both writers,
`terraform output -json` value suppression, slug uniqueness under `select_for_update`, the
60-second signed EKS token, and the hashed single-use onboarding token. F3/F6 must not
reintroduce a credential-persistence path.

## Ordering change forced by the audit

**Build F5's policy generator + CI drift gate first.** F1's `acm:` and F4's `ce:` grants are
exactly the edits that gate exists to protect, and F5's `iam:GetRolePolicy` diff is the
preflight check F1 and F4 need to fail fast. Fold the pre-existing `iam:*`/`kms:*` narrowing
into the same policy bump — it is the one moment customers will be asked to re-run the script.
