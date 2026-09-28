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

**Status: done** (`fix/h4-resource-names`).

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

**Decisions**

1. **Discriminator is per-row (infra id + application id), not per-infra.** The item
   above says "full-id hash" and "per-infra discriminator"; both `target_group_name`
   and `new_ecs_log_group` (`application-service/api/common/naming.py`) hash the
   infrastructure id *and* the application id together
   (`hashlib.sha256(f"{infra_id}:{app_id}")[:8]`, `Database.module_name()`'s pattern —
   full value hashed first, only the digest sliced). An infra-only discriminator would
   still collide on the delete-then-recreate case: `application_cleanup_service.
   _delete_target_group` retries `ResourceInUseException` six times and then gives up,
   so a deleted app's target group can be left behind, still carrying that app's own
   `launchpad:infra`/`launchpad:app` tags; recreating an app of the same name on the
   same infra would then reproduce both the exact same name *and* the exact same
   expected tags, so the ownership check below would wrongly accept the orphan as
   "ours." Binding the hash to the application id as well as the infrastructure id
   means a recreated app (new id) never lands on its predecessor's name at all.
2. **Target group name truncation never touches the hash.** The old formula truncated
   the whole `f"{slug}-{infra_id[:8]}-tg"` string to ALB's 32-char limit, which could
   already cut into the discriminator (or the `-tg` marker) for a long app name —
   a second, independent bug two long-named apps on *different* infras could hit even
   before H4. `target_group_name` reserves the `-{8-hex-hash}-tg` suffix first and
   truncates only the slug to what's left (20 chars). It also re-restricts the slug to
   `[a-z0-9-]`: `app_slug` still admits `.`/`_` for Docker tags, which ALB target group
   names reject.
3. **Never adopt a target group that isn't ours — checked on both AWS response
   shapes.** `ALBClient.create_target_group` compares the existing target group's
   `launchpad:infra`/`launchpad:app` tags (`describe_tags`) against what this call
   would have set, and raises `TargetGroupOwnershipMismatch` on any mismatch or missing
   tag, before returning an adopted ARN. This runs after `DuplicateTargetGroupNameException`
   (AWS's differing-settings case) *and* after a plain create success (AWS's
   same-name/matching-settings case, which returns the pre-existing target group's ARN
   with no exception at all — the literal scenario this item names). A freshly created
   target group trivially passes (we just set those tags in the same call); the check
   only ever rejects an adoption. One extra `DescribeTags` call, paid once per new
   target group's lifetime, not per deploy.
4. **Existing apps keep their stored `target_group_arn` — no rename.**
   `_create_target_group`'s existing reuse-by-stored-ARN branch (same VPC → `describe_target_groups`
   by ARN, `modify_target_group` for the health check, return) is untouched; the new
   hashed name is only ever computed when that branch falls through (no stored ARN, or
   the stored one is gone/wrong-VPC). A currently-running app's target group is never
   renamed or re-created by this change.
5. **ECS log group: same per-row hash, persisted once, read everywhere through one
   function.** `Application.log_group_name` (additive migration, nullable) is set the
   first time `_create_task_definition` runs for a row with no stored name: to
   `new_ecs_log_group(application)` (the hashed name) if the row has never *completed*
   a deploy, or to the *legacy* `ecs_log_group(slug)` string if it has (a pre-H4 app
   whose task definition/log group already exist under the shared name) — either way
   the result is persisted, so every row converges onto `log_group_name` after its very
   next deploy without ever renaming a live resource (the legacy branch persists the
   exact string it already used). Every later deploy/rollback/backfill of that row
   reuses the stored value instead of recomputing it. Every reader —
   `runtime_logs_service._tail_ecs`, `application_cleanup_service._delete_log_group`,
   `exit_inventory._task_definition_json` — now calls one function,
   `ecs_log_group_for(application)` (`application.log_group_name` or the legacy
   fallback), instead of each re-deriving a name independently (the F2 pre-review's own
   RECOMMENDED-but-not-done item). `ECSClient.create_task_definition` takes the
   resolved name as an explicit `log_group=` kwarg, defaulting to `f'/ecs/{family}'`
   only when omitted, so no other caller's behavior changed.
   - "Has completed a deploy before" is answered from `Deployment` history
     (`_has_succeeded_before`: any row with `status=SUCCEEDED`), not
     `application.task_definition_arn` — an earlier draft of this decision used that
     field and was wrong: `ApplicationRetryDeployView` (`api/views/application.py`)
     resets `task_definition_arn` (and `service_arn`/`target_group_arn`/
     `listener_rule_arn`/`runtime_refs`) to give a failed deploy a clean slate before
     re-queuing, on a **live** `Application` row it does not delete. A pre-H4 app that
     succeeded once, later fails an unrelated redeploy, and gets retried through that
     view would otherwise read as "never deployed" and mint a second, hashed log group
     out from under its own history — caught in review, fixed before this shipped, and
     covered by `test_retry_after_failure_does_not_switch_a_previously_deployed_apps_log_group`.
   - `_rollback_ecs`'s own invariant ("no `Application` field is written until every
     AWS call has already succeeded") is not violated by persisting `log_group_name`
     inside `_create_task_definition` before that: the value written is deterministic
     from `(application, has-succeeded-before)` at call time, identical whether or not
     the rollback ultimately succeeds, and the cpu/memory/port fields that invariant
     actually protects are restored separately, after, on failure — see
     `rollback_application`'s `except` block.
