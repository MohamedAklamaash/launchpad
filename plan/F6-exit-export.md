# F6 — Exit export

**Status:** done (mock-verified; see REAL-AWS-VALIDATION) · **Depends on:** F3 (tag pinning), F5 (generator)
**Blocked by:** nothing · **Priority:** last

Last for a reason: it depends on two other features, and its value only materialises when
a customer actually wants to leave. Worth having before an enterprise deal asks about it.

## Goal

Leave a customer fully operational with `LaunchpadDeploymentRole` deleted.

## Verified on main at `9c8743d`

**The good news is real.** Terraform state, its bucket
(`launchpad-tf-state-{account}-{region}`) and the lock table already live in the customer's
account, and the root config is deterministically regenerable from
`terraform_worker._generate_config`. Nothing to migrate.

**The catch is also real.** The entire application layer is imperative and in no terraform
state: ECS task definitions, services, target groups, listener rules; the CodeBuild project
and its IAM role; the EKS bootstrap IngressClass, namespace and CNI patch; every per-app
Kubernetes object. A terraform state dump hands the customer **a cluster with no
applications in it**.

## Design

**A continuity export, not an IaC reconstruction.** Everything keeps running if Launchpad
simply stops touching it. The export's job is documentation and handover, not
reproduction.

Tarball:

- README with a full inventory and ARNs.
- Revocation instructions — delete `LaunchpadDeploymentRole`.
- The generated root `main.tf` + modules + a `backend.hcl` pointing at their **existing**
  state bucket.
- Cleaned Kubernetes manifest dumps and ECS task-definition JSON.
- The buildspec as a CI seed.

Full terraform import codegen for the imperative layer is the ideal end state, is large and
brittle, and is not needed for continuity.

## H6 is the design — this is a secrets-bearing archive

Three of the four contents carry secrets today:

- Kubernetes manifest dumps — `Secret.data` is base64, not encryption; Deployment `env` is
  plaintext.
- ECS task-definition JSON — `environment` is plaintext by definition.
- The buildspec — `codebuild.py` has a plaintext GitHub-token fallback.

And `backend.hcl` points at the terraform state bucket, where state stores generated
passwords in plaintext.

Requirements, all of them:

- **Owner-only; invited ADMINs refused.** On a shared infrastructure an invited ADMIN could
  otherwise exfiltrate the env vars of every app including other users'.
- Re-authentication for this operation specifically.
- **Redacted by default** — key names with `<redacted>` values.
- Never persisted: temp dir `0600`, stream it, delete in `finally`.
- **No request-supplied path component.**
- Size cap and timeout.
- An audit log.

F3's `Deployment` snapshot helps here: it already stores shape-not-values, so the export
can reuse it rather than re-reading live env values.

## Gaps found in review (now part of the design)

- **DNS and TLS.** Once F1b ships, an exiting customer's `edge.` / wildcard records and
  ACM validation CNAME live in the platform zone. Exit must run F1b's H2 teardown for that
  infrastructure — otherwise a dangling `edge.` is a takeover target and a retained
  validation CNAME keeps authorising a *former* customer's account to issue certificates
  for a Launchpad hostname. The export README warns that platform hostnames stop resolving
  and points at custom domains as the migration path.
- **The GitHub webhook.** It keeps pointing at Launchpad after exit, so pushes hit a dead
  end and CodeBuild is no longer driven. The README lists every app's webhook and tells the
  customer to remove it and seed their own CI from the bundled buildspec.

## Files

- `infrastructure-service/api/services/exit_export.py`, `api/views/exit_export.py`,
  `api/services/exit_export_audit.py`, `api/models/exit_export_access.py` (+ migration),
  `api/models/infrastructure.py` (`exited_at` + migration), `api/routes.py`,
  `core/settings.py`/`env.example`/`test_settings.py` (rate budgets, timeouts,
  `APPLICATION_SERVICE_URL`).
