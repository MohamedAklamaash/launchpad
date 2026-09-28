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
| `POST /auth/forgot-password` | **CRITICAL, found during H3 review.** Returned `{ otp }` in the response body unconditionally (the gateway's `ForgotPasswordResponse` model claimed "dev-only" but `PasswordService.requestPasswordReset` never actually gated the echo on `NODE_ENV`) — the same code, the OTP, authenticates via `authenticate-with-otp`/`verify-reset-otp`, so this was a full account-takeover-by-email primitive in every environment including production. Also enumerated accounts: 404 for an unknown email, 200+OTP for a known one. | `requestPasswordReset` never returns the OTP (removed from the return value entirely, not just from the controller) and never throws for an unknown email or an account with no infra — it silently does nothing. `ForgotPassword` always responds `202 { message }` with the same generic body, and doesn't wait for `requestPasswordReset` to finish (fire-and-forget, errors logged server-side only) — so response time doesn't distinguish a known email (DB write + queue enqueue) from an unknown one (nothing). |
| `GET/POST /auth/authenticate-with-otp`, `POST /auth/verify-reset-otp` | **Found during the same review.** No cap on failed OTP guesses against a 6-digit code (1,000,000 values) — same secret class as the forgot-password leak above, just reached by brute force instead of by reading the response. `authenticate-with-otp` was GET-only, so the OTP sat in the URL (access logs, browser history, proxies) even for the dashboard's own manual-entry form. `generateOTP` used `Math.random()`, not a CSPRNG. | Added a Redis-backed attempt cap (`utils/otp-attempts.ts`, keyed per email+purpose, 5 attempts, TTL matches the OTP's own 10-minute expiry — same Redis instance already wired for BullMQ, no new infra): the 6th attempt invalidates the outstanding OTP and 429s, forcing a fresh request. Added `POST /auth/authenticate-with-otp` (body, not query) and switched the frontend's `verifyOtp` to it; kept the GET route because the verification *email itself* links to it (`notification-service`'s `user-event.consumer.ts` builds `.../authenticate-with-otp?email=...&otp=...` for a clickable link, which has to be a GET) — documented as an accepted trade-off inherent to email magic links, bounded by the new attempt cap and the existing 10-minute expiry. `generateOTP` switched to `crypto.randomInt` (CSPRNG) instead of `Math.random()`. Reviewed but not changed: both endpoints still 404 "User not found" before checking the OTP at all, which is a smaller enumeration channel than forgot-password's (it required already knowing/guessing an OTP-shaped value to reach); left as a follow-up rather than expanding this PR further. Reviewed but not changed: the OTP compare is a Postgres `WHERE otp = ...` equality, not an app-level string compare — not textbook constant-time, but with the 5-attempt cap in place, timing-channel exploitation of a 6-digit space isn't practically feasible, and rewriting the lookup to compare in JS with `crypto.timingSafeEqual` risked breaking support for a user with multiple simultaneous pending OTPs (one per pending infra invite); documented as a judgment call, not fixed. |
| `POST /auth/register` (its dev-only `otp` echo) | Reviewed — same "return the OTP" shape as forgot-password, but already gated: `RegisterInvitedUser` only sets `body.otp` when `env.NODE_ENV !== 'production'` (see `local-e2e-multitenant-harness.md` memory — this is the local/e2e convenience the harness relies on). The frontend (`inviteUser`) never reads the field even though its type allows it. | No change — production never sees it, and this is a deliberate different case, not an oversight. |
| Every call site that verifies an access token and treats it as an active session (`RegisterInvitedUser`, `ListInvitedUsers`, `RemoveMemberFromOrg`, `UpdatePassword`, `RevokeRefreshToken`'s `resolveRevokeCallerId`, `GetCurrentUser`'s `getUserFromToken`, and the new `resolveCaller` in user-service/notification-service) | **Found during the same review.** `verifyAccessToken` alone only checks signature and expiry — it doesn't reject a token minted for a narrower purpose. `PasswordService.verifyResetOTP` mints a 5-minute token with `scope: 'password_reset'` that still carries the user's real `role`, so a leaked/observed reset token could reach `superAdminMiddleware`-gated actions (invite/remove members), list the holder's invited users, revoke their sessions, or (via the new H3 code) read their profile/notifications — none of which "I just proved I own this email" should grant. | Added `verifySessionToken` (auth-service `utils/handle-token.ts`): wraps `verifyAccessToken` and additionally rejects any payload with a truthy `scope`. Every call site above now uses it (or the equivalent check added to `resolveCaller` in user-service/notification-service) instead of the raw `verifyAccessToken`/`jwt.verify`. `resetPassword` itself is unchanged — it's the one call site that *requires* `scope === 'password_reset'`, the opposite check. |
| `POST /auth/login`, `/reset-password`, `/refresh`, GitHub OAuth routes, health/docs routes | Reviewed — legitimately unauthenticated (pre-session flows, proven by password/reset-token/refresh-token possession instead) or exempt (health, docs, favicon). | No change. |

