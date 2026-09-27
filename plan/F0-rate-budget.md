# F0 — Per-user budget for customer-account calls, plus the two unowned bugs

**Status:** not started · **Depends on:** nothing · **Blocked by:** nothing

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
