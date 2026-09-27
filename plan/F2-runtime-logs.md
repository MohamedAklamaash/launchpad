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
5. **Cursor:** a signed cursor (`django.core.signing`, `RUNTIME_LOGS_CURSOR_SECRET`) over
   `{app_id, user_id, container, start, end, aws_token}`, 15-minute max age, ≤4KB — never a
   raw CloudWatch `nextToken`. Bound to the app/user/container that requested it, so a
   cursor can't be replayed across apps, users, or containers, or used to widen the window
   past what the original request was authorized for. EKS has no cursor in v1 (pods/logs
   are read fresh each call, capped at 5 pods / 500 lines / 512KiB each).
6. **Rate limit: dedicated `runtime_logs` F0 bucket**, fail-closed on Redis down (503), no
   gateway exemption — each call cost an AssumeRole (or `{cluster}-deploy` chain) plus a
   CloudWatch/k8s call in the customer's own account.
7. **Timeouts:** a dedicated `Config(connect_timeout=2, read_timeout=4, max_attempts=2)`
   distinct from `aws.session.BOTO3_CONFIG`, plus a `time.monotonic()` deadline (~6s) checked
   before every additional AWS/k8s call in the same request — bounding total latency under
   the gateway's 10s proxy timeout even with the (2+4)×2 calls the ECS path can make.
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

## Files

- `deployment-services/application-service/api/services/runtime_logs_service.py` — ECS/EKS
  tail logic, stream/pod derivation, deadline, bounds.
- `deployment-services/application-service/api/services/runtime_logs_cursor.py` — signed
  cursor encode/decode.
- `deployment-services/application-service/api/services/runtime_logs_audit.py` — DB row +
  structured log line.
- `deployment-services/application-service/api/models/runtime_log_access.py` +
  `api/migrations/0029_runtime_log_access.py` — the audit table.
- `deployment-services/application-service/api/views/runtime_logs.py` — the endpoint
  (strict params, budget, error mapping, no-store headers).
- `deployment-services/application-service/api/common/naming.py` — `ecs_log_group` helper
  (appended).
- `deployment-services/application-service/api/urls.py` — one appended route.
- `deployment-services/application-service/core/settings.py`, `env.example`,
  `test_settings.py` — Redis settings, the runtime-logs budget/cursor-secret settings, the
  DEBUG guard, logger pins.
- `deployment-services/application-service/api/mock/mock_session.py`,
  `api/mock/mock_k8s.py` — `list_tasks`/`filter_log_events`/`read_namespaced_pod_log` mocks,
  a seeded (GitGuardian-safe, non-AWS-shaped) fake secret line.
- `gateway-service/app/api/endpoints/application.py` — one appended route, `app_id` typed
  as `uuid.UUID`.
- `gateway-service/main.py` — dropped `details: str(exc)` from the global 500 handler.
- `launchpad-frontend/components/runtime-logs-panel.tsx` (new) — the log panel: container
  toggle, window select, cursor-based "load more", opt-in ≤1/10s auto-refresh that stops on
  a hidden tab or after 10 minutes, plain-text rendering (no `dangerouslySetInnerHTML`), no
  persistence.
- `launchpad-frontend/app/dashboard/applications/[id]/page.tsx` — one import, one `isOwner`
  const, one JSX insertion point.
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
