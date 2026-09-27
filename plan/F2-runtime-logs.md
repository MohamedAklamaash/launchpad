# F2 — Runtime log tailing

**Status:** done (mock-verified; see [REAL-AWS-VALIDATION.md](REAL-AWS-VALIDATION.md#f2-runtime-logs))
**Depends on:** nothing · **Blocked by:** nothing technical

Highest risk of the remaining features, lowest urgency. It is the first feature where
Launchpad reads **customer application output** — request payloads, PII, their end-users'
data. Deliberately left until last.

## Goal

Tail a running application's logs in the dashboard, on demand, storing nothing on the
platform.

## Verified on main at `9c8743d`

**Already done by the provisioning half (#70, #71):**

- `redact_provisioning_text` — the allowlist redactor, at write time, before truncation.
- `sanitize_deploy_error` — type-dispatched sanitisation for exception text (#73).
- The owner-only endpoint pattern: UUID pre-validation, two-step authz (404 stranger / 403
  invited), `_error_response` mapping, access logging, **no rate-limit exemption**.
- Read-time re-redaction with drift detection, meaningful because stored values are
  redactor fixed points.

**Not built:** anything runtime. No CloudWatch client, no `filter_log_events`, no
`read_namespaced_pod_log`.

**Known facts for the design:**

- ECS log group is `/ecs/{slug}-task`, created in
  `application_deployment_service._create_task_definition`.
- Task definitions already ship both containers' logs to CloudWatch, and the policy already
  grants `logs:*` — **no new IAM needed**.
- EKS is merged now, so `read_namespaced_pod_log` genuinely applies (it did not when the
  roadmap was written).

## Design

On-demand proxy tail. Nothing stored on the platform — storing customer log data would
undercut the whole BYOC pitch.

- **ECS:** `filter_log_events` on `/ecs/{slug}-task` through the existing assumed-role
  session.
- **EKS:** `read_namespaced_pod_log` through the existing k8s client.

**The redaction posture is an open decision, not a default.** These are the customer's own
application logs being shown back to the customer. `redact_provisioning_text` is an
allowlist built for terraform output and would withhold essentially all of an application's
log lines — the same over-aggression reversed twice during #70. Reaching for it by reflex
would ship a feature that displays nothing. Decide this in the pre-review.

## H5 requirements — these are the design, not additions

- **No-store path**, explicitly excluded from any error-reporting integration. Corrected by
  the pre-review: the live `str(exc)` sink for this feature is
  `application-service`'s own house pattern (`Response({"error": str(e)}, 500)`, used
  throughout `api/views/application.py`), not the gateway — a failure mid-stream there
  puts customer log content into an error response and into platform logs. The new view
  never uses that pattern; every branch maps to a fixed message. (The gateway's own
  `main.py:78` `details: str(exc)` was still worth dropping as defense in depth, and is,
  but it was never the primary risk here.)
- **Log group and namespace derived server-side from the authenticated app record, never
  from a request parameter.**
- Bounded limit and window.
- An access log of who tailed what.
- A written authorization decision for invited ADMINs. The provisioning endpoint settled on
  owner-only; runtime logs are arguably more sensitive, not less.
- An explicit data-handling statement in customer-facing docs.
- **No rate-limit exemption.** The gateway does not verify JWTs, so exemption is decided
  before anything authenticates — and each request costs an AssumeRole plus a CloudWatch or
  k8s API call *in the customer's account*, billing them and consuming their API throttle.

The gateway `proxy_request` timeout is 10s, which rules out streaming/SSE through it.

## Decisions

1. **Redaction posture:** the customer's own logs are shown unredacted to the owner — the
   allowlist redactor would display nothing. Hardening goes into the *boundaries* instead:
   never stored, never logged, never in an error body. Confirmed by the pre-review.
2. **Invited ADMINs:** refused. Owner-only — `get_user_role(infra, user_id) == SUPER_ADMIN`,
   which also refuses an invited ADMIN who happens to be the application's own creator
   (`Application.user` is the creator, not necessarily the infra owner).
3. **Host: `application-service`**, not infrastructure-service — it owns `Application`,
   `Environment`, the ECS/EKS deploy clients, and the mock session/k8s seams this feature
   reads through. The original file's "whichever owns the app record" was resolved here;
   `infrastructure-service` would have needed a second cross-service lookup for no benefit.
4. **B1 (pre-review BLOCK): stream binding, not log-group derivation.** `/ecs/{slug}-task`
   is account+region scoped, not app scoped — a slug is unique only per infra, and two
   infras (possibly two owners) can share an AWS account. Deriving the log group from the
   app record alone is *necessary* but not *sufficient* isolation. The fix: every read is
   additionally bound to this application's own ECS service (`list_tasks` on
   `environment.cluster_arn` for `{slug}-service`, RUNNING and STOPPED) or EKS namespace,
   and the resulting `logStreamNames`/pod names are the only ones ever read — never derived
   from a request parameter, and further clamped so no ECS window can begin before the
   `Application` row's own `created_at` (the backstop for a deleted-then-recreated app of
   the same name, whose STOPPED task can still appear in `list_tasks` for up to ~1h).
5. **Cursor:** a signed — not encrypted — cursor (`django.core.signing`,
   `RUNTIME_LOGS_CURSOR_SECRET`, `fallback_keys=[]` so `settings.SECRET_KEY_FALLBACKS`, a
   different secret domain entirely, is never a second valid signing key) over
   `{app_id, user_id, container, start, end, aws_token}`, 15-minute max age, ≤4KB — never a
   raw CloudWatch `nextToken`. Bound to the app/user/container that requested it, so a
   cursor can't be replayed across apps, users, or containers, or used to widen the window
   past what the original request was authorized for. The payload is base64/JSON and
   readable by anyone holding the cursor — that's fine, since it discloses nothing beyond
   what the request that produced it already gave that same client. EKS has no cursor in
   v1 (pods/logs are read fresh each call, capped at 5 pods / 500 lines / 512KiB each).
6. **Rate limit: dedicated `runtime_logs` F0 bucket**, fail-closed on Redis down (503), no
   gateway exemption — each call cost an AssumeRole (or `{cluster}-deploy` chain) plus a
   CloudWatch/k8s call in the customer's own account. Checked before query-param
   validation, not after: a flood of malformed requests still writes an audit row per
   attempt, so it has to be budgeted too, or that alone could grow the audit table without
   bound.
7. **Timeouts, enforced end-to-end, not just at the CloudWatch/ECS client.** A
   `_budget_config(deadline)` sized to whatever's actually left of the request's ~6s
   deadline — not a fixed `Config(connect_timeout=2, read_timeout=4, max_attempts=2)` — is
   threaded through every AWS/k8s client on this path: the initial `AssumeRole`
   (`create_boto3_session`'s new `config=` param; that call is synchronous and would
   otherwise inherit `aws.session.BOTO3_CONFIG`'s 60s read timeout / adaptive retries), the
   EKS `describe_cluster` and `{cluster}-deploy` `AssumeRole` (`k8s_apis`/`EKSClient`/
   `assume_deploy_role`'s new `config=` param, default `None` so every other caller is
   unaffected), and `list_namespaced_pod` (added the `_request_timeout=(2,4)` that
   `read_namespaced_pod_log` already had). Below one read-timeout's worth of budget,
   retries are dropped to 1 rather than shrunk further. `ConnectTimeoutError`/
   `ReadTimeoutError` are now caught alongside `ClientError` and map to `502 code=Timeout`,
   not an uncaught exception falling through to a generic 500.
8. **Audit: both a DB row (`RuntimeLogAccess`) and a structured `audit.runtime_logs` log
   line**, written for every outcome including denials, metadata only — never log content,
   the cursor value, or a stream/pod name.
9. **DEBUG guard + logger pins added to `application-service/core/settings.py`**: the
   service now refuses to boot with `DEBUG=True` outside `MODE=dev` (a technical 500 page
   would render this endpoint's local variables, including log content, on any unhandled
   exception), and `botocore`/`urllib3`/`kubernetes` loggers are pinned to `WARNING`.
10. **Log-group rename (RECOMMENDED, not done here):** the pre-review's non-blocking
    suggestion to fold a per-infra discriminator (e.g. `dns_label`) into the log group name
    for new deploys, so two infras never share a log group at all. Left as a separate
    change — it touches `application_deployment_service._create_task_definition` and
    `application_cleanup_service._delete_log_group`, both of which currently re-derive
    `/ecs/{slug}-task` independently of the new `api/common/naming.ecs_log_group` helper
    added here (which only the new runtime-logs code uses so far).
11. **"Not deployed" (409) vs. "no tasks yet" (200, empty).** These are different states.
    409 means the application has no `service_arn` (ECS) / no `runtime_refs.namespace`
    (EKS), or its environment isn't `ACTIVE` with a `cluster_arn` — there is nothing to
    read logs from. Once deployed, a deploy in progress or a sleeping app can legitimately
    have zero running or recently-stopped tasks; that's a normal 200 with `events: []`, not
    an error — an owner should be able to confirm "no logs yet" without a false failure.
12. **Query strings appear in the platform's own access logs** (standard web-server/WSGI
    behavior) — acceptable here only because this endpoint has no search/filter text
    parameter (`container`/`minutes`/`previous` are small enums/bounded integers, `cursor`
    is opaque and short-lived). This is a constraint on any future parameter, not a gap: a
    free-text search parameter would need to move off the query string before it could be
    added.
13. **Control characters and terminal escapes are stripped server-side, not just
    byte-capped.** ANSI cursor/color escapes and the C0/DEL control range are removed;
    tab and newline are kept (the frontend renders `whitespace-pre-wrap`). The Unicode
    bidi embedding/override/isolate characters (U+202A–U+202E, U+2066–U+2069) used in
    "Trojan Source"-style spoofing — making a line *read* differently than its byte order,
    e.g. hiding or disguising part of it — are replaced with U+FFFD. This is a
    rendering-safety measure, distinct from the redaction posture: the content itself
    still reaches the owner unredacted, just not in a form that can lie about its own
    order in the browser.
14. **A platform misconfiguration is never a 400.** `create_boto3_session` and `k8s_apis`
    each raise a bare `ValueError` for two cases that have nothing to do with what the
    client sent: the infra's `is_mock` flag disagreeing with `MODE` (a deploy
    misconfiguration — now checked ahead of time via `aws.session.gate_mismatch` and
    mapped to `503`), and a real infra that never finished AWS onboarding (`infra.code`
    empty — mapped to the same `409` as "not deployed", since there's equally nothing to
    tail). Neither is a request the client can fix by changing a parameter.
