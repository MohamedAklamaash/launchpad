# F1b — TLS activation and custom domains

**Status:** Phase 1 plumbing done (#68); decisions + zone terraform done (#75); **part 1
(platform DNS writer + ledger + teardown) done, mock-verified**; **part 2 (cert bootstrap,
ACM policy grants, 443 listener, nginx host mode groundwork, EKS group-name fix) done,
mock-verified**; **part 3a (host URLs end-to-end: cross-service readiness contract, ECS/EKS
deploy-flow wiring, DNS synced_at, host URL publish gating, backfill) done, mock-verified —
see Part 3a below**; **part 3b (customer custom domains: claim/verify/list/delete API +
dashboard UI, authoritative-TXT ownership proof, per-domain ACM issuance, ALB SNI cap +
attach/detach, teardown at all three entry points, periodic re-validation, fail-closed
`RESERVED_DOMAIN_SUFFIX`) done, mock-verified — see Part 3b below**. F1b is feature-complete
against mocks. **Depends on:** #68, #75, part 2 · **Blocked by:** the owner action below
(zone not yet applied to real AWS, so every part above is mock-verified only) — see
`plan/REAL-AWS-VALIDATION.md` for the real-AWS checklist every part still owes.

## Part 3a — what shipped (mock-verified)

Scope: the plan's "Scaffolded but not wired" list from part 2 (below), narrowed to host URLs
— custom domains are a separate, not-yet-started slice.

- **Cross-service contract.** A new event, `infrastructure.host_readiness_updated`
  (`api/services/host_readiness.py`, infrastructure-service), carries a fresh snapshot
  (`dns_label`, `tls_status`, `dns_synced`, `https_ready` — the last two computed, not
  copied verbatim from any single table) to application-service's read-model
  (`api/messaging/consumers/infrastructure.py:HostReadinessEventConsumer`). Published from
  every place any of those four can change: `terraform_worker.py._save_outputs`,
  `cert_bootstrap.ensure_certificate` (`finally`, so it fires on every outcome), the two
  transitions in `run_worker.py.check_pending_certificates`, and the DNS writer's own
  `dispatch.converge_from_infra_id` after a successful converge — the writer publishing a
  read-model event needs no `PLATFORM_DNS_*` credential (`rabbitmq_url` is not one of the
  vars `LAUNCHPAD_PROCESS_ROLE=dns_writer` restricts), so this does not weaken its
  isolation. `dns_label` also travels on the existing `infrastructure.created` payload
  (fires on every apply) as a second delivery path, since it never changes once minted.
  application-service's consumer never trusts the payload for anything but read-model
  fields, follows `InfraUpdatedEventConsumer`'s retry-until-materialized pattern, and
  compares the event's own `occurred_at` against a stored `host_readiness_occurred_at`
  before applying — this service's three publishers (a terraform apply, the TLS re-check
  tick, the DNS writer) give no cross-publisher ordering guarantee, so a stale snapshot must
  never clobber a fresher one.
  **Decision:** the raw `https_listener_arn` is not mirrored — application-service's ECS
  deploy flow gets the authoritative value with a live `describe_listeners` call right
  before creating a rule (exactly like the existing `:80` listener lookup), which can never
  be stale the way a mirrored value could. `https_ready` (compute-type-normalized: ECS's
  `Environment.https_listener_arn` set, EKS's new `Environment.eks_ingress_tls_ready` set)
  is still mirrored as a cheap DB-only pre-check for both compute types. **Updated by the B1
  security fix:** EKS *does* now also get a live check on every deploy
  (`EKSDeployer._verify_eks_https_listener`) — `https_ready`/`eks_ingress_tls_ready` proves
  only "the IngressClassParams patch was accepted by the k8s API," never "the ALB listener
  actually exists," and the live check is what closes that gap before host mode is granted.
- **`PlatformDnsRecord.synced_at` population** (`api/services/platform_dns/converge.py`).
  After a successful `change_resource_record_sets`, an INSYNC status on the response itself
  (the mock zone, and occasionally a real one) stamps immediately; otherwise a bounded poll
  (~60s, `_SYNC_POLL_ATTEMPTS`/`_SYNC_POLL_INTERVAL_SECONDS`) runs after the ledger
  transaction has committed and its advisory lock released, never inside it. A row whose
  poll times out keeps its `change_id` and is picked up by a one-shot `GetChange` catch-up
  (`_stamp_pending_syncs`) at the *start* of the next reconcile for that infra, even one
  that itself has nothing new to upsert or delete — closing the gap the early-return check
  would otherwise leave. New `PlatformDnsRecord.change_id` field (additive migration); no
  grants SQL change needed — the writer's Postgres role already has table-level
  SELECT/INSERT/UPDATE/DELETE on `api_platformdnsrecord` (see `platform_dns/sql/
  dns_writer_grants.sql`), and this is a new column on a table it already owns outright, not
  a new table. **Known gap, documented rather than built out:** an infra whose only
  remaining item is DNS propagation, and which never triggers another reconcile for any
  other reason, could in principle stay unsynced past the poll bound — tracked in
  `REAL-AWS-VALIDATION.md` as a candidate for a periodic sweep (`sweep_platform_dns`'s
  pattern) if it's ever observed in practice.
- **`api.services.host_readiness.is_dns_synced`**: true only when BOTH the edge and
  wildcard ledger rows exist and have a non-null `synced_at` — a lone validation record (the
  state before the first successful apply) never counts.
- **EKS TLS apply** (`api/services/eks_bootstrap.py:apply_eks_tls`), the EKS counterpart to
  ECS's conditional 443 listener: a merge-patch of the cluster's shared `IngressClassParams`
  (created once, never updated, by part 2's `_ensure_ingress_class`) with
  `certificateARNs: [cert_arn]` and `listenPorts: [{HTTP:80},{HTTPS:443}]`, called from a new
  `run_worker.py._apply_eks_tls_for_issued_certs` sweep alongside the existing ECS
  re-enqueue sweep, under the same `_cert_recheck_eligible` gate. Mirrors
  `cert_bootstrap._acm_client`'s mock/real gate; in mock/dev it returns `True` with no k8s
  call at all (there is no real cluster to patch — `bootstrap_eks_environment` already
  refuses mock/dev outright), letting `Environment.eks_ingress_tls_ready` (new field) get
  set the same way a real patch would for the mock end-to-end flow.
- **ECS deploy-flow wiring** (`api/services/application_deployment_service.py`). Routing
  mode is resolved once per deploy (`_resolve_host_routing`, before the image is even
  built — host mode is baked into the task definition's nginx sidecar config): the mirrored
  DB fields are a fast pre-check, a live `get_listener_arn(alb_arn, 443)` call is
  authoritative. Host mode threads `host_mode`/`app_hostname` through
  `_create_task_definition` -> `ECSClient.create_task_definition` ->
  `container_config.generate_nginx_config`/`inject_routing_envs` (all already built and
  tested in part 2 — this is the first thing that actually calls them with `host_mode=True`).
  The target group's health check path moves in lockstep on every deploy, whether the TG is
  new or reused (`ALBClient.modify_target_group`, also part-2 code, now actually called).
  **The :80 redirect is one per-infra wildcard rule, not one per app**
  (`ALBClient.ensure_host_redirect_rule`): the general path-rule allocator
  (`get_next_priority`) always grabs the lowest free priority, so a per-app :80 redirect
  rule would routinely land at a *higher* priority number than an existing path rule and
  lose to it — `Host: a.{label}.{base}` + path `/a/x` would match app A's own `/a*` path
  rule first and forward the request in plaintext, exactly the "never forwards an app
  hostname" violation the pre-review called out. One wildcard rule
  (`*.{dns_label}.{base}`), reserved at priority 1 (an atomic `set_rule_priorities` swap
  displaces whatever currently holds it), created idempotently the first time any app on the
  infra deploys in host mode, sidesteps the problem entirely and halves `:80` rule
  consumption versus a per-app scheme. The per-app `:443` forward rule
  (`Application.host_forward_rule_arn`, new field) *is* per-app, since 443 has no path rules
  to collide with and a fixed-404 default action. A deploy that regresses out of host mode
  (TLS/DNS readiness lost since the last deploy) tears down its own stale 443 rule.
  Rollback (`_rollback_ecs`) re-resolves routing the same way, never carrying over whatever
  mode the target snapshot's era implies.
- **EKS deploy-flow wiring** (`api/k8s/deployer.py`). `EKSDeployer` resolves its own routing
  mode in `__init__` (no live check available the way ALB's `describe_listeners` is — EKS
  trusts the mirrored `Infrastructure.https_ready` outright). The host rule is a *second*
  `V1IngressRule` on the same Ingress with `host=` set to the exact hostname (Prefix path
  `/`), added alongside the always-present path rule — never replacing it. The ALB
  healthcheck-path annotation and the nginx sidecar's own readiness probe both move to
  `HOST_MODE_HEALTH_CHECK_PATH` in the same deploy that flips `host_mode`, closing the
  three-way lockstep the design doc calls for. `Application.host_route_applied` (new field)
  is EKS's counterpart to `host_forward_rule_arn`, written only after the Ingress apply
  actually ran.
- **Host URL publish gating** (`api/common/host_url.py`). `build_app_hostname` is the single
  choke point every hostname passes through before reaching any of the three
  config-injection sinks (nginx `server_name`, an ALB host-header condition, a k8s Ingress
  `rules[].host`) — validates the platform domain is configured (fails closed: no
  `launchpad.app`-style fallback the way infrastructure-service's `PLATFORM_BASE_DOMAIN`
  setting has, since a value from here is shown to a customer as a live URL), the
  `dns_label` shape (`[0-9a-f]{16}`, mirroring the writer's own `naming.DNS_LABEL_RE`), and
  that the app's slug is a single DNS label (narrower than the general `app_slug`, which
  admits `.`/`_` for Docker tags — neither survives under a wildcard cert scoped to one
  label). `app_host_url(application)` is the full API-facing gate: every infra-level leg
  (`infra_host_ready` — domain configured, `dns_label` minted, `tls_status == ISSUED`,
  `dns_synced`, `https_ready`) *and* this specific app's own routing evidence
  (`host_forward_rule_arn` on ECS, `host_route_applied` on EKS) — a TLS-ready infra is not
  enough on its own if this app hasn't actually been deployed with the route applied yet.
  Wired into `views/application.py`'s detail response as `host_url` (a live `https://` URL
  or `null`) and `host_url_status` (a stable reason string — `tls_not_issued`,
  `dns_not_synced`, `host_route_not_applied`, `slug_not_hostname_safe`, etc. — never a raw
  exception). The dashboard shows a Host URL card above the always-present path-URL card.
- **`backfill_host_routing`** (`ApplicationDeploymentService.backfill_host_routing` +
  the management command of the same name). Closes the gap for an ECS app that was last
  deployed before its infra finished TLS onboarding and hasn't pushed code since — host mode
  otherwise only applies on an app's *next* deploy. Re-registers the task definition against
  the exact image (tag + digest) its most recent `tag_source=resolved_sha` `Deployment` row
  recorded (the same "pin to a known-good image, no rebuild" approach rollback already
  uses), then runs the same target-group/ALB wiring a normal deploy would. Idempotent
  (`already_host_mode` short-circuits) and safe to re-run; skips anything not eligible
  rather than guessing. EKS needs no equivalent — `EKSDeployer` re-resolves `host_mode` on
  every deploy already, so an EKS app picks up host mode the next time it deploys for any
  reason, deploy or rollback.
- **Mock end-to-end.** `api/mock/mock_session.py`'s ALB stub now always exposes both `:80`
  and `:443` listeners (mock provisioning never runs terraform, so there is no "443 not
  applied yet" state to model there — the app-level `tls_status`/`dns_synced`/`https_ready`
  gate is what actually decides whether a mock deploy attempts host mode) and implements
  `set_rule_priorities`; `describe_rules`/`create_rule` now round-trip `Conditions` so
  `ensure_host_redirect_rule`'s idempotent lookup works against the mock the same as against
  real ALB. `_mock_provision` (infrastructure-service) mirrors the real `provision()`'s
  ISSUED-cert gate for `https_listener_arn` (ECS) and flips `eks_ingress_tls_ready` directly
  (EKS) — the same mock-skips-the-network-call-and-synthesizes-the-result pattern used
  throughout this file.

**Deferred, explicitly:**

- Custom domains proper (`CustomDomain` API/UI, the `RESERVED_DOMAIN_SUFFIX` fail-closed
  removal originally flagged in part 1) — a separate slice from host URLs, done in part 3b
  (see below).
- A live check on every EKS deploy is now built (see Security review fixes below, B1) —
  but only a read-only confirmation that the shared group ALB has a real `:443` listener.
  There is still no live read of `IngressClassParams.spec.certificateARNs` itself;
  `Infrastructure.https_ready` (mirrored from `Environment.eks_ingress_tls_ready`) is
  trusted for "has the cluster-level patch been attempted and reported success," and the
  live `:443` check plus `EKS_HOST_MODE_ENABLED` are what actually gate whether host mode
  is granted.

### Security review fixes (post-push, same branch)

An independent review of the first part 3a push returned BLOCK. All findings addressed on
the branch before it was force-pushed:

- **B1 (EKS `:80` plaintext forward) — revised after a second review round.** Adding a
  `host=` rule to an Ingress whose `IngressClassParams.listenPorts` includes both `80` and
  `443` makes the AWS Load Balancer Controller put a forwarding rule for that hostname on
  **both** ports — a plaintext forward for the exact host a customer would reach over
  HTTPS on `:443`, with EKS's `Infrastructure.https_ready` mirror as the only (pre-review)
  gate and no live check at all.

  The first fix attempt kept the host rule on the existing path Ingress and added an
  out-of-band `boto3` `:80` redirect rule (`ensure_host_redirect_rule`, the same helper ECS
  uses) to intercept it. A second review round rejected this: that `:80` listener is owned
  by the AWS Load Balancer Controller, which reconciles it independently of any manually
  created rule, so the Ingress apply that runs right after can delete or renumber a rule
  outside its own model with nothing re-checking afterward — the host rule could end up
  forwarded on `:80` in plaintext regardless of what was reserved a moment earlier.

  **Final fix:** the host rule now lives in its own Ingress (`{slug}-host`,
  `EKSDeployer._host_ingress_manifest`), in the same `IngressGroup` as the path Ingress
  (same `ingress_class_name`, hence the same shared ALB) but scoped to `HTTPS: 443` only
  via the per-Ingress `alb.ingress.kubernetes.io/listen-ports: '[{"HTTPS": 443}]'`
  annotation. The path Ingress carries no such annotation and keeps defaulting to `:80`,
  unchanged (golden-tested byte-for-byte in path mode). No out-of-band ALB rule is created
  or needed — protection now comes entirely from a controller-recognized object, not from
  fighting the controller's own reconcile loop. `EKSDeployer._verify_eks_https_listener`
  (renamed from `_verify_and_secure_eks_alb`) still runs on every deploy before host mode
  is granted, but is now read-only: it finds the shared group ALB via `Environment.alb_dns`
  (`_find_group_alb_arn`, a client-side `DescribeLoadBalancers` scan, since the real API
  has no filter-by-DNS-name) and confirms a real `:443` listener exists on it; it mutates
  nothing. Any failure or ambiguity anywhere in that chain fails closed to path mode
  (`eks_alb_not_discovered` / `eks_alb_live_check_failed` /
  `eks_https_listener_not_applied`) — never a guess. A controller-managed `:80`→`:443`
  redirect for this exact host (a per-Ingress `ssl-redirect` annotation) was considered and
  left out: whether it can be scoped to only the host Ingress inside a shared group without
  affecting the path Ingress's own `:80` traffic is exactly the kind of unverified
  assumption this review round was raised over. Without it, `:80` for the hostname simply
  hits the group ALB's fixed default action (a 404) — safe, if less friendly; never a
  plaintext forward.

  One more conflict surfaced while closing this out: `apply_eks_tls` was still patching
  `spec.listenPorts: [{HTTP:80},{HTTPS:443}]` onto the shared `IngressClassParams` — and
  the AWS Load Balancer Controller documents class-level `IngressClassParams` fields as
  overriding the equivalent per-Ingress annotation, not the other way around. Left in
  place, that class-level value would have silently widened every Ingress in the group,
  including the host-only one, back onto both ports regardless of its own `listen-ports`
  annotation — the exact same plaintext-forward risk this fix exists to close, just moved
  one layer up. Fixed by removing `listenPorts` from the class-level patch entirely
  (`apply_eks_tls` now sets only `certificateARNs`) and making both Ingresses' listen-port
  scoping fully explicit: the path Ingress now also carries its own
  `listen-ports: '[{"HTTP": 80}]'` annotation (previously implicit, relying on the
  controller's own no-annotation default) alongside the host Ingress's `HTTPS: 443`. There
  is now exactly one source of truth for listen ports — the per-Ingress annotations — not
  two pulling in opposite directions.

  Removing `listenPorts` from the class-level patch surfaced a second, structural problem:
  nothing else established the group ALB's `:443` listener ahead of any app existing.
  `EKSDeployer._verify_eks_https_listener` refuses to grant host mode until a `:443`
  listener already exists, but the only thing that would have declared one was the very
  per-app host Ingress that check gates — a deadlock that would make `EKS_HOST_MODE_ENABLED`
  permanently unable to activate for any infra, ever, once turned on.

  The first attempt at fixing this put `HTTPS: 443` on the bootstrap Ingress
  (`eks_bootstrap.py:_ensure_bootstrap_ingress`) directly, at bootstrap-creation time — but
  that runs before any infra has ever requested a certificate, and an ALB HTTPS listener
  cannot be created without one (a hard `CreateListener` constraint, not a controller
  choice); the controller would fail to resolve a certificate for the group and never write
  an ALB hostname onto the Ingress's status, timing out EKS cluster bootstrap entirely —
  every new EKS cluster, not just host mode. Caught before push. **Final fix:**
  `_ensure_bootstrap_ingress` stays `HTTP: 80` only, and `apply_eks_tls` — the point where a
  certificate is actually known to exist — patches the bootstrap Ingress's `listen-ports`
  annotation to add `HTTPS: 443` right after (same k8s API call sequence) patching
  `certificateARNs` onto the class, class first so the certificate is already resolvable
  when the Ingress patch triggers the controller's reconcile of it. The bootstrap Ingress's
  `default_backend` has no application significance (an empty-selector Service, never a real
  app's backend), so this grants no app plaintext exposure — it mirrors, at the
  bootstrap-Ingress layer, what ECS's terraform-managed 443 listener does (created
  independent of any specific app), except its default action resolves to an empty target
  group (503) rather than a literal fixed-404 response; see REAL-AWS-VALIDATION.md.

  Host mode on EKS is additionally gated behind a new `EKS_HOST_MODE_ENABLED` setting
  (default `False`, `application-service`) until the items below are confirmed against a
  real cluster; with it off, every EKS app stays on path URLs
  (`host_url_status=eks_host_mode_disabled`), unaffected by anything above. Also fixed in
  this round (LOW, same review): `EKSDeployer.__init__` resolved host eligibility with a
  DB-only check and no longer makes any AWS call — `_resolve_host_routing` is pure DB
  logic, and the live `_verify_eks_https_listener` check now runs from `deploy()`, right
  before it matters, so constructing an `EKSDeployer` can never have a side effect against
  the customer's account. `apply_eks_tls`'s `listenPorts`/`certificateARNs` schema and
  whether the controller honors a per-Ingress `listen-ports` override inside a shared
  `IngressGroup` the way its docs describe (rather than reconciling every group member onto
  the union of every declared port) are both unverified against a real cluster — see
  `REAL-AWS-VALIDATION.md`, and `EKS_HOST_MODE_ENABLED` stays off until they are.
- **R1 (redirect rule permanently outranked after one failed swap).**
  `ensure_host_redirect_rule`'s lookup path returned the existing rule's ARN without
  checking its priority — a `set_rule_priorities` call that failed partway (or a rule
  displaced by something else after the fact) left it outranked by a later path rule
  forever, since nothing ever re-checked. Fixed: the lookup now re-claims priority 1 via
  `_reprioritize_to_one` every time it finds the rule not already there, not only at
  creation. Also fixed in the same pass: `_reserve_host_redirect_priority` now runs
  **before** `_configure_alb_routing` creates a path rule (closing the first-deploy race
  where a path rule could grab priority 1 before any app on the infra ever reaches host
  mode), and per-app path conditions changed from a single glob (`/{slug}*`, which also
  matches `/{slug}suffix` and, once priorities can be swapped, could shadow a
  different app whose slug shares a prefix) to an exact-or-prefixed pair
  (`[f"/{slug}", f"/{slug}/*"]`) that never overlaps between different slugs regardless of
  rule order.
- **R2 (host_url shown for a route that no longer works).** `host_forward_rule_arn` was
  saved before `verify_target_group_attached` ran and never cleared if a later step failed
  and the unwind deleted the rule; `app_host_url` also ignored `Application.status`
  entirely. Fixed: the `host_forward_rule` branch of `_cleanup_resource` now nulls the DB
  field (tolerating `RuleNotFound`, since the rule may already be gone); `app_host_url`
  requires `status == 'ACTIVE'` before considering any routing evidence at all.
- **R3 (EKS TLS patch state never re-evaluated).** `eks_ingress_tls_ready` was a one-shot
  latch — a certificate re-issue (a fresh ARN after a FAILED retry) left the Ingress class
  serving a stale/deleted certificate forever, and a persistently failing patch attempt
  (unreachable cluster, k8s API throttling) retried every ~30s tick indefinitely with no
  cutoff. Fixed: new `Environment.eks_ingress_tls_cert_arn` tracks which cert ARN the
  ready flag actually reflects (or is currently targeting); a mismatch against the
  currently-ISSUED cert triggers a re-patch and drops the ready flag immediately, even
  before the patch attempt itself runs. New `eks_ingress_tls_patch_attempted_at` bounds
  retries to `EKS_TLS_PATCH_TIMEOUT` (30 min, mirroring `ISSUED_CHECK_TIMEOUT`'s own
  age-based cutoff) against a stable target. All three EKS TLS fields are cleared at every
  DESTROYED transition in `TerraformWorker.destroy()`.
- **R4 (dns_label overwritable in the read-model).** Both ingestion paths
  (`upsert_infrastructure` and `HostReadinessEventConsumer`) let a later event's dns_label
  silently overwrite an already-stored value — this field feeds directly into every
  hostname application-service builds. Fixed: write-once in both places — a stored
  non-null value that disagrees with an incoming one is logged and refused, never applied;
  a malformed incoming value is rejected outright.
- **Recommended, all applied:**
  - `converge.py`'s `_poll_until_synced` slept up to ~50s on the writer's single
    `prefetch_count=1` consumer thread after every UPSERT, delaying every other queued
    reconcile (including a teardown) behind it. Removed entirely — a PENDING change now
    just leaves its `change_id` recorded, confirmed later by `_stamp_pending_syncs` (the
    next reconcile for that infra) or the new `sync_platform_dns_changes` management
    command (`sweep_pending_dns_syncs`), an out-of-process periodic sweep grouped by
    `change_id` to avoid redundant `GetChange` calls — same operational pattern as
    `sweep_platform_dns`.
  - `occurred_at` (a wall-clock timestamp, vulnerable to skew between this service's
    several concurrent publishers) is replaced end to end by
    `Infrastructure.host_readiness_version`, a per-infra counter incremented under a row
    lock before every publish. `HostReadinessEventConsumer` now requires it — a payload
    missing or carrying a non-integer value is discarded, never treated as "always
    current" the way a missing `occurred_at` previously was.
  - `docs/PLATFORM_DNS_ISOLATION.md`'s RabbitMQ section extended to cover the writer's new
    publish target (`infrastructure.events`, for `host_readiness_updated`), including an
    explicit callout that RabbitMQ permissions are exchange/queue-name-based, not
    routing-key-based, so this grant cannot be narrowed to only that one routing key — a
    compromised writer credential could still forge other `infrastructure.events` message
    types. Not wired into `infra/.docker` (documented, not automated), same as the
    pre-existing Redis ACL gap in that file.
  - `backfill_host_routing` now takes the per-app `DeploymentLock` before touching
    anything (skipping — not blocking on — an app a concurrent ordinary deploy/rollback
    already holds), rolls the ECS service back to its previous task definition if
    `_configure_host_routing` fails after the service has already been moved to the new
    one, and `--dry-run` calls the same (read-only) eligibility evaluation a real run
    would rather than just naming the apps it would consider.
  - `converge.py`'s ledger updates now filter exact `(name, type)` pairs (a `Q`-OR chain)
    instead of an `__in`/`__in` cross product, and every `synced_at` stamp — in
    `_stamp_pending_syncs`, the main upsert path, and the new periodic sweep — is guarded
    on `change_id` matching what was just read, so a concurrent reconcile that already
    re-upserted the row with a different change cannot have its confirmation misapplied.

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

**All wired in part 3a** — see "Part 3a — what shipped" above. Left as written at the time
(part 2) for the historical record of what was and wasn't done at each stage.

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

**Part 3a's actual test files** (infrastructure-service):
`test_platform_dns_converge.py` (synced_at population, catch-up, the periodic-sweep
functions), `test_sync_platform_dns_changes.py`, `test_host_readiness.py` (including the
monotonic version counter), `test_eks_bootstrap.py` (`apply_eks_tls`),
`test_eks_tls_sweep.py` (re-issue detection, bounded retry, teardown clearing).
Application-service: `test_host_url.py` (hostname validation), `test_host_url_gating.py`
(the full gating matrix, including the ACTIVE-status gate), `test_host_readiness_consumer.py`
(including the required-version rejection), `test_dns_label_write_once.py`,
`test_alb_host_routing.py` (`ensure_host_redirect_rule`, including the re-prioritize-on-lookup
fix), `test_host_mode_deploy_wiring.py` (ECS end-to-end against the mock ALB, the
first-deploy-window fix, plus the path-mode golden-equivalence checks), `test_mock_eks_deploy.py`
(EKS host-rule additions and the live-ALB-check fail-closed paths, appended to the existing
SEAM 3 harness), `test_backfill_host_routing.py` (including the lock/rollback/dry-run fixes).

## Security pre-review

**Required, and this is the highest-risk feature remaining.** The platform DNS zone is the
first shared all-tenant asset in a product sold on "nothing of yours lives with us". H1,
H2 and H3 are all live here. Review the design before writing the DNS writer, not after.

## Out of scope

Apex/root custom domains (CNAME-only, documented). Migrating existing apps off path URLs —
host URLs are additive. Any traffic transiting platform infrastructure: DNS and certs only.

## Part 3b — customer custom domains (design)

Builds on the `CustomDomain` model from #68 (state machine, partial unique index, 72h
expiry — kept as-is). Adds the API, the DNS/ACM verifier, ALB wiring, and teardown.

**Service boundary.** `CustomDomain` and the ACM certificate lifecycle
(`RequestCertificate`/`DescribeCertificate`/`DeleteCertificate`) stay in infrastructure-
service, next to `cert_bootstrap.py`, reusing its `_acm_client`/`FakeAcmClient`/tagging
helpers rather than duplicating them. Listener rules and the SNI attach/detach live in
application-service's `aws/alb.py`, next to the existing host-forward/redirect rules and
the live `describe_listeners`-based listener discovery already used by
`_resolve_host_routing` — application-service never persists a listener ARN, so this stays
consistent. The two are joined by a **synchronous internal HTTP call**
(`shared.resilience.http_client.ResilientHttpClient` + `X-INTERNAL-TOKEN`), the same
pattern `exit_export.py` already uses to call application-service's `export_inventory` —
not a new RabbitMQ event type. This keeps the whole PENDING→VALIDATED transition,
including the SNI cap check and the attach, inside one Django transaction on the
`Infrastructure` row lock in infrastructure-service, with app-service's attach/detach as a
best-effort-idempotent step that gets compensated (detached again) if the local DB commit
loses the cross-tenant race.

New model: application-service gets `CustomDomainRoute` (hostname, infrastructure_id,
application_id, cert_arn, host_forward_rule_arn, host_redirect_rule_arn) — its own record
of what it attached, so app-delete cleanup and detach don't need a round trip back to
infrastructure-service to discover rule ARNs.

**Verify flow** (`CustomDomainService.verify`, infrastructure-service):
1. `select_for_update` the `Infrastructure` row (serializes concurrent verifies for the
   same infra — needed because the SNI cap check below is a live AWS read, not something
   the DB transaction can make atomic on its own).
2. `select_for_update` the `CustomDomain` row; re-check `status == PENDING` and
   `not is_expired` **on the locked row**, not the caller's in-memory object (this was a
   real bug in `mark_validated()` — see below).
3. Authoritative TXT check at `_launchpad-challenge.{host}` against the hostname's own
   NS set (never the recursive resolver).
4. `DescribeCertificate` on the domain's own per-domain ACM cert (requested at claim
   time — see below) — must be `ISSUED`.
