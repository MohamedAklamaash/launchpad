# F0 — Per-user budget for customer-account calls, plus the two unowned bugs

**Status:** done (mock-verified) · **Depends on:** nothing · **Blocked by:** nothing

F2, F4, F5 and F6 each add an endpoint whose every request costs an AssumeRole plus at
least one API call **in the customer's account** — billed to them, consuming their API
throttle. Every one of those plans says "no rate-limit exemption", which is necessary but
not sufficient: the only limit today is the gateway's per-IP window, and a single
authenticated user behind one IP (or many IPs) is bounded by nothing that knows who they
are. This lands first so the four endpoints after it have something to call.

## Verified on main

- The gateway does not verify JWTs (`CLAUDE.md`), so it cannot key a limit on identity.
- `gateway-service/app/core/rate_limiter.py` — per-IP fixed window in Redis
  (`RATE_LIMIT_WINDOW_SECONDS`, default 300). The roadmap's security review notes it
  **fails open** on Redis errors.
- The `databases` rate-limit exemptions rest on "owner-only", which is enforced two hops
  downstream and so cannot justify an exemption at the gateway.
- A malformed UUID in `<str:infra_id>` on the databases routes returns 500: Django's
  `ValidationError` is not a `ValueError`, so it escapes the error mapper. The logs endpoint
  pre-validates; these routes do not.

## Design

**A per-user budget enforced where identity is verified** — in the Django services, after
`shared/middleware/authentication.py` has resolved the user. A small shared helper in
`deployment-services/shared/` (`customer_call_budget(user_id, bucket, limit, window)`),
Redis-backed, applied as a decorator to endpoints that call into a customer account.
Buckets are per feature (`costs`, `evidence`, `runtime_logs`, `exit_export`, `databases`)
so one feature cannot starve another. Over budget → 429 with `Retry-After`.

**Fail closed for these buckets.** If Redis is unavailable the call is refused with 503.
These endpoints cost the customer money; an unavailable limiter is not a reason to make
them free.

**The per-IP gateway limit stays** as the outer bound. The `databases` exemptions are
removed from the gateway and replaced by the per-user budget.

**UUID pre-validation** on the databases routes, matching the logs endpoint: malformed →
404, never 500.

## Tests

Budget: under limit passes, over limit 429 with `Retry-After`, buckets independent,
Redis down → 503 (not allowed through). Databases: malformed UUID → 404 on every route.
Gateway: the exemptions are gone.

## Security pre-review

Not required — it narrows access, adds no data path.

## Decisions

- **Only the `databases` bucket is wired up.** The plan names five buckets (`costs`,
  `evidence`, `runtime_logs`, `exit_export`, `databases`), but only `databases` has an
  endpoint today — the other four belong to F2/F4/F5/F6, none of which have shipped.
  `shared/ratelimit/budget.py` (`customer_call_budget(user_id, bucket, limit, window)`,
  the `rate_limited(bucket, limit, window)` DRF decorator) is bucket-agnostic and takes
  no settings names, so each later feature wires its own bucket/limit/window at its own
  call site without touching this module.
- **Defaults: 60 requests / 60s per user for `databases`**, covering every method on
  both routes (list/get/create/delete) under one bucket, not a finer per-method split.
  `RATE_BUDGET_DATABASES_LIMIT` / `RATE_BUDGET_DATABASES_WINDOW_SECONDS`, env-overridable
  in `deployment-services/infrastructure-service/{core/settings.py,env.example}`.
- **Fixed window, keyed `budget:{bucket}:{user_id}`**, same INCR+TTL/EXPIRE pattern as
  the gateway's per-IP limiter and `api/services/infra_queue.py`'s dedup lock, reusing
  the same `REDIS_HOST/PORT/PASSWORD/DB` the service already has — no new Redis DB index.
- **Fail-closed is a caught exception, not the DRF exception-handler chain.**
  `customer_call_budget` raises `HttpError(status_code=503)` on a Redis error; the
  `rate_limited` decorator catches it locally and returns `{"error": ...}` itself,
  matching how `database.py`/`provisioning_logs.py` already handle their own errors
  (they never rely on `settings.REST_FRAMEWORK["EXCEPTION_HANDLER"]` either — every
  `HttpError` in this codebase is caught by hand). This also means the decorator behaves
  identically under `test_settings.py`, which doesn't configure that handler at all.
- **No `fakeredis` dependency added.** Not already a dependency anywhere in the repo;
  tests monkeypatch `shared.ratelimit.budget._redis`, matching the existing convention
  of monkeypatching the bound Redis-touching name (`InfraQueue` in the database/logs
  tests) rather than faking the wire protocol.

## Known risks, not fixed here

- **Gateway per-IP window is now the only outer bound on GET database status polling**
  again, at whatever `MAX_USER_REQUESTS`/`RATE_LIMIT_WINDOW_SECONDS` the gateway is
  configured with (default 10 req / 300s per IP across all of `/api`, not just
  databases). The exemption existed because a dashboard polling database status can
  exceed that. Whoever operates the gateway needs a realistic value here, or the
  frontend's poll interval needs to stay well under it.
- **One `databases` bucket covers both cheap GETs and the AssumeRole-plus-IAM-simulate
  POST.** 60 creates/min into a customer account is generous for what should be a rare
  operation; a tighter write-only bucket is a reasonable follow-up once usage data
  exists, not designed in blind.
- **The fail-closed path can hold a request for the pool's `socket_timeout` (5s) before
  returning 503** if Redis is unreachable rather than down outright (matches the
  connection pool tuning `infra_queue.py` already uses).
- **Same orphaned-key edge as the gateway's limiter**: a crash between `INCR` and
  `EXPIRE` leaves a budget key with no TTL, undercounting that user's remaining budget
  until it's manually cleared. The gateway has carried this same gap since before F0;
  not introduced here, not fixed here.