15. **RUNNING tasks always win a log-stream slot; STOPPED tasks fill the rest
    newest-first.** `list_tasks`' STOPPED ordering isn't documented as recency-sorted, so
    `_ecs_task_ids` now runs a `describe_tasks` on the STOPPED set and sorts by
    `stoppedAt` before taking however many slots remain under `MAX_ECS_TASKS` — one more
    AWS call, spent only when RUNNING didn't already fill every slot.
16. **The frontend gates the runtime-logs panel on per-infra ownership, not the JWT's
    global role.** `isOwner` used to read `user.role === 'super_admin'` — the same
    approximation the pre-existing `canEdit` still uses — which is wrong in both
    directions: an infra owner isn't necessarily a platform super_admin, and a platform
    super_admin viewing someone else's infra isn't its owner. The app detail page now
    fetches the infrastructure (`infrastructureApi.get(app.infrastructure_id)`) and
    compares `infra.user_id` to the signed-in user; the backend's own `SUPER_ADMIN`-only
    check is authoritative regardless, so this only decides whether to show the panel at
    all. The same fetch's `compute_type` now also hides the "Previous instance" (EKS-only)
    checkbox on ECS applications, where the backend rejects it with 400.

## Files

- `deployment-services/application-service/api/services/runtime_logs_service.py` — ECS/EKS
  tail logic, stream/pod derivation, deadline, bounds.
