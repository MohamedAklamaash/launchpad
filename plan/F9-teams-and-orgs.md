# F9 — Organizations with members and roles

**Status:** design complete, decided autonomously where possible — **in progress**. Ticket 1
(`Account` table + backfill, auth-service, #146), ticket 2 (`Organization`/`Membership`/
`Invitation`/`MembershipAuditEvent` tables, `org.events` exchange, internal CRUD, auth-service,
#149), ticket 3 (personal-org backfill + `organization_id` columns on infrastructure-service/
application-service/payment-service, #151), and ticket 4 (`org.events` consumers mirroring
Organization/Membership read-models in those same three services, #154) are merged. All
ship behind `ORGS_ENABLED` (default off), so none changes request behavior yet. **Depends
on:** nothing technical. **Blocked by:** nothing. The owner has since decided both Open
Questions below: Stripe consolidation is deferred, and there is one global limit with no
per-org tiers.

The owner is away; per instruction, every question below that has a defensible engineering
answer is decided here, not deferred. Alternatives are noted briefly for each.

## Summary

Formalize what's already informally true — each `Infrastructure` is a de facto single-owner
tenant with invited members — into a real `Organization`/`Membership`/`Invitation` model
owned by auth-service, with a four-role matrix (owner/admin/developer/viewer), JWT carrying
only an `org_id` scope claim (never role), and role authority resolved per-request against a
short-TTL-cached Membership read-model mirrored into infrastructure-service,
application-service, and payment-service the same way `User`/`InfrastructureUserRole` are
mirrored today. Every existing user gets a personal org computed by a deterministic hash (no
cross-service call needed to agree on the id); existing infras/apps/databases/billing rows
gain a nullable `organization_id` backfilled to it. Ships behind `ORGS_ENABLED`, dual-written
against the legacy `invited_users`/`InfrastructureUserRole`/`roles{}` shape until cutover, old
shape retired in its own later ticket — matching the F7 staged-rollout pattern.

## Goals

- Replace the single-owner-plus-flat-invite model with real multi-member organizations that
  support more than one role above "owner" and below "no access."
- Every existing account and every existing infra/app/database keeps working with zero manual
  migration step — personal orgs and `organization_id` backfill are automatic and idempotent.
- Role authority is resolved consistently across all three Django services and the two
  Node identity services, with a bounded, documented staleness window on role change/removal
  — not "whatever the 7-day JWT happened to carry."
- A full permission matrix for all 13 named capability classes, each cell decided.
- Org switching, invitations, an audit log, and the frontend surfaces (switcher, members page,
  invite flow, role-gated UI) are specified to the point an implementer doesn't need to make
  a judgment call on data shape.
- Security properties stated up front: no privilege escalation, no cross-org IDOR, no
  last-owner lockout, no invite-token replay.

## Non-goals

- **Not unifying `User` (GitHub OAuth owners) and `InvitedUser` (password accounts) into one
  login mechanism.** Different auth flows (OAuth has no password; password accounts have
  OTP/reset) stay separate credential stores. A new thin `Account` identity (below) is
  introduced so Membership has one thing to point at — that is identity *linking*, not
  *merging*.
- **Not building seat-based or usage-based org billing tiers.** F9 moves `Billing` from
  per-user to per-org keying only; pricing/tiering is a payment-service product decision out
  of scope here.
- **Not retiring `invited_users`/`InfrastructureUserRole`/`roles{}`` in this body of work.**
  They stay as the dual-write source of truth until a separate cutover ticket flips reads,
  and a separate retirement ticket removes them later — same pattern F7 used for the old
  per-infra CodeBuild path.
- **Not changing how the gateway handles auth.** It still doesn't verify JWTs and still
  passes `Authorization` through unchanged (`CLAUDE.md`); every enforcement point here is a
  Django/Node service that already decodes the token itself.
- **Not solving the pre-existing cross-table email collision question** (could the same
  email exist in both `users` and `invited_users` today, independent of F9) — flagged, not
  fixed, since it predates this work and fixing it is a credential-data cleanup, not an org
  feature.

## Context & constraints

**Current model, verified against `main`:**

- **JWT issuance** — `identity-services/services/auth-service/src/utils/handle-token.ts`.
  `AccessTokenPayload` carries `sub`, `email`, `user_name`, `role` (global), optional
  `roles: Record<infra_id, role>` (per-infra map — this is the thing the comments already
  call "org" informally), optional `scope` (narrow-purpose tokens, e.g. `password_reset`),
  `auth_time`. `JWT_EXPIRES_IN=7d` (`env.example`) — a long-lived access token, which is
  exactly why role must never live in it (see Decision: JWT shape).
- **Two disjoint identity tables, no shared id space.** `db/models/user.model.ts` (`users` —
  GitHub OAuth accounts; `superAdminMiddleware` requires a row here; only these accounts
  create/own infra today) and `db/models/invited-user.model.ts` (`invited_users` — password
  accounts created by `InvitedUserService.register`, carrying `roles: Record<infra_id,
  USER_ROLE>` and `infra_id: string[]`). An invited (password) account can never become an
  infra owner today because ownership is `Infrastructure.user_id == User.id`, and `User` and
  `InvitedUser` are different tables with independently generated UUIDs. `role.ts`'s
  `ROLE_RANK`/`primaryRole`/`authorizeMemberRemoval` already operate on the `roles` map
  per-infra, already calling each infra an "org" in its own comments
  (`authorizeMemberRemoval`: "may remove a member only from infras the caller owns").
- **Invite flow is provisioning, not inviting.** `InvitedUserService.register` (called from
  `RegisterInvitedUser`, gated by `superAdminMiddleware` + `super_user.infra_id.includes
  (infra_id)` — **owner-only, hardcoded**, no admin-can-invite today) takes a `password` the
  *inviter* sets for the invitee and mails an OTP for verification. There is no
  invite-token/accept-later flow anywhere in the codebase.
- **Django side has its own, looser mirror.** `infrastructure-service`'s own
  `Infrastructure` model (`api/models/infrastructure.py`) has only `user` (owner FK) and
  `invited_users` (M2M, binary — invited or not, no role). Its own
  `InfrastructurePermissions` (`api/services/infrastructure_permissions.py`) only has
  `is_super_admin`/`can_update_infrastructure`/`can_delete_infrastructure` — **no
  `get_user_role`, no per-infra role granularity at all**, despite the per-infra role map
  existing upstream in auth-service. `api/repositories/infrastructure.py`'s resolver is
  `Q(user_id=user_id) | Q(invited_users__id=user_id)` — owner-or-member, binary.
  `api/messaging/consumer/consumer.py`'s `AuthEventConsumer` consumes `auth.user.registered`
  but only reads `infra_id` (list) to populate the M2M — **it reads but discards the `roles`
  map**. This is the actual role-granularity gap F9 needs to close, not a hypothetical one.
- **Application-service has the richer mirror** — `api/models/infrastructure_user_role.py`
  (`InfrastructureUserRole`: infra, user, role, unique together) and
  `api/services/infrastructure_permissions.py`'s `get_user_role` (owner → `SUPER_ADMIN`;
  else looks up the `InfrastructureUserRole` edge; else, if in `invited_users` with no role
  row yet, defaults to least-privilege `USER`; else `None`). `api/repositories/user.py`'s
  `_link_invited_infrastructures` is what actually writes `InfrastructureUserRole` from the
  `roles` map on the `auth.user.registered` event, with `PendingInvitedInfrastructure`
  bounded retry when the infra hasn't materialized yet (ordering race between
  `auth.user.registered` and `infrastructure.created` on separate queues). This
  retry-until-materialized pattern is the one F9's Membership sync reuses.
- **Owner-only is a named, audited security posture, not an oversight.** `owner_only.py`
  (application-service) — "Owner-only authorization ladder... extracted from
  `runtime_logs_service.py` (F2)... see `app_metrics_service.py`, the second caller" — backs
  logs, metrics, and (per `launchpad-frontend/types/infrastructure.ts` comments) costs. These
  were deliberately scoped to owner-only during F2/F4 security pre-reviews specifically
  because they AssumeRole into the customer's own AWS account on every call and F0's
  per-user budget exists because that costs the customer real money/API throttle. Widening
  any of these cells is a security-relevant change, not a free one — see Decision: permission
  matrix.
- **`F0`'s budget primitive is per-user, not per-tenant**: `customer_call_budget(user_id,
  bucket, limit, window)`, key `budget:{bucket}:{user_id}` — built that way because identity
  (not tenancy) was the only axis that existed. F7's `builds_concurrent`/`builds_started` are
  already per-infra. Billing (`payment-service/api/models/billing.py`) is per-`user_id`.
  `payment-service`'s own `Infrastructure`/`User` mirrors (`api/models/{infrastructure,user}
  .py`) are thin, synced the same consumer-pattern way
  (`api/messaging/consumer/{infrastructure,user}.py`).
- **H3 already fixed the adjacent class of bug this touches.** Unauthenticated/under-scoped
  identity endpoints were a prior finding (`plan/H-hardening.md` §H3): callers must be
  derived from a verified token, never a path/body-supplied id; non-members get 404 not a
  distinguishing 403 (`owner_only.py`'s `LookupError`/`PermissionError` split); OTP/token
  issuance follows `issue_onboarding_token`'s pattern (SHA-256 at rest, single-use, TTL,
  plaintext returned exactly once). F9's `Invitation` reuses that pattern wholesale rather
  than re-deriving it.
- **F1b already learned that wall-clock event ordering fails across publishers** —
  `host_readiness_version`, a per-row monotonic counter the row itself hands out, replaced
  `occurred_at` specifically because "this service's several publishers... give no
  cross-publisher ordering guarantee." Membership sync has the same shape (role-change events
  racing membership-removal events) and must use the same fix, not repeat the mistake.
  `host_readiness_version`'s rule — "a payload missing it is rejected outright, never treated
  as always current" — is cited explicitly in Decision: cross-service sync below.
- **`auth-service` has no migration framework** (`memory/auth-service-no-migrations.md`):
  `sequelize.sync()` only creates missing tables; existing-table column adds need a manual,
  idempotent `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` in `db/index.ts`'s `initModels`
  (precedent: H3's `purpose` column on `invited_user_otp`). This bounds how F9 touches
  `users`/`invited_users`.
- **Naming convention for truncated ids is a hard rule** (`CLAUDE.md`): never a UUIDv7 prefix
  as a namespace/uniqueness key (forceable from `created_at`). `Infrastructure.dns_label`
  (independent random token) and `Database.module_name()` (full-UUID hash) are the two
  correct patterns already in the codebase. F9's deterministic personal-org id must follow
  the full-hash pattern, never a slice.
- **Feature-flag convention**: `EKS_ENABLED`, `PLATFORM_BUILD_ENABLED` — both default-off,
  gate a whole code path, flipped on once validated. `ORGS_ENABLED` follows the same shape.
- **Django routes versioned `/api/v1/...`, gateway exposes unversioned `/api/...`**; new
  gateway endpoints are added the same `endpoints/<service>.py` + `proxy_request` way as
  every existing route (see `gateway-service/app/api/endpoints/auth.py`).
- **Critical paths touched**: auth (JWT shape, every Django service's `JWTAuthMiddleware`),
  billing (money), and the owner-only AssumeRole-backed endpoints (logs/metrics/costs/nuke/
  exit). All three get elevated rigor below and a required security pre-review.

## Options considered

### Identity: how Membership references a person

- **Option A — new `Account` identity table, `users`/`invited_users` keep existing as
  credential stores, each gets an additive nullable `account_id`.** New rows (OAuth signup,
  invite accept) always create `Account` + credential row together; backfill creates one
  `Account` per existing row, deduped by email via a deterministic id.
  - **Pros**: no destructive change to either credential table; OAuth-vs-password login
    mechanics untouched; Membership/Organization get one clean thing to point at;
    additive-only migration (new table + nullable column), trivial rollback (drop column).
  - **Cons**: a third table in the identity graph; every service that currently joins on
    `User.id`/`InvitedUser.id` eventually wants to join on `account_id` instead — not for F9,
    but it's the seam a future identity-unification project would use.
  - **Cost/complexity**: M. One new table, two additive columns, one backfill script.
- **Option B — merge `invited_users` into `users`** (add `auth_method`, nullable
  `password_hash`/OTP columns onto `users`; retire `invited_users`).
  - **Pros**: one table, no indirection.
  - **Cons**: `users` gains a pile of password-account-only columns that are always null for
    OAuth rows and vice versa; every existing query against either table needs a rewrite;
    much larger blast radius for a feature whose actual ask is "organizations," not "merge
    our two user tables"; a bad migration here risks login itself, the one thing that must
    never break. Violates "no comments literally everywhere" cost but more importantly
    violates the project's own migration-risk posture (auth-service has no rollback
    mechanism for a destructive table merge beyond a manual restore).
  - **Cost/complexity**: L, and on the single highest-blast-radius table in the platform.
- **Option C — don't unify at all; `Membership.member_id` + `member_kind` enum pointing at
  either table.**
  - **Pros**: zero new table.
  - **Cons**: every join, every FK, every "who is this" check in Organization/Membership/
    Invitation/audit-log now needs the enum discriminator; this is exactly the kind of
    polymorphic-reference pattern that produces bugs at every call site forever, for a
    problem Option A solves with one new table.
  - **Cost/complexity**: S to build, but a permanent tax on every future join — rejected on
    maintainability grounds, not effort.

### JWT role authority: claim vs. per-request lookup

- **Option A — role fully in JWT, refreshed only at token expiry/refresh.** Simple, zero
  extra DB/cache hit per request.
  - **Cons**: `JWT_EXPIRES_IN=7d` means a demoted/removed member keeps acting at their old
    role for up to a week unless every revocation path also force-revokes refresh tokens
    (it already does for full removal, `removeFromOrg`, but not for a same-org demotion).
    This is the exact staleness problem the task explicitly asks to decide, and a 7-day
    window is not an acceptable answer for "I just removed this admin."
- **Option B — nothing in JWT; every request does a DB lookup.** Matches today's Django
  pattern (`InfrastructurePermissions.get_user_role` is a live DB read every call).
  - **Cons**: adds a DB round-trip to every authorized request across three services, for
    data that changes rarely (role changes are not a hot path).
- **Option C (chosen) — split the claim: `org_id` in the JWT as a scope selector (which
  tenant this token may act in; immutable until org-switch mints a new token), role resolved
  per-request from the mirrored Membership row through a short-TTL cache (not the DB) and
  **not** from the JWT at all.**
  - **Pros**: `org_id` in the token means a stolen/replayed token can't be pointed at a
    different org with no DB hit (cheap, cryptographically backed check); role staleness is
    bounded by the cache TTL (seconds), not the 7-day token lifetime; matches the existing
    Django per-request-lookup convention (`get_user_role`) instead of replacing it, so
    existing call sites generalize rather than get rewritten from scratch.
  - **Cons**: a cache layer to build and invalidate correctly (mitigated: reuse the Redis
    instance F0's budget primitive already uses, same INCR/TTL operational pattern the
    team already runs).
  - **Cost/complexity**: M.

### Cross-service sync shape

- **Option A — reuse `occurred_at` wall-clock ordering**, matching how `infrastructure.created`
  already propagates.
  - **Cons**: F1b's own hardening note says this already failed across multiple publishers
    and had to be replaced by `host_readiness_version`. Membership has at least two
    publishers in practice (an explicit role-change call, and an invite-accept) that can race
    the same way. Repeating a mistake the codebase already paid to fix.
- **Option B (chosen) — per-membership monotonic version**, same shape as
  `host_readiness_version`: incremented under a row lock on every Membership write, carried
  on every `org.membership.changed` event, and a payload missing it (or older than what's
  stored) is rejected outright by every consumer — never treated as "always current."
  - **Cost/complexity**: S once the pattern exists; it's a copy of a pattern already proven
    in this codebase, not a new design.

## Decision

Build Option A/C/B above as one coherent design: a new `Account` identity, `org_id`-only JWT
scope with cached per-request role resolution, and version-stamped Membership sync. Rationale
tied to goals: this is the only combination that (a) lets any existing invited (password)
account eventually hold any role including owner without a destructive table merge, (b)
bounds role-change staleness to seconds instead of the 7-day token TTL, and (c) doesn't
repeat the wall-clock-ordering bug F1b already found and fixed once.

### Data model

New tables, **auth-service owns the canonical copies** (it already owns User/InvitedUser,
mints the JWT, and runs invite/OTP — keeping ownership there is the smaller diff, not a new
direction):

- **`Account`** `(id, email, display_name, created_at)`. Backfill id =
  `uuid5(ACCOUNT_NAMESPACE, normalized email)`, one per *distinct normalized email*, not one
  per legacy row. A same-email pair across `users`/`invited_users` (or even within one table,
  since `email`'s unique index is case-sensitive) computes the same id, but an Account is
  **never linked to a second credential row**, by the backfill or by the live paths: whoever
  can log in as any row holding an Account acts as that Account in every org, so a merge is
  a decision that one person controls both rows, made by a human. The backfill links a row
  only when it is the single unlinked verified row for that email and no other row holds the
  Account; otherwise it logs `accountBackfillSharedAccount` and links nothing. Separately, an
  inviter-chosen password never survives first OTP verification (the invitee sets one, or the
  account has none until forgot-password), so an inviter cannot keep a working login on an
  email they do not own. This is
  safe despite email being mutable because the id is computed **once**, at backfill time, and
  stored in the FK — no service ever recomputes `Account.id` from a row's *current* email
  later (that pattern — a service deriving the same id independently, with no coordination —
  is `Organization`'s `uuid5(ORG_NAMESPACE, legacy_user_id)` below, keyed on an immutable id,
  not email). Organic rows created by later tickets (OAuth signup, invite accept) have no
  prior row to agree with, so they get a plain `uuidv7()` like every other table here.
  `users.account_id` and `invited_users.account_id` added via idempotent
  `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` in `db/index.ts` (H3's `purpose`-column
  precedent), nullable, backfilled, then enforced `NOT NULL` once backfill is confirmed
  complete (separate ticket, see Execution plan).
- **`Organization`** `(id, owner_account_id, name, created_at, updated_at)`. Personal-org id
  for an existing owner = `uuid5(ORG_NAMESPACE, legacy_user_id)`, where `legacy_user_id` is
  the pre-F9 `users.id` (auth-service) — the same id already sitting in every mirror's
  `Infrastructure.user_id` column today (`infra-created.consumer.ts`'s `User.findByPk
  (user_id)` confirms the Django mirrors already carry this exact id). This is deliberately
  **not** keyed on the new `Account.id`: infrastructure-service/application-service/
  payment-service can each compute `uuid5(ORG_NAMESPACE, infrastructure.user_id)` straight off
  a column they already have, with no dependency on Account having backfilled first — the
  specific property that makes a three-service, no-coordination backfill safe to run in any
  order and idempotent on retry. auth-service computes the identical id the same way
  (`uuid5(ORG_NAMESPACE, users.id)`) when it creates the `Organization` row and sets
  `owner_account_id` to that same row's (now-backfilled) `Account.id`.
- **`Membership`** `(id, organization_id, account_id, role, version, created_at, updated_at)`,
  unique `(organization_id, account_id)`. `version` is a per-row monotonic counter,
  incremented under a row lock on every write — the `host_readiness_version` pattern.
  **Invariant, enforced by a partial unique index**: `UNIQUE (organization_id) WHERE role =
  'owner'` — exactly one owner per org, enforced by the database, not just application logic.
- **`Invitation`** `(id, organization_id, email, role, invited_by_account_id, token_hash,
  expires_at, accepted_at, revoked_at, created_at)`. Token issuance/verification is a direct
  copy of `Infrastructure.issue_onboarding_token`: plaintext `secrets.token_urlsafe(32)`
  returned exactly once, SHA-256 at rest, single-use (checked via `accepted_at IS NULL AND
  revoked_at IS NULL AND expires_at > now()`), 7-day TTL (matches invite-email conventions
  elsewhere in the codebase; shorter than the onboarding token's 24h because this isn't a
  "run it right now" script, it's "an email someone might not open for a few days").
- **`MembershipAuditEvent`** `(id, organization_id, actor_account_id, action,
  target_account_id, old_role, new_role, occurred_at)`. Append-only, insert-only API — no
  update/delete path exists, which is itself the integrity guarantee.

Mirrors in infrastructure-service, application-service, payment-service: thin `Organization`
and `Membership` read-model tables, same shape as today's `InfrastructureUserRole` mirror,
synced via a new `org.events` exchange (routing keys `org.created`, `org.membership.changed`,
`org.membership.removed`, `org.invitation.accepted`) consumed with the exact
retry-until-materialized pattern `_link_invited_infrastructures`/`PendingInvitedInfrastructure`
already implements, generalized from "wait for the infra to materialize" to "wait for the org
to materialize." Each payload carries `version`; a consumer rejects (does not apply, does not
nack-and-drop — logs and skips, since an older replay is not an error) any event whose
`version` is not greater than what's stored.

`Infrastructure` (all three services' copies), `Application`, `Database`, and `Billing` each
gain a nullable `organization_id`, backfilled to the owning account's personal-org id, then
`NOT NULL` once backfill is confirmed (same two-step additive pattern the advisor flagged for
money-path safety on `Billing` specifically — keep `user_id`/`owner_account_id` columns
alongside `organization_id` indefinitely as the rollback path, never drop them in this body of
work).

### Role set and permission matrix

Four roles, replacing the current admin/user/guest-plus-implicit-super_admin-owner set:
**owner, admin, developer, viewer**. Migration mapping: existing infra owner (`User.id ==
Infrastructure.user_id`) → `owner`; `InfrastructureUserRole.role == ADMIN` → `admin`;
`== USER` → `developer`; `== GUEST` → `viewer`.

| Capability | owner | admin | developer | viewer |
|---|---|---|---|---|
| View infra/app detail, deploy status | yes | yes | yes | yes |
| Create infrastructure | yes | yes | no | no |
| Delete infrastructure / nuke | yes | no | no | no |
| Deploy | yes | yes | yes | no |
| Rollback | yes | yes | yes | no |
| Logs & metrics (read) | yes | yes | yes | no |
| Costs (read) | yes | yes | no | no |
| Env vars & secrets (read+write) | yes | yes | yes | no |
| Databases (create/attach/delete) | yes | yes | no | no |
| Custom domains | yes | yes | no | no |
| Exit (account closure) | yes | no | no | no |
| Members: invite | yes | yes (not as owner) | no | no |
| Members: remove | yes | yes (not owner) | no | no |
| Members: change role | yes | yes (not to/from owner) | no | no |
| Billing | yes | no | no | no |

`viewer` exists to give a stakeholder (e.g. a client, a non-technical founder) read access to
"is it up, did the deploy work" without touching anything that costs money or exposes secrets
— hence the one unconditional `yes` row and nothing else.

Four cells are deliberate, security-relevant **widenings** from current behavior, each
flagged for the required pre-review (see Risks):

- **Logs/metrics move from literally owner-only to owner+admin+developer; costs move to
  owner+admin only, not developer.** Current code (`owner_only.py`, F2/F4 pre-reviews) gates
  both identically and strictly to the owner — this was the right call when the only
  non-owner role was "invited viewer," but a role literally named "developer" that can deploy
  and roll back, yet cannot see why its own deploy failed, defeats the purpose of having a
  developer tier. The two are split, not bundled, because they carry different sensitivity:
  a CloudWatch log/metric tail (F2's AssumeRole-backed read) is an operational artifact of a
  deploy the developer themselves triggered, while Cost Explorer data is financial
  information about the whole account — kept at owner+admin, matching F4's original
  reasoning more closely than logs ever needed to. Alternative considered and rejected:
  leave both owner-only forever — makes `admin`/`developer` meaningless for a team product,
  which is the whole point of F9.
- **Deploy/rollback/env-vars move from admin-only to admin+developer.** Current
  `can_update_application`/`can_create_application` gate at `[SUPER_ADMIN, ADMIN]` only — an
  invited `USER`-role member today is view-only. A role named "developer" with no deploy right
  is not a developer role. Alternative considered and rejected: keep today's gate and just
  rename `USER`→`developer`/`GUEST`→`viewer` cosmetically — fails the task's explicit ask for
  deploy/rollback to be distinct, decided matrix cells.
- **Nuke, exit, and billing stay owner-only, unchanged, on purpose.** These are monotonic,
  irreversible, or money-moving. Exit specifically keeps its existing `auth_time` reauth gate
  (F6) — widening or weakening either would contradict a decision already made under a
  security pre-review and is explicitly out of scope here.

**Owner decision (2026-10-31): developers keep spend authority.** A `developer` can create
applications and change cpu, memory, port, branch and dockerfile path. Those cells stay as
decided above and were reviewed and accepted by the owner as a known cost-control trade-off,
not an open finding.

Admin cannot grant or hold the `owner` role, cannot remove or demote the org's owner, and
cannot touch billing — enforced by: the partial-unique-index invariant (only one `owner` row
can ever exist) plus an explicit role-transition check (`new_role == 'owner'` requires
`actor_role == 'owner'`; `target_role == 'owner'` on a remove/demote requires
`actor_role == 'owner'`).

**Ownership transfer** (owner reassigns `owner` to a different member): a single transaction
that (1) sets the new member's role to `owner`, (2) sets the old owner's role to `admin`,
gated behind the same reauth (`auth_time` within the exit-flow's existing freshness window)
used for exit — this is as consequential as account closure and reuses that exact gate rather
than inventing a second one.

### JWT and authorization

`AccessTokenPayload` gains `org_id` (the active organization scope) and drops nothing — `role`
stays for backward compatibility with any caller still reading it during rollout, but every
authorization decision after F9 ignores it. Role resolution: `OrgPermissions.get_role
(org_id, account_id)` — one function per service, mirroring `InfrastructurePermissions
.get_user_role`'s existing shape — checks a short-TTL (60s) cache keyed `org_role:{org_id}:
{account_id}` in the same Redis instance F0 already uses, falling back to the mirrored
`Membership` table on a miss. On any role change or removal, the writing service publishes
`org.membership.changed`/`org.membership.removed` **and** every consuming service evicts that
cache key on receipt — bounding staleness to the smaller of (60s TTL, event-propagation
latency), not 7 days. Removal and any demotion away from `owner` additionally revokes the
target's refresh tokens (reuse `removeFromOrg`'s existing `RefreshToken.destroy`), forcing a
re-login rather than relying on cache eviction alone for the highest-consequence transition.

**Org switching**: `POST /api/auth/switch-org {organization_id}` — verifies an active
Membership row for the caller in that org, mints a new access token with `org_id` updated and
`auth_time` carried over unchanged (same pattern `RefreshTokenForUser` already uses for
rotation). The refresh token itself gains an `active_org_id` column (same idempotent-ALTER
precedent as `Account.id` above) that `switch-org` updates — without it, every
`POST /auth/refresh` would have nothing to read `org_id` from and would silently drop the
caller back to an unintended org on their next token refresh.

**Two rollout-day compatibility gaps, decided now:**

- **Pre-F9 access tokens carry no `org_id` at all** (7-day TTL means these are still live
  well after `ORGS_ENABLED` flips). `JWTAuthMiddleware`/`verifyAccessToken` treat a missing
  `org_id` as "this caller's personal org" — computed as `uuid5(ORG_NAMESPACE, sub)` with no
  DB lookup, exactly the same formula backfill used, so an old token works unchanged until it
  expires or the caller switches orgs.
- **`/auth/refresh` must know which org to mint the new access token for.** Read from the
  refresh token's own `active_org_id` column (set at login to the caller's personal org,
  updated by `switch-org`), never defaulted blindly to "personal org" on every refresh — that
  would silently undo an org switch on the next token rotation.

**Cross-org IDOR**: every resource lookup filters by **both** `organization_id == token.org_id`
and an active Membership — not membership alone. A request scoped to the wrong org for a
resource that legitimately exists returns the same 404 `owner_only.py`'s `LookupError`
convention already uses for "doesn't exist or you're not a member" — never a 403 that would
let a caller distinguish "exists, wrong org" from "doesn't exist."

### Billing and per-org limits

`Billing.user_id` stays; `Billing.organization_id` added (nullable → backfilled →
`NOT NULL` later), payment-service's `Infrastructure`/`User` mirrors gain `organization_id`
the same way. One Stripe customer per organization going forward; **not** retroactively
merging any already-created per-user Stripe customer record into an org-level one in this body
of work (money-adjacent data migrations need an explicit owner sign-off — see Open Questions).

`customer_call_budget` gets an org-aware sibling, `customer_call_budget(org_id, bucket, limit,
window)` with key `budget:{bucket}:org:{org_id}` — the existing per-user signature and keys
are **not removed**, since the cost/throttle exposure F0 bounds is the customer account
(= the org, once `organization_id` exists on `Infrastructure`), not the individual human who
happens to click the button. F7's `builds_concurrent`/`builds_started` are already per-infra
and infra→org by the backfill, so no change needed there beyond the join.

### Invitations

`POST /api/organizations/{id}/invitations {email, role}` — owner/admin only, role must be
`<= admin` per the matrix above (admin cannot invite-as-owner). Rate-limited per-org via the
same `customer_call_budget` primitive, new bucket `invites` — stops invite-spam/email-bombing
of arbitrary addresses, fail-closed on Redis-down matching every other F0 bucket's posture.
Email sent via notification-service (`templates/infra-email.template.ts`'s pattern, new
`invitation.template.ts`), the link bound to the invited email — `GET
/invitations/{token}/accept` validates token hash + TTL + unused, and **requires the
accepting identity's email to equal the invitation's email** (closes an invite-token-forwarded-
to-a-different-account hijack). Accept creates (or links, if the email already has an Account)
the Account + Membership row atomically, publishes `org.invitation.accepted`, and is itself
unauthenticated (token-authenticated), following the onboarding callback's existing exemption
pattern — listed explicitly wherever a Django service would otherwise need it (it doesn't:
this endpoint lives in auth-service, Node/Express, not behind `JWTAuthMiddleware` at all).

### Audit log

`GET /api/organizations/{id}/audit-log` — owner/admin only. Every Membership/Invitation
mutation writes one `MembershipAuditEvent` row in the same transaction as the mutation itself
(not a side effect published after the fact) so the log can never drift from what actually
happened.

### Frontend

- **Org switcher** in the dashboard layout header (`app/dashboard/layout.tsx`): lists the
  caller's orgs (from a new `GET /api/organizations` — "orgs I'm a member of"), calling
  `switch-org` on selection, storing the new access token the same way login already does
  (no new client-side storage mechanism).
- **Members page** (`app/dashboard/organizations/[id]/members/page.tsx`, new): list with role,
  invite form (email + role dropdown, capped at the caller's own role per the matrix), pending
  invitations with revoke, remove-member action — all gated client-side by a `useOrgRole()`
  hook that fetches `GET /api/organizations/{id}/membership/me` once per org-switch (role is
  never read out of the JWT client-side either, consistent with the backend decision) and
  re-fetches on a `membership.changed` toast/poll.
- **Invite flow**: accept page at a new frontend route reading the token from the URL path
  (not a query string, learning H3's R3/B3 experience with link hygiene), same shape as the
  existing onboarding flow's token handling.
- **Settings page** gains an org billing tab reusing `app/dashboard/settings/page.tsx`'s
  existing layout.

## Risks & mitigations

- **Security: three permission-matrix widenings (logs/metrics/costs to admin; deploy/
  rollback/env-vars to developer) touch AssumeRole-backed, customer-billed, previously
  owner-only-by-security-decision surfaces.** Mitigation: this file requires a security
  pre-review before any ticket touching the matrix lands (see below), explicitly re-examining
  F2/F4's original owner-only rationale against the new admin/developer definitions, same as
  how F2 itself required two pre-review passes.
- **Privilege escalation via role-change/invite endpoints.** Mitigation: DB-level invariant
  (partial unique index, exactly one owner), explicit transition checks (no actor can grant a
  role above their own; only owner touches owner), tested with adversarial cases (admin
  attempts to self-promote, attempts to invite as owner, attempts to remove the owner).
- **Cross-org IDOR.** Mitigation: every lookup filters on `organization_id == token.org_id`
  AND membership, never membership alone; non-member/wrong-org always 404, tested against the
  existing local multi-tenant E2E harness's cross-tenant probe pattern
  (`memory/local-e2e-multitenant-harness.md`).
- **Last-owner lockout** (org left with zero owners, e.g. a race between two removal
  requests). Mitigation: the partial unique index makes a second `owner` row impossible to
  create, and removal/demotion of the current sole owner is rejected at the application layer
  before the transaction even opens (checked under a row lock on the Membership table).
- **Invite-token replay/forwarding.** Mitigation: SHA-256-at-rest, single-use, TTL, and the
  accept flow's accepting-email-must-equal-invited-email check, closing the specific hijack
  where a forwarded invite link is redeemed by a different account than the one it was issued
  to.
- **Event-ordering races across org.events publishers** (a role change and a removal racing
  each other, or an org.events consumer seeing invite-accept before the org itself has
  materialized). Mitigation: per-Membership monotonic `version`, rejected-if-stale consumer
  rule (F1b's `host_readiness_version` pattern), and the existing
  `PendingInvitedInfrastructure`-style bounded retry for "org not materialized yet."
- **Role-staleness via cache** (60s TTL means a demoted admin can act for up to 60s after
  demotion). Mitigation: explicit cache-eviction-on-event in addition to the TTL (so the
  common case is sub-second, not 60s), and a refresh-token revoke on any demotion away from
  `owner` specifically — the one role transition where "up to 60 seconds" is judged
  unacceptable regardless of cache behavior.
- **Backfill correctness/idempotency across three services with no synchronous coordination.**
  Mitigation: the deterministic `uuid5` id scheme means every service computes the same
  `Organization` id for the same account independently — no service has to wait for or call
  another during backfill, and re-running the backfill script is a no-op on rows already
  migrated.
- **Money-path migration risk on `Billing`.** Mitigation: strictly additive (`organization_id`
  nullable alongside the existing `user_id`, never dropped in this body of work), matching the
  project's own stated principle that data migrations need a rollback path.
- **Observability gap if left unaddressed.** Mitigation (added in Execution plan): structured
  log line on every Membership/Invitation mutation (actor, target, org, old/new role), a
  metric for invite-accept rate and role-change rate per org, and an alert on an abnormal
  spike in role-change-to-owner attempts (a leading indicator of a privilege-escalation
  attempt, not just a UX event).
- **Rollback path**: `ORGS_ENABLED=false` reverts every service to reading
  `invited_users`/`InfrastructureUserRole`/`roles{}` exactly as today, since those are kept as
  the dual-write source of truth through the entire rollout (see Execution plan) — the flag
  flip is the rollback, no data needs to be un-migrated.

## Execution plan

Each ticket is one PR, independently shippable behind `ORGS_ENABLED` (default off until the
cutover ticket).

1. **`Account` table + backfill, auth-service.** New table, additive `account_id` columns on
   `users`/`invited_users` via idempotent `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`
   (`db/index.ts`), backfill script computing `uuid5(ACCOUNT_NAMESPACE, email)` for every
   existing row, deduped by normalized email. Tests: backfill idempotency (run twice, same
   result), email-collision-across-tables behavior documented (not fixed — Non-goals).
2. **`Organization`/`Membership`/`Invitation`/`MembershipAuditEvent` tables + `org.events`
   exchange, auth-service.** CRUD for org/membership (no HTTP surface yet — internal only),
   the partial-unique-owner-index migration, `version` increment-under-lock helper. Tests:
   DB-level invariant (attempt to insert a second owner row fails), version monotonicity.
3. **Personal-org backfill + `organization_id` columns on `Infrastructure`/`Database`
   (infrastructure-service), `Infrastructure`/`Application` (application-service),
   `Infrastructure`/`Billing` (payment-service).** Nullable, backfilled via the deterministic
   `uuid5(ORG_NAMESPACE, infrastructure.user_id)` id computed straight off the `user_id`
   column every one of these tables (or its parent `Infrastructure`) already carries — no call
   to auth-service needed, `NOT NULL` deferred to ticket 9. Tests: backfill idempotency per
   service, cross-service id agreement (same legacy owner id → same computed org id in all
   three services, asserted directly, not via a live call).
4. **`org.events` consumers in infrastructure-service/application-service/payment-service**,
   mirroring `Organization`/`Membership` read-models with the version-rejection rule and the
   `PendingInvitedInfrastructure`-style bounded retry. Tests: out-of-order delivery (older
   version arrives after newer — rejected), org-not-yet-materialized retry/give-up,
   idempotent redelivery, flag off → consumer not started.
5. **`OrgPermissions.get_role` + Redis cache + cache-eviction-on-event**, one implementation
   per Django service mirroring `InfrastructurePermissions.get_user_role`'s shape; JWT gains
   `org_id` plus the missing-`org_id`-means-personal-org and refresh-token `active_org_id`
   handling above; `switch-org` endpoint **plus its gateway route**
   (`gateway-service/app/api/endpoints/auth.py`, proxied the same way every other
   `/auth/*` route is). Tests: role resolution matches the matrix for all four roles, cache
   hit/miss/eviction, switch-org rejects a non-member target org, a pre-F9 token with no
   `org_id` resolves to the caller's personal org, refresh preserves `active_org_id`.
   **Security pre-review required before merge** — this is the authorization core.
6. **Invitation HTTP surface + notification-service email template + accept flow, plus a new
   gateway router** (`gateway-service/app/api/endpoints/organization.py`, UUID-typed path
   params per H5's existing convention). Tests: token single-use, TTL expiry,
   accepting-email-must-match, rate limit (`invites` bucket) under Redis-down fail-closed.
7. **Permission-matrix enforcement rewrite** across infrastructure-service/application-service
   views, replacing `can_update_application`/`can_attach_database`/etc. with
   `OrgPermissions`-backed checks per the matrix table, including the three deliberate
   widenings. Tests: every matrix cell, both allowed and denied directions, for all four
   roles. **Security pre-review required before merge** (the three widenings specifically).
8. **Ownership transfer + last-owner-removal rejection + refresh-token revoke on
   demotion-from-owner.** Tests: transfer is atomic (both role changes or neither), reauth gate
   enforced, last-owner removal/self-demotion rejected with 409, admin cannot perform transfer.
9. **Billing/budget re-keying**: `customer_call_budget` org-aware sibling, `invites` bucket,
   `Billing.organization_id` wired into payment-service's own read path (Stripe customer
   creation stays per-user until the owner decision in Open Questions). Flip `organization_id`
   to `NOT NULL` on `Infrastructure`/`Application`/`Database`/`Billing` once tickets 3–8 are
   verified in a real environment (mirrors F7 ticket 10's "flip the default on" gate).
10. **Frontend**: org switcher, members page, invite/accept UI, role-gated UI via
    `useOrgRole()`, settings billing tab. Tests: role-gated controls hidden/disabled per role
    (component tests), E2E invite-to-accept-to-member-visible flow.
    **Legacy invite verify (from the PR #166 security review):** `POST
    /api/auth/authenticate-with-otp` accepts an optional `new_password`; the frontend verify
    page does not send it yet. Until it does, a newly verified invited user has no usable
    password and must use forgot-password once. The verify page should prompt for a password
    and send it.
    **Accept page requirements (from the ticket 6 security review):** the invitation token
    arrives in the URL fragment (`#token=...`). The page must scrub it with
    `history.replaceState` before any analytics or session-replay script loads, must never
    move it into a query string or a `?next=` value when bouncing through login (keep it in
    memory or sessionStorage across the login redirect), and must send it with
    `POST /api/invitations/accept` carrying only the bearer header and the JSON body.
11. **Audit log HTTP surface** (added to the same `endpoints/organization.py` gateway router
    from ticket 6) **+ observability** (structured log line, role-change-rate metric,
    owner-role-change-attempt alert). Tests: every mutation produces exactly one audit row in
    the same transaction.
12. **Cutover**: flip `ORGS_ENABLED=true` by default once 1–11 are verified (mirrors F7's
    staged real-environment validation). Legacy `invited_users`/`InfrastructureUserRole`/
    `roles{}` retirement is explicitly its own later ticket, not part of this plan — same
    "old path stays in the codebase pending its own separate retirement ticket" decision F7
    made for its CodeBuild path.

### Security pre-review

**Required**, at two points specifically called out above (tickets 5 and 7) — this widens
three previously owner-only, AssumeRole-backed, customer-billed surfaces and introduces a new
unauthenticated accept endpoint. Matches the project's own stated rule ("run a security
pre-review on each slice before implementing it... it returned BLOCK twice on F2 alone").

## Rollout notes

Operational steps for shipping the auth-service side of F9. Order matters.

1. **Boot the new auth-service before running `backfillAccounts`.** The `account_id` and
   `email_verified_at` columns are added by `applyPendingSchemaPatches()` on boot
   (`sequelize.sync()` never alters existing tables). The backfill
   (`pnpm --filter auth-service backfill:accounts`) reads and writes those columns, so
   running it against a database the new code has not yet booted against fails or links
   nothing. Deploy, wait for the service to come up healthy, then run the backfill, then
   review its collision log before enabling `ORGS_ENABLED`.
2. **Re-invite previously-verified users whose code expired before this deploy.** The boot
   patch sets `email_verified_at` only for rows that are `is_authenticated = true` at that
   moment. A user who had verified, was then invited to another infra (which resets
   `is_authenticated` to false), and let that code expire before the deploy has no
   `email_verified_at`. They cannot sign in with a password and `backfillAccounts` will not
   link them. They have to be re-invited so they can verify again.

   Runbook:

   1. List candidates (no live REGISTER code, never verified):

      ```sql
      SELECT iu.id, iu.email, iu.infra_id, iu.invited_by
      FROM invited_users iu
      WHERE iu.email_verified_at IS NULL
        AND NOT EXISTS (
          SELECT 1 FROM invited_user_otp o
          WHERE o.invited_user_id = iu.id AND o.purpose = 'register' AND o.expires_at > NOW()
        );
      ```

   2. Ask the inviter (`invited_by`) to invite the same email to the same infra again from
      the dashboard. This issues a fresh REGISTER code to the user's inbox.
   3. The user enters the code. That sets `email_verified_at` and links the Account.
   4. Re-run `backfillAccounts` (idempotent) if you want the link before they next sign in.

   Invites are limited to 3 per target email per hour and fail with 503 if Redis is down, so
   spread out re-invites for the same address and check Redis first if they all fail.
3. **Run deployed environments with `NODE_ENV=production`.** The invite controller echoes
   the OTP in the response body when `NODE_ENV` is `development` or `test`. That is a dev
   convenience and must never be on in a deployed environment, staging included. Verify
   the variable before enabling invites.

4. **Invitations (ticket 6) deviate from the Decision text in three ways, on purpose.**
   - Accept is `POST /api/invitations/accept {token}`, not `GET /invitations/{token}/accept`.
     The token stays out of URLs, gateway access logs and `Referer`. The email link is
     `{DASHBOARD_URL origin}/invitations/accept#token=...` (fragment, never sent to a server);
     the dashboard page that reads the fragment and POSTs it is a frontend ticket and does
     not exist yet, so leave `ORGS_ENABLED` off until it ships.
   - Accept requires a session JWT as well as the token. `linkAccountForVerifiedIdentity`
     needs a credential row, and "the email's owner is the one accepting" is proven by that
     row being email-verified (`InvitedUser.email_verified_at`, or a GitHub `User`, whose
     email is the primary verified one) with a normalized email equal to the invitation's.
     An invitee with no credential row has to sign in with GitHub first; there is no
     password-signup path for them yet.
   - The per-org `invites` bucket is a Redis counter in auth-service (`budget:invites:org:{id}`,
     20 per hour), because `customer_call_budget` is Python-only. It fails closed (503).
   - `DASHBOARD_URL` must be set on notification-service, or invitation email jobs fail
     permanently (the link cannot be built).

5. **Personal orgs must exist before `ORGS_ENABLED` is flipped.** Nothing else creates them.
   The Django services stamp every pre-F9 resource with `organization_id =
   uuid5(ORG_NAMESPACE, infra.user_id)`, so with the flag on an owner whose personal org was
   never created resolves to no role. Order:

   1. Boot the new auth-service, run `backfill:accounts` (note 1), review its collision log.
   2. Make sure each Django mirror's `org.events` consumer is running. The consumer starts
      only when that service has `ORGS_ENABLED=true`, and its durable queue is what binds to
      the exchange. `org.created` published while no queue is bound is dropped by the broker.
      If a mirror was not consuming during step 3, use `--republish-existing` below.
   3. Run the personal-org backfill with the flag set on that process only:

      ```bash
      ORGS_ENABLED=true pnpm --filter auth-service backfill:personal-orgs
      ```

      It creates `uuid5(ORG_NAMESPACE, users.id)` for every GitHub `User` with a linked
      `account_id`, owned by that Account, and publishes `org.created` for each. Re-running is
      safe: existing orgs are skipped. It refuses to start if the `org.events` publisher cannot
      connect. Exit code `2` means at least one `conflict` (an org with that id is owned by a
      different Account; left untouched, logged as `personalOrgConflict`), `inconsistent`
      (org exists, owner Membership missing) or `failed` row. Resolve those by hand before
      continuing. `skippedUnlinked` users (email collision held for review) get no org until
      they are linked; they are logged as `personalOrgSkipped`. `shared_account` users (another
      `users` or `invited_users` row holds the same Account; logged as
      `personalOrgSharedAccount`) also get no org and also make the script exit `2`.

      **Never link a `User` to an Account held by an `InvitedUser`, or the reverse, without
      confirming that one person controls both.** An `InvitedUser` password may have been chosen
      by an inviter, and the shared Account would let that login act as the GitHub user in
      every org they own. For a legacy shared Account, unlink the `InvitedUser` row
      (`account_id = NULL`, and have that person verify again) after checking the personal org
      and its mirrors were not already created for it.

      `org.created` goes through the `org_event_outbox` table (see note 7). The script drains
      the outbox itself on a confirm channel and exits `3` if any row is still pending
      afterwards (the running service's dispatcher also delivers pending rows, and a re-run
      with `--republish-existing` enqueues them again).
   4. Verify every mirror received the events (queries below). If any are missing, re-run with
      `-- --republish-existing`: it re-emits `org.created` for correctly owned existing orgs
      and writes nothing. Consumers ignore a redelivery whose `version` is not newer.
   5. Only then set `ORGS_ENABLED=true` on the long-running services.

   **Reset inviter-chosen passwords before the flip.** Before verify-time reset existed, an
   inviter chose the password on the row they created, and verified invitees kept it, so the
   inviter can still log in as those rows. There is no signal for whether an invitee later
   changed it, so the script resets **every** verified `invited_users` row (unverified rows are
   handled at verification). Steps:

   1. Send the notice first: affected invitees will have to use "Forgot password" once.
   2. Boot the new auth-service (adds `password_reset_required_at`).
   3. Dry run, which only prints the count:
      `pnpm --filter auth-service reset:inviter-passwords -- --dry-run`
   4. Run it: `pnpm --filter auth-service reset:inviter-passwords`. Optional
      `-- --verified-before=<ISO date>` skips rows verified after the verify-time fix shipped.
   5. It sets an unusable random hash, sets `password_reset_required_at`, and revokes the
      rows' refresh tokens (access tokens expire on their own, 15 minutes). Re-running skips
      marked rows. Login for a marked row returns 401 "Password reset required"; completing
      forgot-password clears the marker. It never runs automatically.

   After the flag is on, `upsertUser` ensures the personal org on each GitHub login (same
   idempotent, no-steal logic), so new signups need no backfill. A failure there is logged
   (`Failed to ensure personal org at login`) and does not block sign-in; the next login retries.

   `InvitedUser` rows get no org on purpose: creating an infra requires `super_admin`, invited
   roles are clamped below `super_admin` (`clampInvitedRole`), and only GitHub `User` rows are
   created as `super_admin`, so an invited user never owns an infra.

   **Verification, run in each Django mirror (infrastructure-service and application-service
   databases).** Both must return zero rows:

   ```sql
   -- infra rows with no organization_id, or whose org / owner membership is not mirrored
   SELECT i.id, i.user_id, i.organization_id,
          (o.id IS NOT NULL) AS has_org, (m.id IS NOT NULL) AS has_owner_membership
   FROM api_infrastructure i
   LEFT JOIN api_organization o ON o.id = i.organization_id
   LEFT JOIN api_membership m
          ON m.organization_id = o.id
         AND m.account_id = o.owner_account_id
         AND m.role = 'owner'
         AND m.removed_at IS NULL
   WHERE i.organization_id IS NULL OR o.id IS NULL OR m.id IS NULL;
   ```

   Users the backfill skipped for having no linked Account show up here as missing orgs; that
   is expected until they are linked. To cross-check the auth-service side (needs the
   `uuid-ossp` extension for `uuid_generate_v5`):

   ```sql
   SELECT u.id FROM users u
   LEFT JOIN organizations o ON o.id = uuid_generate_v5('4c900569-d11e-46e2-b34c-cd677a57ca73', u.id::text)
   WHERE u.account_id IS NOT NULL AND o.id IS NULL;
   ```

6. **Permission-matrix enforcement (ticket 7) rollout.** With `ORGS_ENABLED` on, access in
   infrastructure-service, application-service and payment-service comes only from an active
   Membership in the resource's org; the legacy `invited_users` M2M and
   `InfrastructureUserRole` grant nothing. Before flipping the flag:
   - **Personal orgs must exist first** — see note 5.
   - **Run the Django migrations first.** `0041` (infrastructure-service), `0045`
     (application-service) and `0008` (payment-service) re-stamp `organization_id` on rows
     created after the first backfill. A NULL `organization_id` is inaccessible under the flag.
   - **Deploy before the flag.** New `infrastructure.created` events carry `organization_id`;
     the application-service and payment-service mirrors store it write-once.
   - **Old tokens resolve to no role.** A token with no `account_id` claim gets no role
     (review M1), so every session minted before F9 must re-login or refresh.
   - Open SSE log streams authorize once, at open. A member removed mid-stream keeps the
     stream for at most `LOG_STREAM_MAX_DURATION_SECONDS` (10 minutes by default).
   - **The personal-org backfill grants only `owner`.** Legacy `InfrastructureUserRole`
     invitees and `invited_users` members are not folded into the owner's personal org.
     Membership is per org, so folding them in would widen a per-infra grant to every infra
     that owner has. Each invitee needs an explicit re-invite into the org. Until then they
     have no access with the flag on.
   - **Personal org ids are computable by anyone** (`uuid5(ORG_NAMESPACE, user_id)`). No
     endpoint may ever accept a client-chosen org id; org scope comes only from the signed
     token claim and the stored resource column.
   - **Turning the flag back off after flag-on creates is unsupported.** With the flag on,
     an org admin can create an infrastructure and `Infrastructure.user` is that admin.
     The flag-off checks treat `user` as the owner, which would give the admin owner-only
     powers and lock out the real owner. infrastructure-service records
     `created_by_non_owner_under_orgs` at create time and refuses to start with the flag off
     while any row has it set. Recover by turning the flag back on, or by reassigning those
     rows by hand.
   - **Re-run the idempotent backfill after the full rollout, immediately before the
     flip** (migrations `0041`, `0045`, `0008` re-stamp NULL rows; rerun them, or the
     equivalent update, once every service is on the new code).
   - **Policy refresh callback (closed in ticket 8).** With the flag on, the per-user
     script API key records the Account named by the issuing session's signed `account_id`
     claim (`ScriptApiKey.account_id`, migration `0043`). The callback links an infra only
     when that Account holds the ACTIVE owner Membership in the infra's org, read from the
     mirror at request time (no cache). The creator no longer counts; an owner who did not
     create the infra is accepted. Anything else, including an unknown infra id, is a
     uniform 403 that is still audited: a `PolicyRefreshEvent` is written with no infra link and
     `refused_infrastructure_id` set, the key's `last_used_at` is updated, and
     `policy_version` is never advanced. Keys issued before `0043`, or with the flag off, carry no Account and are
     refused under the flag: the owner re-runs the dashboard's *Refresh policy* snippet,
     which mints a new key. Flag off is unchanged (creator match).
   - **Role staleness bounds.** A removed member loses access within about 60s (cache TTL)
     when the Django `org.events` consumers are healthy. If a consumer is stalled the
     mirror keeps the old Membership, and access lasts until it recovers (their token
     stays valid up to `JWT_EXPIRES_IN`, 7 days). Open SSE streams authorize once and last
     at most `LOG_STREAM_MAX_DURATION_SECONDS`.
   - **Trust boundary.** The mirrors store `organization_id` write-once, first writer wins,
     so anyone holding AMQP publish credentials is inside the trust boundary for org
     derivation.

7. **Ownership transfer (ticket 8).** `POST /api/organizations/{id}/transfer-ownership`
   `{new_owner_account_id}`, behind `ORGS_ENABLED` (404 when off).
   - Owner only (verified credential, claim equals the row's account, ACTIVE owner
     Membership; re-checked under row locks inside the transaction). The token's
     `auth_time` must be within `EXIT_EXPORT_REAUTH_MAX_AGE_SECONDS` (default 600, same name
     and value as infrastructure-service; keep them equal) or the call is a 401 with
     `code: reauth_required`, which the dashboard already turns into a re-login.
   - One transaction: lock the Organization row, then the owner and target Memberships;
     demote the owner to admin, then promote the target (the partial unique owner index is
     checked per statement); update `Organization.owner_account_id`; two audit rows; revoke
     the old owner's refresh tokens. The old owner must sign in again.
   - Events go through a transactional outbox (`org_event_outbox`, created by `sync()`;
     the pending-row index is applied in `applyPendingSchemaPatches`). Every publish site
     (create org, add/revive/change/remove member, invitation accept, transfer) inserts its
     row in the SAME transaction as the state change, so a rollback writes no event and a
     crash after commit loses none. A dispatcher loop in the service takes a Postgres
     advisory lock (one dispatcher cluster-wide), publishes pending rows in `seq` order on a
     confirm channel, and marks them sent only after the broker confirmed the batch and no
     channel loss or disconnected publish happened during it; otherwise rows stay pending
     and it retries with exponential backoff (500ms up to 30s). Delivery is at-least-once;
     the mirrors drop a repeated or older version. Sent rows are kept (no pruning yet).
   - The transfer writes the demote row first, then the promote row. The promote also
     carries `previous_owner_account_id` and `previous_owner_version`. Mirrors apply an OWNER
     role atomically with demoting the named previous owner (stamping that version, so the
     separate demote event is a no-op) and, defensively, demoting every other active owner by
     role only. So a lost or delayed demote event cannot leave two owners; the worst
     transient state is a briefly missing owner (owner-only actions fail closed). The
     mirrors also derive `Organization.owner_account_id` from the one active owner row.
     Caveat: an owner event for an old owner with a higher version than their row (only
     possible from a forged or wrongly republished event) flips ownership back; the last
     applied owner wins.
   - A personal organization (id = `uuid5(ORG_NAMESPACE, users.id)` for a user linked to
     the owner's Account) is never transferable: 409. Its id is derivable, Django stamps
     the legacy owner's infrastructures into it, and every GitHub login re-runs
     `ensurePersonalOrganization`, which would report `conflict` forever. This also keeps
     `backfill:personal-orgs --republish-existing` safe: it only touches personal orgs, whose
     owner cannot change, and it already refuses (and publishes nothing for) an org whose
     owner column differs from the user's Account.
   - Access bound after demotion: auth-service endpoints see the new role at once. Django
     resolves from the mirror, so the old owner's live access token loses owner rights after
     consumer lag plus at most the 60s role-cache TTL (eviction runs on the event). The
     token itself stays valid up to `JWT_EXPIRES_IN` (7d in `env.example`); only the role
     changes. Revoking refresh tokens stops new tokens from being minted.
   - Rolling `ORGS_ENABLED` back off after a transfer is unsupported: the creator would
     regain legacy owner powers and the new owner would have none. infrastructure-service
     marks every infra in a transferred org (`created_by_non_owner_under_orgs`) when its
     org.events consumer sees the owner move, and the existing startup guard then refuses to
     boot with the flag off. The creator-to-Account mapping is not available in Django, so
     the mark is per org, not per creator. Check each mirror for orgs with other than one
     active owner:

     ```sql
     SELECT organization_id, count(*) FROM api_membership
     WHERE role = 'owner' AND removed_at IS NULL
     GROUP BY organization_id HAVING count(*) <> 1;
     ```

## Open questions

Everything else above is decided. These two genuinely need the owner, not because they're
technically hard but because they're business/compliance calls this plan shouldn't make
unilaterally:

1. **Stripe customer consolidation.** F9 adds `Billing.organization_id` and stops at "one
   Stripe customer per org going forward" — it does **not** merge any already-created
   per-user Stripe customer into an org-level one. Does the owner want existing customers
   migrated/merged (and if so, on what timeline, and does Stripe's own customer-merge
   tooling get used or does this get modeled as a fresh org-level customer with the old one
   archived)? This is a live-payment-data decision, not an engineering one.
2. **Per-org paid-tier limit overrides.** F7's own open question 6 ("should per-infra build
   concurrency/rate limits be configurable per customer, e.g. a paid tier gets higher
   concurrency") is now squarely an org-level question once `organization_id` exists
   everywhere. Is a single global default acceptable for F9's launch, or does pricing/tiering
   need to land in the same release? Affects whether `customer_call_budget`'s org-aware sibling
   takes a per-org override column now or later — a product-pricing call, not a technical one.