6. **F2's B1 stream binding is unaffected and still does the harder half of the
   isolation work.** `test_two_infras_same_account_same_app_name_read_only_own_streams`
   (unchanged) still proves that even two infras sharing a log group can't read each
   other's task logs, because the actual read is scoped to `logStreamNames` derived
   from `list_tasks` on *this app's own* ECS service. H4's log-group split means a
   post-H4 deploy stops sharing a group at all; pre-H4 rows still rely on B1 alone
   until their next deploy.
7. **No IAM change required.** `policy.json`'s `elasticloadbalancing:*` statement
   already covers `DescribeTags`; there's no new policy version and no
   `NEXT_PUBLIC_LAUNCHPAD_SCRIPT_REF` bump for this PR.
8. **Remaining `[:8]` sites reviewed, not changed (confirmed non-uniqueness-keys):**
   - `infrastructure-service/api/common/naming.py:environment_name` and
     `shared/aws/app_security_group.py:app_security_group_name` both concatenate a
     `[:8]` UUID prefix with a *separate* full-id hash suffix (`unique_suffix`/`suffix`
     — `hashlib.md5(str(id)).hexdigest()[:8]`, never a slice of the id itself); the hash
     is what actually disambiguates two ids sharing a prefix, so the prefix is a
     cosmetic label, not a uniqueness key. Comments added at both call sites.
   - `eks_bootstrap.py:_ingress_group_name`'s `str(infra.id)[:8]` fallback already
     carries an extensive docstring (added when `dns_label` superseded it) explaining
     it's dead in practice — every infra gets a `dns_label` — and is kept only so a
     bootstrap call never raises for the theoretical row that somehow lacks one. Left
     untouched; renaming it would require the same "don't move a live ALB group name"
     care H4 already applies to target groups and log groups, for a code path that
     shouldn't be reachable at all.
   - Application-service migration `0005_infrastructure_name.py`'s `str(infra.id)[:8]`
     backfills a purely cosmetic `Infrastructure.name` display field on this service's
     own read-model copy (no unique constraint, never an AWS resource name). It's also
     a historical, already-applied data migration — not rewritten in place; a comment
     was added explaining why, rather than changing migration history.
   - `infrastructure-service/api/services/cost_service.py`'s `_mock_costs` was already
     correct before this PR: its seeds hash the full `infra.id`/`app.id` and only slice
     the digest, with a comment already citing CLAUDE.md.
   - Grep for every other truncated-id pattern (Redis/cache/lock keys, cluster names,
     bucket names) found nothing else. The `worker_id`/`lock_owner` values built from
     `uuid.uuid4().hex[:8]` (`run_worker.py`, `backfill_host_routing.py`,
     `application_service.py`) are random UUIDv4s, not UUIDv7 — no embedded timestamp,
     not forceable from any row's `created_at` — so they're out of scope for this item.
   - One further residual, deliberately left alone: `ECSClient.create_task_definition`'s
     `family` is still `{slug}-task` (shared across infras sharing a slug — not a
     security boundary, since every ARN Launchpad stores carries the specific
     revision). (`ALBClient.create_target_group`'s wrong-VPC fallback — originally
     noted here as a second residual, `f"{name[:24]}-{int(time.time()) % 10000}"` — was
     fixed in the same-day review pass below; see decision 11.)

