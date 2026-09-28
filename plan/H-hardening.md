# H — Hardening follow-ups

**Status:** not started · Found while building and reviewing F0–F6 and F1b; none was
owned by any feature. Each item is one PR, built mock-first, with an independent security
review before merge — same process as the features.

## H1 — Encrypt `Application.envs` at rest

`Application.envs` (`application-service/api/models/application.py`) is a plaintext
`JSONField`. F3 (H7) and F6 (H6) were designed around that rather than fixing it.

- Envelope-free field-level encryption with a platform key (`cryptography` Fernet via
  `MultiFernet` so keys rotate without a flag day); key from env, required outside dev.
- Data migration encrypting existing rows; reads accept only ciphertext after migration.
- Every reader of `envs` goes through the model — the deploy path (task definition, k8s
  manifest), rollback, exit inventory, the API (which already returns values to the owner
  only) — so no new plaintext copy appears anywhere.
- Rotation command re-encrypting under the newest key.

## H2 — Enforce exit in application-service

F6 Decision 14: after *Complete exit* infrastructure-service refuses reprovision/config
changes, but application-service still deploys and creates apps — its read-model never
learns `exited_at`. Publish an `infrastructure.exited` event (or extend the readiness
contract), mirror `exited_at`, refuse deploy / rollback / app create / webhook deploys /
custom-domain attach with a clear error.

## H3 — Unauthenticated identity endpoints

**Status: done** (`fix/h3-identity-authz`).

user-service `GET /users/:userId` and `GET /users?q=` (`user.controller.ts`) have no
auth; the gateway exposes search publicly (`gateway-service/app/api/endpoints/user.py`),
so anyone can enumerate users by email. The notification route
`/notifications/user/{user_id}` passes a caller-chosen user id. Audit every
identity-service and notification-service route for the same pattern (id from
path/body, no verified caller). Fix: derive the caller from a verified token; scope
lookups to what the caller may see.

**Decisions**

Root cause: `user-service` and `notification-service` both already carried `JWT_SECRET`
in their env schema (`config/env.ts`) and already used it to gate the Swagger docs
(`middleware/docs-auth.middleware.ts`) — but nothing verified a token on the actual data
routes. Per `CLAUDE.md`, the gateway does not verify JWTs and passes `Authorization`
through unchanged, so once a route reached the gateway with no auth dependency of its
own, it was reachable by anyone. Each service now verifies its own caller locally
(`src/utils/resolve-caller.ts`, one per service — not lifted into `@launchpad/common`,
since that would add `jsonwebtoken` to the shared package's dependency graph and touch
the root lockfile for no reuse benefit; every other JWT check in this codebase, e.g.
`docs-auth.middleware.ts`, is already duplicated per service the same way).

Audit table — every identity-service/notification-service route, before vs. after:

