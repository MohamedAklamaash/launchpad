# Plan

Status of the platform roadmap. Every per-feature file's factual claims were re-verified
against `main` at `9c8743d`; `ROADMAP.md` is the original document and its claims were not.

## Status

| | Feature | Status | Blocked by |
|---|---|---|---|
| — | Phase 0 prework | **done**, except the rate-limit carve-out | a design decision — see below |
| [F1](F1b-tls-activation.md) | TLS, custom domains | Phase 1 **done** (#68), activation **not started** | 3 decisions + hosted zone |
| [F2](F2-runtime-logs.md) | Logs | provisioning **done** (#70, #71); runtime **not started** | nothing technical |
| [F3](F3-rollback.md) | Rollback | prerequisites **done** (#69); rollback **not started** | nothing |
| [F4](F4-cost-tagging.md) | Cost attribution | **not started** | nothing |
| [F5](F5-evidence-pack.md) | Compliance pack | generator **done** (#67, #72); pack **not started** | nothing |
| [F6](F6-exit-export.md) | Exit export | **not started** | F3, F5 |

## Recommended order

1. **[F4 cost tagging](F4-cost-tagging.md)** — the only item that gets *worse* by waiting.
   Tags are not retroactive, so every deploy before this ships is permanently unattributable.
2. **[F3 rollback](F3-rollback.md)** — prerequisites in, pure software, no external dependency.
3. **[F5 evidence pack](F5-evidence-pack.md)** — the hard half is built; the drift diff is small.
4. **[F1b TLS activation](F1b-tls-activation.md)** — start the NS delegation now regardless;
   that part is wall-clock, not work.
5. **[F2 runtime logs](F2-runtime-logs.md)** — highest remaining risk, lowest urgency.
6. **[F6 exit export](F6-exit-export.md)** — depends on F3 and F5.

## Two rules, both learned the hard way

**Re-verify every factual claim against `main` before starting a phase.** `ROADMAP.md` was
written against the unmerged EKS branch and described a codebase that did not exist for the
entire implementation run. Every line number in it is wrong; the truncation gap was four
sites not three; the immutable image tag was never built rather than built-and-ignored;
`container_config.py` did not exist; reconcile-apply had already been built by the
managed-database work; the `policy_version` delivery mechanism already existed.

**Run a security pre-review on each slice before implementing it.** It returned BLOCK twice
on F2 alone, and the second one was a live credential leak — a wrapped ElastiCache token
that survived into Postgres, Redis and an HTTP-served field because a whitespace-collapse
defeated the boundary assertions meant to catch it. No denylist would have caught it. Each
feature file states whether a pre-review is required and why.

## What the roadmap missed entirely

Three live leaks it never mentions, all found by *executing* the plan rather than reading
it, all now fixed:

- `Database.error_message` — raw terraform stderr served over HTTP (#70).
- `Application.error_message` — raw exception rendered in the dashboard (#73).
- `logger.error` / `logger.exception` — raw stderr and the platform IAM ARN into the
  platform log stream (#70).

That is the argument for continuing with this plan: it is a good map for a security review
to walk, not a specification to type in.

## Standing release actions

- [ ] **Bump `NEXT_PUBLIC_LAUNCHPAD_SCRIPT_REF`.** Policy is at **v2**; until the pinned ref
      moves, the dashboard's refresh snippet serves the v1 script, reports v1, and every
      existing customer is flagged stale **and cannot clear it**. F4 will bump the policy to
      v3 and require this again.
- [ ] **Run `manage.py redact_stored_provisioning_text --dry-run`, then for real, before
      enabling the provisioning-logs endpoint.** Lossy by design, no reverse.
- [ ] Dismiss the GitGuardian incidents on #65 and #73 — both flagged AWS's published
      documentation credentials in test fixtures, since removed.

## Known issues on `main`, not owned by any feature

- The `databases` rate-limit exemptions rest on the same wrong-layer reasoning rejected for
  the logs endpoint: the gateway does not verify JWTs, so "owner-only" is enforced two hops
  downstream and cannot justify an exemption.
- A malformed UUID in `<str:infra_id>` still returns 500 on the databases routes. Django's
  `ValidationError` is not a `ValueError`, so it escapes the error mapper. The logs endpoint
  pre-validates; those routes do not.
- **Phase 0's rate-limit carve-out cannot be built as specified.** The gateway does not
  verify JWTs so it cannot distinguish authenticated traffic; `EXEMPT_PATHS` is exact-match
  so an ID-bearing path can never match; and exempt means *zero* limit on endpoints that
  each trigger an AssumeRole in the customer's account. Needs a per-user budget enforced
  where identity is actually verified, keeping the IP limit as the outer bound.