- `application-service/api/services/exit_inventory.py`, `api/views/exit_inventory.py`,
  `api/urls.py`.
- `gateway-service/app/api/endpoints/infrastructure.py` — `GET .../exit-export` and
  `POST .../exit`, both `infra_id: UUID`-typed.
- `launchpad-frontend/lib/api/client.ts` (reauth-required interceptor path),
  `lib/api/infrastructures.ts`, `types/infrastructure.ts`,
  `app/dashboard/infrastructures/[id]/page.tsx` (Export + Complete Exit actions, each
  behind its own confirmation dialog).

## Tests

`terraform init` with the bundled `backend.hcl` succeeds in a sandbox · **credential scan
of the archive is clean** (seed a secret into every one of the four content types and
assert absence) · invited ADMIN refused · no request-supplied path reaches the filesystem ·
the temp file is gone after both the success and failure paths.

## Security pre-review

**Required.** This is the highest-consequence export surface in the product: a single
archive spanning every application's configuration. H6 exists because the first draft of
this feature would have shipped secrets.

## Decisions

1. **No un-redacted mode.** The values already live in the customer's own account (task
   definitions, k8s objects); the export points at where, rather than copying them out.
2. **Throttled:** the F0 per-user budget, plus one export per infrastructure per hour.
3. **Re-authentication:** the JWT must have been issued within the last 10 minutes
   (`iat`); otherwise 401 with a code the dashboard turns into a re-login prompt.
4. **Which service hosts it:** infrastructure-service assembles the archive — it already
   owns `Environment`/`Infrastructure` ARNs, `terraform_worker._generate_config`, the
   IAM role/policy names, and the evidence-pack precedent (owner-only zip, in-memory,
   dedicated exceptions) this feature's service layer mirrors almost line for line.
   Application-layer data (per-app ARNs, env key names, webhook URLs, a redacted
   task-definition/Kubernetes-manifest rendering, and the buildspec) comes from a new
   `GET /api/v1/infrastructures/<infra_id>/export-inventory/` endpoint in
   application-service, called same-origin. That call forwards the caller's own
   `Authorization` header alongside `X-INTERNAL-TOKEN` rather than exempting the path
   from JWT auth — application-service re-derives the request's user and re-checks
   ownership against its own `Infrastructure` copy via `InfrastructurePermissions`,
   so this is a genuine second authorization check, not a trust-the-caller hop, and
   needed no change to `shared/middleware/authentication.py`'s hardcoded exemption
   lists. No gateway route exists for `export-inventory`, so it is unreachable from
   outside the deployment network regardless.
5. **Redaction happens at the source, once.** application-service's
   `api/services/exit_inventory.py` builds every env-value-bearing artifact (ECS
   task-definition JSON, the Kubernetes manifest bundle including a companion `Secret`)
   with values already replaced by `<redacted>` — from the latest successful `Deployment`
   snapshot's `env_keys` (F3's shape-not-values pattern) when one exists, falling back to
   `Application.envs.keys()` (never `.values()`) otherwise. infrastructure-service's
   `exit_export.py` never reads an env value and never redacts anything itself; it only
   assembles already-safe text. The buildspec bundled as a CI seed is the exact static
   template `CodeBuildClient._get_buildspec()` returns (reused directly — it takes no
   per-app parameters and embeds no secret itself); the real GitHub-token fallback lives
   only in `start_build()`'s `environmentVariablesOverride`, which this feature never
   calls, so there is nothing to strip from the template text. A companion
   `buildspec/env.example` lists the env var names a CI seed needs, with `GITHUB_TOKEN`
   always shown as `<redacted>` regardless of whether the app's repo is private.