**Tests** — `application-service/api/tests/test_h4_resource_naming.py` (new): two
infra ids sharing the old `[:8]` prefix get distinct target-group and log-group names;
the discriminator survives slug truncation at the 32-char ALB limit; a foreign/adopted
target group (tag mismatch) raises on both the `DuplicateTargetGroupNameException` path
and the plain-success/matching-settings path (a hand-rolled fake models the latter,
since `api/mock/mock_session.py`'s shared mock only reproduces the former); a
same-app/same-tags re-adoption still succeeds (self-healing); an app with a stored
target-group ARN never calls `create_target_group` at all; a brand-new app's first
deploy persists the hashed log group and passes it to `ECSClient.create_task_definition`;
a pre-H4 app (a `Deployment` row with `status=SUCCEEDED` already exists, no stored name)
keeps the legacy log group even across an `ApplicationRetryDeployView`-style
`task_definition_arn` reset; a row with an already-stored name reuses it unchanged.
`test_runtime_logs.py` gained one test proving the runtime-logs tail reads
`Application.log_group_name` when present. `test_cost_tagging.py`'s `_FakeALBClient`
gained a `describe_tags` so it still models a real elbv2 client now that
`create_target_group` always checks tags when given any. Final state: 454
application-service tests pass (433 pre-existing + 21 new/modified across this file and
the two edits above), 880 infrastructure-service tests pass unchanged (H4 touched no
infra-service test), 74 gateway-service tests pass unchanged (H4 touched no
gateway-service code or test — the count includes H5's unrelated additions, merged in by
this branch's rebase onto `origin/main`).

**REAL-AWS-VALIDATION:** a new `## H4 truncated-UUID resource names` section was added
to `plan/REAL-AWS-VALIDATION.md` — not yet run against a real account. Summary: which
AWS response shape `CreateTargetGroup` actually returns for matching vs. differing
settings on an existing name (this decides which branch the ownership check fires on),
`DescribeTags` read-after-write consistency right after `CreateTargetGroup(Tags=...)`,
and that a brand-new app's first real deploy lands on the hashed log group while a
pre-H4 app's redeploy keeps its existing one.

**Independent security review — fixes applied (second pass, same branch):**

9. **R1 (REQUIRED): pre-F3 apps with no Deployment history.** Migration 0029 created
   the `Deployment` table with no backfill, and `_record_deployment` is best-effort (a
   history row failing to write never fails the deploy it describes) — so an app
   deployed before that table existed, or whose own history row silently failed to
   write, can have neither a `Deployment` row nor `log_group_name`. Decision 5's
   `_has_succeeded_before` originally checked Deployment history only, which would have
   read such a row as "never deployed" on its very next deploy and minted a brand-new
   hashed log group, orphaning its actual runtime log history. Fixed three ways,
   deliberately redundant, in priority order: (a) `_has_succeeded_before` checks
   `deployment_url` first — set only by a fully successful deploy
   (`deploy_application`'s step 9) and never reset by `ApplicationRetryDeployView`
   (that view resets `status`/`error_message`/`service_arn`/`task_definition_arn`/
   `target_group_arn`/`listener_rule_arn`/`runtime_refs`, not this field and not
   `build_id`); (b) `task_definition_arn`/`service_arn` next, covering an app whose
   first deploy attempt got that far but hasn't gone through a retry-reset yet; (c)
   Deployment history last, as the final backstop. A new migration,
   `0035_backfill_legacy_log_group_name.py`, persists `log_group_name =
   f"/ecs/{slug}-task"` for every existing `Application` matching any of those same
   three signals — the slug regex is duplicated inline in the migration rather than
   imported from `api.common.naming`, since a migration must not depend on application
   code that can change independently of its own, fixed historical meaning.
   - **Why `deployment_url` and not just (b)+(c):** an earlier version of this decision
     claimed persisting `log_group_name` via migration made `_has_succeeded_before`'s
     exact signal not matter once the migration had run — that was wrong. A retry
     queued in the window between `ApplicationRetryDeployView` resetting the ARN
     fields and a worker actually picking up that retry's job (migration timing is
     irrelevant here — this is a race between the reset and the *worker*, not the
     migration) would leave a pre-F3, zero-Deployment-row app with neither ARN field
     nor a `Deployment` row, and `_create_task_definition` would still mint a hashed
     name in that exact instant. `deployment_url`, set at the app's original success
     and untouched by the retry view, closes this without depending on timing at all —
     caught and fixed in a second review pass on this same decision.
   - Tests:
     `test_pre_f3_app_with_task_definition_arn_but_no_deployment_history_keeps_legacy_log_group`
     (no `Deployment` rows, `task_definition_arn` set → legacy name),
     `test_retry_queued_before_migration_runs_still_keeps_legacy_log_group_via_deployment_url`
     (neither ARN field nor any `Deployment` row, only `deployment_url` → legacy name —
     the exact race above), and `test_migration_0035_backfills_only_rows_with_deploy_evidence`
     (calls the migration's `RunPython` function directly against real models — this
     codebase's test `conftest.py` builds the schema from model state and never
     actually runs migrations, so this is the only way to exercise it under the
     existing test setup; it verifies the migration's logic, not its compatibility
     with a future model schema change, since it's handed live models rather than
     migration-historical ones).
   - **Migration numbering:** `feat/h1-envs-encryption` also adds an
     `application-service` migration `0034` (`0034_encrypt_application_envs.py`,
     unrelated name), off the same `0033_custom_domain_route` parent. Neither branch was
     merged into `main` as of this review, so this branch's own `0034`/`0035` were left
     as-is; whichever of H1/H4 merges second renumbers its migration(s) and repoints
     `dependencies` at rebase time.
10. **C1: `launchpad:app-id` on new target groups.** `app_tags()`'s two keys
    (`launchpad:infra`, `launchpad:app`) are both name-shaped — a delete-then-recreate
    of an app under the same name on the same infra would tag its new target group
    identically to its predecessor's on that axis alone. `target_group_name()`'s
    per-row hash already makes the two land on different names in practice, so this
    was defense-in-depth, not a live gap: `aws/tags.py:target_group_tags()` adds
    `launchpad:app-id` (the full `Application.id`) on top of `app_tags()`, and
    `_create_target_group` passes it as the ownership check's expected tags. Existing
    target groups (reused via the stored-ARN path) are unaffected and not re-tagged —
    that path still doesn't call the ownership check at all, unchanged from before this
    review; re-verifying a *stored* ARN's ownership is a larger, separate change than
    this item's scope.
11. **C2: deterministic wrong-VPC fallback name.** The old
    `f"{name[:24]}-{int(time.time()) % 10000}"` truncated `name`'s own hash
    discriminator to 3 characters and derived the replacement from wall-clock time.
    `ALBClient._vpc_discriminated_name` hashes the identifying tags
    (`launchpad:infra`/`launchpad:app-id`, falling back to `name` itself when no tags
    were given) together with `vpc_id` — the actual differing input — so the same
    inputs always produce the same output, and reserves its own `-{hash}-tg` suffix
    the same way `target_group_name` does. The misleading "residual" comment from
    decision 8 (this fallback "still truncates its hash to 3 chars") is now stale by
    construction and was rewritten in `aws/alb.py` at the call site.
12. **C3: retry `DescribeTags` before failing.** `_verify_target_group_ownership` now
    retries up to twice (3 attempts total) with a short backoff (0.1s, 0.2s) before
    raising `TargetGroupOwnershipMismatch`, in case tag propagation lags
    `CreateTargetGroup(Tags=...)` by a moment on a target group this same call just
    created. Whether real AWS actually has such a lag is unconfirmed — added to
    `plan/REAL-AWS-VALIDATION.md`.
13. **C4: actionable, disclosure-safe mismatch message.** `TargetGroupOwnershipMismatch`
    now names the target group and its ARN and tells the customer what to do ("delete
    that target group yourself, or rename this application... then redeploy"). This
    reaches `Application.error_message` verbatim (`sanitize_deploy_error` passes a
    plain `RuntimeError`'s `str()` through unchanged) — safe to show in full because
    it's the customer's own AWS account and their own target group being named back to
    them.

Tests added across this review round (two passes — R1's `deployment_url` fix landed in
a follow-up to the same review): 11 new (31 total in `test_h4_resource_naming.py`, up
from 20), covering R1 (including the retry/migration-timing race), C1, C2
(determinism + length/charset), and C3 (retry-then-succeed, retry-then-exhaust)
directly, plus C4 (message content, survives `sanitize_deploy_error`).
`test_cost_tagging.py`'s `test_create_target_group_call_site_passes_app_tags` now
asserts `app_tags()`'s two keys are a *subset* of what's passed, not the whole dict,
since target groups now carry a third key. Final state: 465 application-service tests
pass, 880 infrastructure-service tests and 84 gateway-service tests pass unchanged
(gateway's count reflects H3's own additions from the rebase onto the now-updated
`origin/main`, not anything H4 touched). `ruff`, `compileall`,
`iam_policy/generate.py --check`, and `makemigrations api --check --dry-run` (against
Postgres connection settings with no live Postgres — "No changes detected" is reported
before the connection is attempted) all clean.

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