- `deployment-services/application-service/api/services/runtime_logs_cursor.py` — signed
  cursor encode/decode.
- `deployment-services/application-service/api/services/runtime_logs_audit.py` — DB row +
  structured log line.
- `deployment-services/application-service/api/models/runtime_log_access.py` +
  `api/migrations/0031_runtime_log_access.py` — the audit table.
- `deployment-services/application-service/api/views/runtime_logs.py` — the endpoint
  (strict params, budget, error mapping, no-store headers).
- `deployment-services/application-service/api/common/naming.py` — `ecs_log_group` helper
  (appended).
- `deployment-services/application-service/api/urls.py` — one appended route.
- `deployment-services/application-service/core/settings.py`, `env.example`,
  `test_settings.py` — Redis settings, the runtime-logs budget/cursor-secret settings, the
  DEBUG guard, logger pins.
- `deployment-services/application-service/api/mock/mock_session.py`,
  `api/mock/mock_k8s.py` — `list_tasks`/`describe_tasks`/`filter_log_events`/
  `read_namespaced_pod_log` mocks, a seeded (GitGuardian-safe, non-AWS-shaped) fake secret
  line.
- `deployment-services/application-service/aws/session.py` — `create_boto3_session`/
  `_assume_role_raw`/`_build_real_session` take an optional `config=` (default `None`,
  every other caller unaffected) and a new `gate_mismatch(infrastructure)` helper.