Gateway changes were route/model shape only — `gateway-service/app/api/endpoints/user.py`
(new `UserSearchResult` model for the minimal search response), `notification.py` (route
renamed to `/me`, dropping the `user_id` path param this file's own H5 write-up
documented — H5's decisions above are now updated to match), and `auth.py`
(`UpdatePasswordBody` drops `email`, `ForgotPasswordResponse` drops `otp` in favor of a
generic `message`, new `AuthenticateOtpBody` for the POST OTP route). The gateway itself
still does not verify JWTs; enforcement lives entirely in the services that own the data,
consistent with `CLAUDE.md`. `user.py`'s remaining `/{user_id}` path param keeps H5's
`Path(pattern=...)` constraint.

Frontend: `launchpad-frontend/lib/api/auth.ts` — `verifyOtp` switched from GET (query
params) to POST (body); `forgotPassword`'s doc comment updated to describe the new
generic-response behavior (its own code was already fire-and-forget from the frontend's
perspective, so no functional change there). Every other changed route
(`/users`, `/users?q=`, `/notifications/user`, `/notifications/me`, `update-password`)
still has zero frontend callers (re-verified by grep), so nothing else to update there.

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
- One path param stays `str`, deliberately not `uuid.UUID`, because the id it names is
  **not** a UUID-typed column upstream: `user.py`'s `/users/{user_id}` (user-service
  `User.user_id` is `DataTypes.STRING`,
  `identity-services/services/user-service/src/db/models/user.model.ts`). In practice
  it's always populated with auth-service's UUID at event time, but the schema doesn't
  guarantee that, so hard-typing `uuid.UUID` would be a claim the model doesn't back. It
  instead takes `Path(pattern=r"^[A-Za-z0-9_-]{1,128}$")`, which forbids `/ ? # %` and
  dot-segments while staying open to any future non-UUID id shape.
  **Updated by H3:** `notification.py`'s `/notifications/user/{user_id}` — the other
  non-UUID id at the time this was written — no longer exists. H3 replaced it with
  `GET /notifications/me`, which derives the id from the caller's verified token instead
  of a path param, so there's nothing left there to type or pattern-constrain.
- `proxy_request` itself wasn't changed: every path segment it now receives is either a
  `uuid.UUID` (whose `str()` form is fixed and safe) or a `Path(pattern=...)`-validated
  string, so the URLs endpoints hand it are safe by construction. A generic quoting helper
  would be redundant given that invariant, and the new regression test
  (`test_path_param_ids_are_constrained.py::test_every_path_param_is_uuid_or_pattern_constrained`)
  enforces the invariant for any future route.
- No frontend changes needed — every id the frontend sends is passed through as an opaque
  string taken from a prior API response's `id` field, and none of the newly-UUID-typed
  routes are called with anything else (confirmed by reading `launchpad-frontend/lib/api/*`
  and its call sites). The `str`-typed `/api/users/{user_id}` and
  `/api/webhooks/github/{app_id}` aren't called by the frontend at all (H3 later gave
  `/api/notifications/user/{user_id}` a frontend-visible replacement — see H3).

## H6 — Small items

- EKS: unmatched `:443` traffic gets 503 (empty-backend Service) rather than a fixed 404.
- Custom domains: an operator path to force-disable a domain (abuse/takedown).
- Databases: a tighter per-user write bucket for create/delete (F0 follow-up).
- dns_writer Redis ACL + RabbitMQ user wired into `infra/.docker` for local dev (documented
  in `docs/PLATFORM_DNS_ISOLATION.md`, not automated).
