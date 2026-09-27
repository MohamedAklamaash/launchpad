# Plan

Status of the platform roadmap. Every per-feature file's factual claims were re-verified
against `main` at `9c8743d`; `ROADMAP.md` is the original document and its claims were not.

## Status

| | Feature | Status | Blocked by |
|---|---|---|---|
| — | Phase 0 prework | **done**; the rate-limit carve-out is replaced by F0 | — |
| [F0](F0-rate-budget.md) | Per-user budget for customer-account calls + two unowned bugs | **not started** | nothing |
| [F1](F1b-tls-activation.md) | TLS, custom domains | Phase 1 **done** (#68); decisions + zone terraform **done** (#75); DNS writer + activation **not started** | zone not yet applied (real AWS) |
| [F2](F2-runtime-logs.md) | Logs | provisioning **done** (#70, #71); runtime **not started** | nothing technical |
| [F3](F3-rollback.md) | Rollback | prerequisites **done** (#69); rollback **not started** | nothing |
| [F4](F4-cost-tagging.md) | Cost attribution | **not started** | nothing |
| [F5](F5-evidence-pack.md) | Compliance pack | generator **done** (#67, #72); pack **not started** | nothing |
| [F6](F6-exit-export.md) | Exit export | **not started** | F3, F5, F1b (teardown) |

## Recommended order

0. **[F0 per-user budget](F0-rate-budget.md)** — F2, F4, F5 and F6 all add endpoints that
   call into the customer's account; each depends on a limit keyed on identity existing.
1. **[F4 cost tagging](F4-cost-tagging.md)** — the only item that gets *worse* by waiting.
   Tags are not retroactive, so every deploy before this ships is permanently unattributable.
2. **[F3 rollback](F3-rollback.md)** — prerequisites in, pure software, no external dependency.
3. **[F5 evidence pack](F5-evidence-pack.md)** — the hard half is built; the drift diff is small.
4. **[F1b TLS activation](F1b-tls-activation.md)** — start the NS delegation now regardless;
   that part is wall-clock, not work.
5. **[F2 runtime logs](F2-runtime-logs.md)** — highest remaining risk, lowest urgency.
6. **[F6 exit export](F6-exit-export.md)** — depends on F3, F5, and F1b's teardown.

## Mock first, real AWS later

No real AWS account is available yet. Every feature is built against the existing mock
seams (`is_mock` infrastructures, `LAUNCHPAD_MOCK`, botocore `Stubber`) and appends the
claims only real AWS can settle to [`REAL-AWS-VALIDATION.md`](REAL-AWS-VALIDATION.md).
"Done" in the status table means done against mocks until that checklist is ticked.

## Decisions made without the owner

Open questions in the feature files were resolved autonomously so work could proceed.
Each file records its choices under **Decisions** with the reasoning — all reversible.

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

- [ ] **Bump `NEXT_PUBLIC_LAUNCHPAD_SCRIPT_REF`.** It lives in the frontend's deploy
      environment, not the repo. Since #76, staleness is per compute type, so an ECS
      customer on v1 is no longer flagged by the EKS-only v2. The bump still matters: if the
      pinned ref predates #72 the script has no `LAUNCHPAD_COMPUTE_TYPE` handling, so **EKS
      onboarding installs the v1 policy without the EKS grants** and the stale flag can never
      clear. F4 bumps the policy to v3 (a base-statement change, so both compute types are
      flagged) and requires the bump again.
- [ ] **Run `manage.py redact_stored_provisioning_text --dry-run`, then for real, before
      enabling the provisioning-logs endpoint.** Lossy by design, no reverse.
- [ ] Dismiss the GitGuardian incidents on #65 and #73 — both flagged AWS's published
      documentation credentials in test fixtures, since removed.

## Known issues on `main`

The per-user budget, the `databases` exemptions and the malformed-UUID 500 are now owned by
[F0](F0-rate-budget.md). Phase 0's rate-limit carve-out cannot be built as specified — the
gateway does not verify JWTs, `EXEMPT_PATHS` is exact-match, and exempt means *zero* limit
on endpoints that AssumeRole into a customer account — so F0 replaces it.

- `Application.envs` is stored in plaintext. F3 and F6 are designed around that (H7, H6)
  rather than fixing it; encrypting it at rest is not scheduled by any feature.