- `deployment-services/application-service/aws/eks.py` — `assume_deploy_role`/`EKSClient`
  take the same optional `config=`.
- `deployment-services/application-service/api/k8s/deployer.py` — `k8s_apis` takes the
  same optional `config=` and threads it into `EKSClient`/`assume_deploy_role`.
- `gateway-service/app/api/endpoints/application.py` — one appended route, `app_id` typed
  as `uuid.UUID`.
- `gateway-service/main.py` — dropped `details: str(exc)` from the global 500 handler;
  `expose_headers=["Retry-After", "X-Request-Id"]` on CORS.
- `launchpad-frontend/components/runtime-logs-panel.tsx` (new) — the log panel: container
  toggle, window select, cursor-based "load more", opt-in ≤1/10s auto-refresh that stops on
  a hidden tab or after 10 minutes, plain-text rendering (no `dangerouslySetInnerHTML`), no
  persistence; `computeType` prop hides the EKS-only "Previous instance" control on ECS.
- `launchpad-frontend/app/dashboard/applications/[id]/page.tsx` — one import, one infra
  fetch effect, `isOwner` keyed on `infra.user_id`, one JSX insertion point.
- `launchpad-frontend/lib/api/applications.ts`, `launchpad-frontend/types/application.ts` —
  the `logs()` client call and its request/response types.
- `docs/RUNTIME_LOGS.md` (new) + a link from `docs/USER_ONBOARDING_GUIDE.md` — the
  customer-facing data-handling statement.

## Tests

The 11 pre-review-ranked tests, in
`deployment-services/application-service/api/tests/test_runtime_logs.py` (plus a small
gateway test for the non-exempt route and the typed `app_id`): stranger 404 / invited ADMIN
incl. creator 403 / owner 200 · cross-infra same-account same-app-name isolation (asserted
on `logStreamNames`) and the created-at window clamp · unknown/invalid params rejected
before any AWS/k8s mock call · a forced exception with a seeded secret in its message never
reaches the response body or logs · cursor tamper/cross-app/cross-user/cross-container/
expired/oversized all collapse to one 400 · no platform setting reaches a container's env
(ECS task def and EKS manifest) · window/message/total-byte clamps · an audit row on both
success and denial with no content fields · budget 429 and Redis-down 503 · the seeded fake
secret returned unredacted with `Cache-Control: no-store` · a real infra refused in dev
mode.

## Security pre-review

Done — see the pre-review notes folded into the Decisions above. The one BLOCK (B1) is
resolved by the stream-binding fix; every REQUIRED item is implemented. The one RECOMMENDED
item not done (the log-group rename) is called out above as a separate future change.

## Out of scope

Storing or indexing logs. Streaming/SSE. Log search.
