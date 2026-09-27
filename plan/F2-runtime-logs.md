# F2 — Runtime log tailing

**Status:** not started (the provisioning half shipped in #70/#71)
**Depends on:** nothing · **Blocked by:** nothing technical — see the pre-review note

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

- **No-store path**, explicitly excluded from any error-reporting integration. The gateway
  returns `str(exc)` in 500 bodies, so a failure mid-stream can put customer log content
  into an error response and into platform logs.
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

## Files

`infrastructure-service` or `application-service` runtime-logs service (whichever owns the
app record) · new owner-scoped endpoint + gateway route · frontend log panel · customer
docs data-handling statement.

## Tests

Cross-tenant 404/403 · log group derived from the record, not the request (assert a
crafted parameter is ignored) · bounded window · access log written · no-store assertion ·
a seeded secret in a log line behaves per whatever redaction posture is chosen.

## Security pre-review

**Required, before any implementation.** This is a new data-handling boundary, not a new
endpoint on an existing one. The pre-review's job is to settle the redaction posture and
the invited-ADMIN decision — both of which change what gets built.

## Out of scope

Storing or indexing logs. Streaming/SSE. Log search.