| Route | Before | After |
|---|---|---|
| `GET /users/:userId` (user-service) | No auth; any caller could fetch any user's full profile by id. | Requires a verified token. 403 unless `userId === token.sub` — no admin carve-out (member lists already exist via auth-service's `/invited-users`, scoped to infras the caller owns). |
| `GET /users?q=` (user-service) | No auth; any caller could enumerate users by name/email substring, full profile returned. | Requires a verified token and `q.trim().length >= 3`. Results are scoped to users who share at least one infra with the caller (fetches the caller's own row by `sub`, intersects `infra_id`) — role alone (e.g. "any super_admin") isn't sufficient since every infra creator is a super_admin of their own tenant. Returns only `user_id`, `user_name`, `email`, `profile_url` (never `infra_id`, `role`, `invited_by`, `metadata`). If the caller's own record hasn't replicated yet, returns `[]` rather than erroring. No product feature calls this today (verified against `launchpad-frontend/lib/api/*` and every other service — Django's `application_service.py` constructs a `user_client` but never calls it), so this is forward-looking scoping for an eventual invite-by-search flow, not a fix for a broken caller. |
| `GET /notifications/user/:userId` (notification-service) | No auth; `userId` was fully caller-chosen — anyone could read any user's notifications by guessing/incrementing an id. | Route replaced with `GET /notifications/me` (service and gateway) — no target-user parameter at all; the id comes only from the verified token's `sub`. Nothing called the old shape (same grep), so there was no compatibility reason to keep a path param and compare it to `sub` instead. |
| `POST /auth/update-password` (auth-service) | Not itself in the original ask, but same bug class: `email` came from the request body with no token check at all, despite Swagger claiming `bearerAuth`. `oldPassword` gated *which* account's password changed but not *whose* — any caller who knew (or brute-forced) a valid `email` + `oldPassword` pair could act on it without ever authenticating as that user. | Requires a verified token; the account acted on is `token.sub` (looked up via `InvitedUser.findByPk`, same pattern as the existing token-based `resetPassword`). `email` is no longer accepted in the body at all. `oldPassword` is still required as proof of current-credential possession. |
| `POST /auth/register`, `GET/DELETE /auth/invited-users*`, `POST /auth/revoke` (auth-service) | Reviewed — already correct. | No change. These already verify the token inline per-controller and derive the caller/target scope from it (`RegisterInvitedUser`/`RemoveMemberFromOrg` via `superAdminMiddleware` + infra ownership; `RevokeRefreshToken` via `resolveRevokeCallerId`, fixed in PR #85). This is the pattern the H3 fixes above were matched to. |
| `POST /auth/forgot-password` | Returns `{ otp }` in the response body; the gateway's `ForgotPasswordResponse` model claims "dev-only" but `PasswordService.requestPasswordReset` doesn't gate the echo on `NODE_ENV`. | **Not fixed here** — no caller-identity spoofing involved (it's a pre-auth flow by design, matching `reset-password`/`verify-reset-otp`), so it's out of H3's scope. Flagged as a separate follow-up: gate the `otp` echo behind `NODE_ENV !== 'production'`, matching `RegisterInvitedUser`'s existing pattern for its OTP. |
| `POST /auth/login`, `/forgot-password`, `/verify-reset-otp`, `/reset-password`, `/refresh`, GitHub OAuth routes, health/docs routes | Reviewed — legitimately unauthenticated (pre-session flows, proven by OTP/reset-token/refresh-token possession instead) or exempt (health, docs, favicon). | No change. |

Gateway changes were route/model shape only — `gateway-service/app/api/endpoints/user.py`
(new `UserSearchResult` model for the minimal search response), `notification.py` (route
renamed to `/me`, dropping the `user_id` path param this file's own H5 write-up
documented — see H5's decisions above, now stale on that one point), and `auth.py`
(`UpdatePasswordBody` drops `email`). The gateway itself still does not verify JWTs;
enforcement lives entirely in the services that own the data, consistent with
`CLAUDE.md`. `user.py`'s remaining `/{user_id}` path param keeps H5's
`Path(pattern=...)` constraint.

No frontend changes: grepped `launchpad-frontend` for every changed route
(`/users`, `/users?q=`, `/notifications/user`, `/notifications/me`, `update-password`)
and found zero callers, so nothing to update there and no compatibility constraint on
the route/body shape changes above.

CI gap found and fixed in passing: `user-service`'s `test` script was
`echo "No tests yet"` and wasn't run in `.github/workflows/ci.yml` at all. Added real
tests and a `Unit tests (user-service)` CI step (`check-identity` job) alongside the
existing `auth-service`/`notification-service` steps.

## H4 — Truncated-UUID resource names

`CLAUDE.md` forbids UUIDv7 prefixes as uniqueness keys.

- **Target groups** — `f"{slug}-{infrastructure_id[:8]}-tg"`
  (`application_deployment_service.py`). Two infras in one AWS account created within ~65s
  with the same app slug get the same name, and `CreateTargetGroup` on an existing name
  with the same settings **returns the existing group** — app B registers into app A's
  target group. Use a full-id hash (the `Database.module_name()` pattern); existing TGs keep
  their stored ARN, new ones get the safe name.
- **ECS log groups** — `/ecs/{slug}-task` is shared by same-name apps across infras in one
  account (F2 pre-review). Add a per-infra discriminator for new deploys; runtime-logs and
  cleanup read the stored name.
- Review the remaining `[:8]` sites (`naming.environment_name`,
  `app_security_group_name`, `eks_bootstrap` legacy fallback) — they carry a full-id hash
  suffix, so they are not uniqueness keys; confirm and document rather than rename live
  resources.

## H5 — Gateway path ids typed as UUID

**Status: done** (`fix/h5-gateway-uuid-params`).

Only routes added in F0–F6/F1b take `uuid.UUID`; 59 older path params are `str`, which lets
`%3F`/`%23` change the upstream URL (confused deputy — upstream authz still applies).
Type them all; a malformed id returns 422 at the edge.

**Decisions**

- Every id-shaped path param across `auth.py`, `infrastructure.py`, `database.py`,
  `application.py` (including the `/webhooks/github/{app_id}` route) is now `uuid.UUID`,
  verified against the owning model: `Infrastructure.id`, `Application.id`,
  `Deployment.id`, `Database.id` (all Django `UUIDField`s, confirmed via
  `deployment-services/*/api/models/*.py` and `<uuid:...>` URL converters in
  `application-service/api/urls.py`), and `InvitedUser.id` in auth-service
  (`identity-services/services/auth-service/src/db/models/invited-user.model.ts`,
  `DataTypes.UUID`). `custom_domain.py` was already fully typed and untouched.
- Two path params stay `str`, deliberately not `uuid.UUID`, because the id they name is
  **not** a UUID-typed column upstream: `user.py`'s `/users/{user_id}` (user-service
  `User.user_id` is `DataTypes.STRING`,
  `identity-services/services/user-service/src/db/models/user.model.ts`) and
  `notification.py`'s `/notifications/user/{user_id}` (notification-service stores
  `user_id` as an untyped Mongo string field). In practice both are always populated with
  auth-service's UUID at event time, but the schema doesn't guarantee that, so hard-typing
  `uuid.UUID` would be a claim the model doesn't back. Both instead take
  `Path(pattern=r"^[A-Za-z0-9_-]{1,128}$")`, which forbids `/ ? # %` and dot-segments while
  staying open to any future non-UUID id shape.
- `proxy_request` itself wasn't changed: every path segment it now receives is either a
  `uuid.UUID` (whose `str()` form is fixed and safe) or a `Path(pattern=...)`-validated
  string, so the URLs endpoints hand it are safe by construction. A generic quoting helper
  would be redundant given that invariant, and the new regression test
  (`test_path_param_ids_are_constrained.py::test_every_path_param_is_uuid_or_pattern_constrained`)
  enforces the invariant for any future route.
- No frontend changes needed — every id the frontend sends is passed through as an opaque
  string taken from a prior API response's `id` field, and none of the newly-UUID-typed
  routes are called with anything else (confirmed by reading `launchpad-frontend/lib/api/*`
  and its call sites). The two `str`-typed routes (`/api/users/{user_id}`,
  `/api/notifications/user/{user_id}`) and `/api/webhooks/github/{app_id}` aren't called by
  the frontend at all.

## H6 — Small items

- EKS: unmatched `:443` traffic gets 503 (empty-backend Service) rather than a fixed 404.
- Custom domains: an operator path to force-disable a domain (abuse/takedown).
- Databases: a tighter per-user write bucket for create/delete (F0 follow-up).
- dns_writer Redis ACL + RabbitMQ user wired into `infra/.docker` for local dev (documented
  in `docs/PLATFORM_DNS_ISOLATION.md`, not automated).