6. **Zero AWS calls in the export path.** The Terraform bundle is `_generate_config`'s
   real output verbatim (same bucket/key/region/table it already computes for a live
   apply — not secret, and restating it in `backend.hcl` as a `-backend-config` file adds
   nothing an attacker couldn't already infer from the infrastructure id). The one value
   that would otherwise need a live `describe_security_groups` call — the per-infra
   Fargate app security group id a managed-database module block references — is left
   `""` instead, with a README note pointing at `app_security_group_name()`'s
   deterministic naming so the customer can look it up themselves. Every ARN in the
   README (ECS task-def/service/target-group/listener-rule, EKS namespace/objects,
   CodeBuild project/role) is either already stored on `Application`/`Environment` or
   computed from the same deterministic naming the deploy path already uses
   (`launchpad-build-{infra_id}`, `launchpad-codebuild-role-{infra_id}`) — never fetched.
   This is also what makes the mock-first requirement trivial: nothing in the build path
   branches on `is_mock` at all.
7. **Zip paths are keyed on UUIDs, never on a name.** `app_slug()` allows `.` unchanged,
   so `app_slug("..")` is `".."` — a real path-traversal string. Every per-app archive
   entry is `apps/{app["id"]}/...`, where `app["id"]` is asserted to parse as a UUID
   before use; a non-UUID id from the (trusted, but not trusted blindly) upstream
   response fails the whole export with a 500 rather than reaching `zipfile.writestr`.
8. **The per-user budget and re-authentication checks run before the per-infrastructure
   throttle**, and the throttle runs only after the owner check succeeds — an invited
   ADMIN's refused attempt against someone else's infrastructure can never burn the
   owner's one-per-hour export slot.
9. **The per-infrastructure hourly gate reuses `customer_call_budget`** (the same
   fixed-window INCR+TTL primitive F0 built, keyed on the infrastructure id instead of
   the user id) rather than a new acquire/release lock primitive. Known, accepted gap:
   a transient 500 *after* the gate increments still consumes the hour's one slot — the
   same class of imperfection F0's own "Known risks, not fixed here" section documents
   for the orphaned-key case, not a new one introduced here.
10. **"Complete exit" wires the real F1b part 1 teardown** (`api.services.platform_dns.
    teardown.request_and_await_dns_teardown`), not a stub — part 1 merged to `main`
    before this feature's implementation finished. It fails closed on `DnsTeardownPending`
    (202, `Infrastructure.exited_at` left unset) rather than declaring the infrastructure
    exited while a wildcard/edge record might still resolve, matching F1b's own H2
    fail-closed pattern; the underlying reconcile request was already published before the
    poll loop starts, so a retry is idempotent and safe. `EXIT_COMPLETE_DNS_TEARDOWN_
    TIMEOUT_SECONDS` (default 7s) is deliberately shorter than `request_and_
    await_dns_teardown`'s own 20s default so the whole request stays under the gateway's
    fixed 10s proxy timeout — see the real-AWS checklist for whether that is actually
    enough for a live Route53 convergence.
11. **Re-authentication reads a real `auth_time` claim, not `iat`.** An initial security
    pass shipped an `iat`-based check and flagged its own bypass: auth-service stamps a
    fresh `iat` on every refresh-token exchange, so a caller holding only a stolen refresh
    token (never the original credentials) could satisfy a 10-minute freshness check
    indefinitely by refreshing right before each call. Fixed properly rather than left as a
    known gap: `identity-services/services/auth-service`'s `signAccessToken`/
    `signRefreshToken` (`utils/handle-token.ts`) now carry an `auth_time` claim — the unix
    timestamp of the original interactive login (password, OTP, or GitHub OAuth) — onto
    both the access and refresh token. `BaseService.buildAuthResponse` stamps `auth_time`
    to `now` only when it is *not* passed an explicit value (every direct-login call site);
    `InvitedUserAuthService.refresh` is the one caller that must and does pass the
    verified refresh token's own `auth_time` through unchanged, never a fresh timestamp. No
    schema change: `auth_time` rides inside both JWTs, never a `RefreshToken` DB column
    (auth-service has no migrations — schema is `sequelize.sync()` — so a column would have
    needed a manual `ALTER TABLE refresh_tokens ADD COLUMN auth_time ...`, avoided
    entirely by keeping it in the token payload). `_reauth_ok` in
    `infrastructure-service/api/views/exit_export.py` now reads `user.get("auth_time")`
    **only** — no `iat` fallback — so a token with no `auth_time` at all (issued by an
    auth-service build predating this claim) is treated as stale, not exempt. One
    unavoidable migration edge: a refresh token minted before this claim existed carries no
    `auth_time`, and `refresh()` stamps one fresh exactly once, on that token's first
    redemption after this change ships — bounded by the existing refresh-token trust
    boundary (still a valid, unexpired, single-use token), not a new bypass.

    The frontend closes the other half: on `reauth_required` the axios interceptor now
    calls `POST /api/auth/revoke` (revoking every refresh token for the current user) *before*
    clearing local storage and redirecting to `/login`, rather than the earlier
    "just discard the local copy" behavior — a stale-`auth_time` 401 means whatever refresh
    token is sitting in the browser can no longer silently mint a new session for a
    sensitive action either. `POST /api/v1/auth/revoke` was already implemented
    (`InvitedUserAuthService.revokeRefreshTokensForUser`) but is itself unauthenticated —
    any caller who knows a `userId` can revoke that user's sessions. Pre-existing, not
    introduced by this feature; fixed on `fix/auth-revoke-authz`: the controller
    (`invited-user.controller.ts`) no longer reads `userId` from the body at all. It
    resolves the caller's own id via `resolveRevokeCallerId` (`utils/revoke-authz.ts`)
    from a verified `Authorization` access token (signature-valid and unexpired — no
    auth_time freshness check, since this is called at the exact moment the original
    access token failed only that freshness gate) or, if that is missing or fails, a
    `refreshToken` in the body as a fallback proof of possession. `revokeSchema` no
    longer requires `userId`; the gateway's `RevokeBody` and the frontend's
    `revokeCurrentUserSessions` were updated to match (it now sends the caller's own
    `Authorization` header and, when present, a `refreshToken` body field sourced from
    local storage — never a `userId`). A grep of every auth-service controller for a
    body/param-supplied `userId` used without an auth check found no other instance of
    this pattern. Known gap left as-is: the refresh-token fallback only checks the JWT's
    signature and expiry, not whether its DB row survived rotation — narrower than the
    original bug since it can only ever name the token's own subject, not fixed here to
    keep the DB out of this endpoint's otherwise pure authorization check.
12. **`_copy_terraform_modules` is an allowlist (`.tf` only today), not a denylist, and
    resolves every candidate path before copying it.** A denylist of "known bad" suffixes
    only ever excludes what someone thought to name; `Path.rglob` also follows directory
    symlinks, so a symlinked module subdirectory (or the module directory entry itself)
    pointing outside the module tree would have its real target's files enumerated with no
    `is_symlink()` true anywhere in that walk. Every candidate is resolved and required to
    stay under the module's own resolved root — the general fix, not a symlink-only
    special case.
13. **`complete_exit` gets its own per-user budget bucket**, matching every other
    customer-account-adjacent endpoint's pattern (F0), and a generic exception handler that
    still writes an audited 500 rather than an unaudited 500 — the same reasoning already
    applied to the export view's own generic handler.
14. **`exited_at` gates infrastructure-service's own mutating endpoints
    (reprovision, config update) with a 409/400, but not application-service's** (deploy,
    app create). infrastructure-service can check the field directly since it owns the row;
    application-service's read-model copy of `Infrastructure` is synced from `infra.created`
    /`infra.deleted` RabbitMQ events only, has no `exited_at` field, and no event carries
    updates to arbitrary fields — wiring that requires either a new event type or an
    inline lookup back into infrastructure-service, both larger than this security-review
    round's scope. Tracked as a follow-up, not fixed here.

## Out of scope

Terraform import codegen for the imperative layer. Anything that writes to the customer's
account — the export is strictly read-only.