5. Call application-service's internal attach endpoint (cap-checks
   `describe_listener_certificates` against the infra's live 443 listener, then
   `add_listener_certificates` + creates the host-forward and host-redirect rules,
   idempotently). A 409 here (cap reached) aborts before any DB write.
6. `domain.mark_validated()`. If this raises `HostnameAlreadyValidatedError` (lost the
   cross-tenant race on the partial unique index — a different infra, so step 1's lock
   didn't serialize against it), call the detach endpoint to undo step 5 and return 409.
7. Commit.

**Claim flow** requests the per-domain ACM cert immediately (DNS validation is
asynchronous and the customer needs the validation CNAME up front, alongside the TXT and
the `edge.` CNAME instructions) and generates the ownership token
(`secrets.token_urlsafe(32)`, returned once, stored as `sha256` hex on
`ownership_token_hash` — never stored in reversible form). Claims are refused with 409 at
`compute_type == 'eks'` (host mode is disabled there behind `EKS_HOST_MODE_ENABLED`,
so a claim could never route) and capped at 20 PENDING claims per infrastructure, both
checked under the same infra row lock claim uses to mint the row.

**`mark_validated` fix (H4).** Before this change, the status/expiry re-check ran on
`self` (the caller's possibly-stale object) *before* acquiring the row lock, then the
locked block blindly wrote `VALIDATED` — a concurrent double-verify or a verify racing
the 72h sweep could validate an already-expired or already-transitioned row. Fixed to
re-check on the row returned by `select_for_update()`.

**Hostname normalization (H6).** `normalize_hostname` used to decode back to Unicode
after the IDNA round-trip, and silently kept the raw label on `idna.IDNAError` — fine for
the suffix check alone, wrong for a value that also has to reach an ALB host-header
condition, `ACM DomainName`, and a DNS query. Changed to return the ASCII/punycode form
and raise instead of silently keeping the raw label; `reject_reserved_suffix` compares
against `PLATFORM_BASE_DOMAIN` run through the same normalizer, not a raw string compare.
A new `validate_hostname_syntax` enforces LDH charset, ≤63 bytes/label, ≤253 total, ≥2
labels (apex stays documented-out, not hard-blocked — unchanged from part 2's decision),
no `*`, and rejects IPv4/IPv6 literals via `ipaddress.ip_address`. This runs before the
hostname reaches ALB, ACM, or the resolver. The 13 existing `test_custom_domain.py` cases
that asserted Unicode output were updated to assert the ASCII form; the suffix-rejection
behavior they cover is unchanged.

**Fail-closed suffix (H5).** `PLATFORM_BASE_DOMAIN` (also `RESERVED_DOMAIN_SUFFIX`, same
setting, infrastructure-service only) no longer defaults to `'launchpad.app'`. Unset
outside dev mode raises a plain `ValueError` at settings load — matching this same
settings module's existing `LAUNCHPAD_PLATFORM_PRINCIPAL_ARN` fail-closed check
immediately above it, not `django.core.exceptions.ImproperlyConfigured` (an earlier draft
of this note said otherwise). Dev mode falls back to `launchpad.test` (a value that can
never collide with a real customer domain or the platform's own zone). application-service
was **not** touched here: its own `PLATFORM_BASE_DOMAIN` (`host_url.py`) already read
`os.environ.get('PLATFORM_BASE_DOMAIN') or None` with no fallback — unset already produces
`None` there today, which already suppresses every platform host URL rather than building
one from a wrong value (an earlier draft of this note claimed both services "got the same
fix"; only infrastructure-service's reserved-suffix check had the fail-open bug — F1b part
3a's own settings.py comment already documented app-service's None-on-unset behavior as
intentional). An optional `PLATFORM_ROOT_DOMAIN` (infrastructure-service) covers a
delegated root the base domain might be a sub-zone of; deliberately left optional rather
than required outside dev, since in this platform's actual topology
`PLATFORM_BASE_DOMAIN` *is* the zone apex, not a delegated sub-zone of anything else this
platform controls (see the settings.py comment for when to set it).

**Re-validation job.** A periodic tick (added next to the existing TLS re-check tick in
`run_worker.py`, and — unlike that tick — run on its own dedicated worker thread with a
hard wait ceiling on the dispatch side, since an authoritative DNS lookup is bounded by
its own deadline but a customer's DNS is still attacker-reachable) walks VALIDATED
domains, re-runs the authoritative TXT check, and increments `verification_failure_count`
on failure (reset to 0 on success). Three consecutive failures move the row through
`DISABLING` (a retryable checkpoint added in the second security review pass — see below)
→ detach (application-service) → `DeleteCertificate` → `DISABLED`; a stuck `DISABLING` row
is retried by the same tick's `sweep_stuck_disabling`. The same tick sweeps expired
PENDING claims (72h) and
best-effort deletes their ACM certs — a claim that only reached `RequestCertificate` and
never got the CNAME published would otherwise leave a `PENDING_VALIDATION` cert in the
customer's account forever.

**Teardown, three entry points.** App delete (application-service's cleanup path) detaches
its own `CustomDomainRoute` rows locally (no round trip needed) and best-effort notifies
infrastructure-service to `DeleteCertificate` + mark `DISABLED`. Infra destroy hooks into
`TerraformWorker._pre_destroy_cleanup` — one call there covers every `enqueue_destroy`
call site, since they all funnel through the same `TerraformWorker.destroy()` — detach,
then delete cert, before terraform destroys the ALB (detach needs a live listener; cert
delete does not). Complete exit (`infrastructure_complete_exit`) is not an infra destroy —
the customer keeps their AWS resources — but Launchpad still stops managing routing for
it, so it gets its own best-effort teardown call, after the DNS teardown succeeds and
never gating `exited_at` (never blocks `request_and_await_dns_teardown`, per H2).

**Authz.** Owner-only (`str(infrastructure.user_id) == str(user_id)`) for claim/verify/
delete, matching `DatabaseService._require_owner` — an invited `ADMIN`
(`InfrastructureUserRole`) is refused with 403, same as database management. List is
readable by owner + invited members (`InfrastructureRepository.get_by_id`'s existing
scope), matching `list_databases`.

**Pre-existing bug found, fixed separately.** `DeploymentQueue.enqueue_cleanup`'s job
dict never carried `host_forward_rule_arn`, so deleting an ECS app in host mode leaked
its `:443` host-forward rule (`ApplicationCleanupService.cleanup_application`, which
does handle it, was dead code — nothing called it). Fixed in its own commit since it's a
platform-hostname bug, not a custom-domain one, but the fix lives on this branch because
custom-domain cleanup touches the exact same job dict and cleanup handler.

**IAM policy: no version bump.** `policy.json` v4's ACM statements already grant
`RequestCertificate`/`DescribeCertificate`/`AddTagsToCertificate`/`DeleteCertificate`
generically (tag-conditioned on `ManagedBy=launchpad`, not scoped to the wildcard
domain), and `elasticloadbalancing:*` is already ungated — both already cover a
per-customer-domain cert and `AddListenerCertificates`/`RemoveListenerCertificates`. No
`policy.json` or `NEXT_PUBLIC_LAUNCHPAD_SCRIPT_REF` change needed for this slice.

## Part 3b — second security review pass

An independent review of the first Part 3b commit found a resource-exhaustion/SSRF gap in
the DNS resolver and several teardown/locking races. All fixed on the same branch before
merge; the design decisions above (service boundary, DISABLING addition aside) are
unchanged.

**B1 — resolver could stall the infra worker fleet, and reached private/link-local IPs.**
`custom_domain_dns.py` previously bounded each individual dnspython call to its own
timeout but had no bound on the *total* number of calls a single lookup could make (label
walk, NS hosts, NS IPs each effectively unbounded) or on the *combined* wall-clock cost —
and the periodic re-validation tick ran this inline on the worker's main dispatch thread,
so a single slow/adversarial domain could stall provision/destroy dispatch fleet-wide for
as long as it stalled, with the Redis tick lock (~295s TTL) expiring mid-stall and letting
another worker pile onto the same problem. Separately, nothing stopped a candidate
nameserver IP from being loopback, RFC1918, link-local, or the cloud metadata address
(`169.254.169.254`) — a customer-controlled "nameserver" is otherwise a live SSRF/timing
oracle against this platform's own VPC. Fixed: one shared `_Deadline` (10s) threaded
through every dnspython call in a single lookup (`_TOTAL_LOOKUP_DEADLINE_SECONDS`), hard
caps on labels walked / NS hosts / NS IPs (`_MAX_LABELS_TO_WALK`/`_MAX_NS_HOSTS`/
`_MAX_NS_IPS`), every candidate IP filtered through `ipaddress.*.is_global` before a
packet is ever sent to it, `OSError` caught alongside `dns.exception.DNSException` (a raw
socket refusal isn't a DNS-library exception), and the periodic tick moved onto its own
dedicated worker thread (`run_worker.py`'s `custom_domain_pool`) with dispatch bounded by
`CUSTOM_DOMAIN_CHECK_HARD_TIMEOUT_SECONDS` (60s) rather than blocking on it directly.

**R1 — verify held both row locks across the DNS lookup and the app-service attach.**
`verify_domain` used to run the authoritative TXT lookup *inside* the
`Infrastructure`+`CustomDomain` row-lock transaction, alongside the attach call
(AssumeRole plus several ELBv2 calls on application-service's side) — a slow lookup into a
zone the customer fully controls, or a client disconnecting mid-request, held both locks
for however long that took. Fixed: `verify_ownership_token` now runs before any lock is
taken; status/expiry/cert_arn are re-checked on the row `select_for_update()` returns
before anything from the pre-lock read is trusted.

**R2 — teardown was one-shot with no retry state, and detach was keyed by hostname
alone.** The original `_teardown` attempted detach and `DeleteCertificate` in one pass and
unconditionally marked the row `DISABLED` (or deleted it, for a PENDING row) regardless of
whether either AWS call actually succeeded — `_detach` didn't check the HTTP response
status (`ResilientHttpClient` returns a 4xx/5xx `Response` object without raising, so a
non-exception failure was silently treated as success), `delete_certificate` swallowed
`ResourceInUseException` the same as any other outcome, and `_detach_route`
(application-service) deleted its `CustomDomainRoute` row in a `finally` block regardless
of whether the AWS calls in the `try` above it had succeeded. A domain could end up marked
DISABLED (or gone) with its SNI certificate and ALB rules still live, permanently orphaned
— and since `CustomDomainRoute.hostname` is unique, a surviving row would make every
future attach for that hostname 409 forever. Separately, detach was scoped by `hostname`
alone: a hostname freed by a DISABLED row can be reclaimed by a different infrastructure,
and a stale/retried detach call for the old owner could rip out the new owner's current
attachment.

Fixed with a new `DISABLING` intermediate status (additive migration, no data backfill —
the table was empty in every real deployment): `_teardown` now does `mark_disabling()`
(a fast, immediately-committed checkpoint — PENDING or VALIDATED → DISABLING, idempotent)
*before* the slow network calls, which run with **no DB lock held at all**; `mark_disabled`
(DISABLING → DISABLED, clearing `cert_arn`) is only reached once `_detach` returns `True`
(HTTP 200, confirmed) **and** `delete_certificate` returns `True` (deleted or already
`ResourceNotFoundException` — `ResourceInUseException` and everything else now returns
`False`). Detach is scoped by `(infrastructure_id, hostname)` on both sides — the request
body and the `CustomDomainRoute` query. `_detach_route` only deletes its row once the ALB
calls succeed; a failure leaves the row in place for the next retry. A stuck `DISABLING`
row is retried by the new `sweep_stuck_disabling` (same bounded-per-tick pattern as the
other two sweeps). Every teardown path — owner delete, infra destroy, complete-exit,
disable-for-application, the expired-claim sweep, the failed-re-validation path — now
converges on the same `_teardown`, so a PENDING row is DISABLED (not hard-deleted) exactly
like a VALIDATED one; this also fixes R1's "PENDING teardown must detach too" (a crash
between application-service's attach succeeding and `mark_validated()` committing could
otherwise leave a PENDING row with a live, un-detached route).

**R3 — `delete_domain`/`_teardown` read status unlocked, racing `verify_domain`.** Reading
`domain.status` on an unlocked object to decide whether to detach could act on a value
`verify_domain` had already changed underneath it (e.g. tearing down a row that was
VALIDATED a moment ago without detaching it). `mark_disabling()` re-checks status on the
row `select_for_update()` returns, not on the caller's object, and is idempotent from any
of PENDING/VALIDATED — `_teardown` now always attempts detach after it succeeds,
regardless of what the row's status used to be, so there's no branch left to race.

**R4 — claim/verify didn't check `exited_at`/`dns_teardown_requested_at`.** A claim or
verification could complete after an infra had already started (or finished) tearing down,
re-attaching routing that teardown had already removed (or never removed, if verify raced
ahead of it) and outliving the ALB's own destruction. Both are now refused
(`_refuse_if_infra_exiting`) — a fast pre-check on the unlocked read, and the authoritative
check again on the row `select_for_update()` returns inside the transaction. No change was
needed to how `dns_teardown_requested_at`/`exited_at` themselves get set:
`mark_dns_teardown_requested`'s conditional `UPDATE ... WHERE dns_teardown_requested_at IS
NULL` and `exit_export.py`'s `exited_at` write both target the same `Infrastructure` row
claim/verify already lock, and under Postgres a plain `UPDATE` on a row blocks until a
concurrent `SELECT ... FOR UPDATE` holder on that row commits — the ordering holds without
either write needing its own explicit lock (this is a real guarantee only under Postgres;
`select_for_update()` is a documented no-op on the sqlite test backend).

**R5 — a custom domain's `:80` redirect could be outranked by a path rule.**
`create_host_redirect_rule` (used for both the platform wildcard and per-custom-domain
redirects) and `create_listener_rule` (path rules, no host condition) both allocated
priority from the same "lowest free number" pool — a path rule created after a custom
domain's redirect could land at a lower priority number (evaluated first) and, having no
host condition, match a request to `http://custom.example.com/{slug}/x` and forward it to
a backend in plaintext instead of redirecting to HTTPS. Fixed by reserving a low priority
band for every host-header-conditioned rule (`_HOST_REDIRECT_PRIORITY_FLOOR = 1`) and
flooring path rules above it (`_PATH_RULE_PRIORITY_FLOOR = 1000`) — `get_next_priority`
takes a `floor` parameter now, threaded through `_create_rule_with_retry`. No live
migration of already-provisioned ALBs was needed: nothing has shipped against a real AWS
account yet.

**Recommended items also addressed:** `attach_custom_domain` is now reservation-first (the
`CustomDomainRoute` row is created, with placeholder rule ARNs, before any AWS call — a
concurrent attach for the same hostname now collides on the DB constraint immediately
instead of both callers racing in AWS), checks `application.target_group_arn` before
attempting anything, and compensates (detaches whatever was actually attached) on any
failure; `cert_arn` is validated against `arn:aws:acm:{region}:{account_id}:certificate/…`
and cross-checked against the calling infra's own AWS account (real infras only — mock
certs don't carry a real account id), and `hostname` is re-validated with the same shared
`shared/validators/hostname.py:validate_hostname_syntax` infrastructure-service uses (moved
there from `api/models/custom_domain.py`, which re-exports it, so existing imports/tests
were untouched) — application-service must not simply trust a value handed to it by
infrastructure-service for a value that reaches an ALB host-header condition. The
authoritative TXT response now requires the `AA` flag and rejects a CNAME-redirected
answer (`response.canonical_name()` must equal the query name) rather than accepting any
TXT rrset present in the answer section regardless of owner name. App delete now notifies
infrastructure-service unconditionally, not only when a `CustomDomainRoute` existed
locally — a still-PENDING domain (claimed but never verified, so never attached in this
service at all) also needs its certificate cleaned up there, and this notification is the
only signal that ever reaches infrastructure-service that the application is gone.
`_verify_application`'s cross-service call was moved off `GET /applications/{id}/` (full
detail, including env vars and the webhook-secret shape) onto a new narrow
`GET /internal/applications/{id}/summary/` returning only `{id, infrastructure_id,
status}` — infrastructure-service's claim flow has no legitimate use for the rest, and
receiving it at all was unnecessary secrets exposure. An all-numeric top-level label
(`0x7f.1`) is now rejected in `validate_hostname_syntax` — some HTTP clients and resolvers
treat it as legacy dotted-decimal/hex IP shorthand. `dnspython==2.8.0` (new dependency,
`deployment-services/requirements.txt`) was checked with `pip-audit` — no known
vulnerabilities in it or anything else in that file.

**Part 3b's actual files.** infrastructure-service: `api/models/custom_domain.py`
(hardened, +application_id/ownership_token_hash/verification_failure_count/DISABLING,
hostname-syntax validation now imported from `shared/validators/hostname.py`),
`api/services/custom_domain_dns.py` (shared per-lookup deadline, label/host/IP caps,
global-IP-only, AA/CNAME-checked), `api/services/custom_domain_cert.py`
(`delete_certificate` returns success/failure), `api/services/custom_domain_service.py`,
`api/views/custom_domain.py` + `api/views/custom_domain_internal.py` + routes,
`core/settings.py` (fail-closed `PLATFORM_BASE_DOMAIN`/`PLATFORM_ROOT_DOMAIN`, budget/cap
settings), `api/services/terraform_worker.py` (destroy teardown hook),
`api/views/exit_export.py` (complete-exit teardown hook),
`api/management/commands/run_worker.py` (periodic re-validation/sweep tick, on its own
worker thread with a hard dispatch-side timeout). `deployment-services/shared/validators/
hostname.py` (new — shared between both services). application-service:
`api/models/custom_domain_route.py`, `api/services/custom_domain_routing.py`
(reservation-first attach, cert_arn/hostname validation, scoped detach),
`api/views/custom_domains_internal.py` + urls (attach/detach/`application_summary_for_
custom_domains`), `aws/alb.py` (SNI attach/detach, `delete_rule`, the host-redirect/
path-rule priority band), `api/mock/mock_session.py` (listener-cert mock support),
`api/services/application_service.py` (app-delete cleanup wiring, unconditional notify),
`api/services/deployment_queue.py` + `api/management/commands/run_worker.py` (the
pre-existing `host_forward_rule_arn` leak fix). `shared/middleware/authentication.py`
(JWT-exempt internal paths). `gateway-service/app/api/endpoints/custom_domain.py` + router.
`launchpad-frontend`: `components/custom-domains-panel.tsx`, `lib/api/custom-domains.ts`,
`types/custom-domain.ts`, wired into the application detail page.

**Part 3b's actual test files.** infrastructure-service:
`api/tests/test_custom_domain.py` (hardening: syntax fuzz incl. all-numeric TLD,
fail-closed suffix, the `mark_validated` race fix, ownership tokens, `DISABLING` state
machine), `api/tests/test_custom_domain_dns.py` (authoritative-lookup plumbing against
mocked dnspython calls and real `dns.message.Message` objects for AA/CNAME handling, the
mock/dev fake resolver, the is_mock/dev_mode gate, the shared deadline and label/host/IP
caps, non-global IPs never queried), `api/tests/test_custom_domain_cert.py` (per-domain
ACM lifecycle against the shared fake ACM double, `ResourceInUse` vs `ResourceNotFound`),
`api/tests/test_custom_domain_service.py` (claim/verify/delete/teardown orchestration, EKS
refusal, PENDING cap, cross-tenant race + compensating detach, sweep + re-validation, the
DNS-lookup-before-lock ordering, the exiting-infra refusal under lock, `DISABLING` retry),
`api/tests/test_custom_domain_api.py` (HTTP layer: owner-only, cross-tenant 404, budget
429, response shape), `api/tests/test_custom_domain_internal_view.py`,
`api/tests/test_destroy_custom_domain_teardown.py`, `test_complete_exit.py`'s two added
cases. application-service: `api/tests/test_alb_sni_certificates.py`,
`api/tests/test_alb_host_routing.py` (the R5 priority-band tests),
`api/tests/test_custom_domain_routing.py` (reservation-first, cert_arn/hostname
validation, scoped detach never touching another infra's attachment),
`api/tests/test_custom_domains_internal_views.py` (incl. the narrow application-summary
lookup), `api/tests/test_app_delete_custom_domain_cleanup.py`. gateway-service:
`tests/test_custom_domain_route.py`, `tests/test_custom_domain_rate_limit_exemption.py`.

**Deferred, explicitly, from part 3b:**

- The pre-claim `application_id` check calls application-service's own
  `GET /internal/applications/{id}/summary/` synchronously; a claim under a slow or
  unreachable application-service fails the whole claim rather than degrading. Acceptable
  for an owner-triggered, low-frequency action; revisit only if it becomes a real
  reliability complaint.
- No admin/support tooling to force-disable a domain outside the owner/periodic-job paths
  (e.g. a takedown request) — `disable_for_application`/`_teardown` exist and are callable,
  but there is no endpoint or management command wired to them for that case yet.
- Frontend: no polling for a claim's ACM validation record once it "still generating" —
  the owner has to refresh the page. `backfill_validation_record`'s single-shot check
  exists server-side; a poll loop client-side was left out to keep the panel simple.
