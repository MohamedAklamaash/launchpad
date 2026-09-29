# Plan

Status of the platform roadmap. Every per-feature file's factual claims were re-verified
against `main` at `9c8743d`; `ROADMAP.md` is the original document and its claims were not.

## Status

| | Feature | Status | Blocked by |
|---|---|---|---|
| — | Phase 0 prework | **done**; the rate-limit carve-out is replaced by F0 | — |
| [F0](F0-rate-budget.md) | Per-user budget for customer-account calls + two unowned bugs | **done** (mock-verified) | nothing |
| [F1](F1b-tls-activation.md) | TLS, custom domains | **done** (mock-verified): Phase 1 (#68); decisions + zone terraform (#75); parts 1–2 (DNS writer; cert bootstrap, ACM policy v4, 443 listener, EKS group-name fix); part 3a (host URLs end-to-end: readiness contract, ECS/EKS deploy wiring, DNS `synced_at`, publish gating, backfill); part 3b (customer custom domains: claim/verify/list/delete API + dashboard UI, authoritative-TXT ownership, per-domain ACM, ALB SNI cap, teardown at every entry point, periodic re-validation) | zone not yet applied (real AWS) — see REAL-AWS-VALIDATION |
| [F2](F2-runtime-logs.md) | Logs | **done** (mock-verified; see REAL-AWS-VALIDATION) | nothing technical |
| [F3](F3-rollback.md) | Rollback | **done** (mock-verified; see REAL-AWS-VALIDATION) | nothing |
| [F4](F4-cost-tagging.md) | Cost attribution | **done** (mock-verified; see REAL-AWS-VALIDATION) | nothing |
| [F5](F5-evidence-pack.md) | Compliance pack | **done** (mock-verified; see REAL-AWS-VALIDATION) | nothing |
| [F6](F6-exit-export.md) | Exit export | **done** (mock-verified; see REAL-AWS-VALIDATION) | nothing |
| [H](H-hardening.md) | Hardening follow-ups (H1–H7) | **in progress** — H1–H7 done (mock-verified) | nothing |

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
6. **[F6 exit export](F6-exit-export.md)** — done; depended on F3, F5, and F1b part 1's
   teardown, all now shipped.

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
      clear. F4 bumped the policy to v3 (a base-statement change — the `ce:*` grant — so
      `required_version_for` is now 3 for **both** compute types) and requires the bump
      again in the same release this ships. F1b part 2 bumped it again to v4 (the `acm:*`
      grants — another base-statement change, so `required_version_for` is now 4 for both
      compute types) and requires the same bump in the release that ships it: until the
      pinned ref moves, cert bootstrap's `policy_version >= 4` gate skips TLS
      (`tls_status=POLICY_STALE`) for every customer who re-runs the onboarding/refresh
      script against the old ref. Per-app metrics bumped the policy to v5 (a base-statement
      change — `cloudwatch:GetMetricData` — so `required_version_for` is now 5 for both
      compute types) and requires the same bump in the release that ships it: until the
      pinned ref moves, the refresh-policy snippet reinstalls the old v4 policy and the
      metrics endpoint's `policy_refresh_required` (422) never clears for a customer who
      re-runs it.
- [ ] **Run `manage.py redact_stored_provisioning_text --dry-run`, then for real, before
      enabling the provisioning-logs endpoint.** Lossy by design, no reverse.
- [ ] Dismiss the GitGuardian incidents on #65 and #73 — both flagged AWS's published
      documentation credentials in test fixtures, since removed.

## Known issues on `main`

The per-user budget, the `databases` exemptions and the malformed-UUID 500 were owned by
[F0](F0-rate-budget.md), now done: a per-user Redis budget on the databases endpoints, the
gateway exemptions removed, and the malformed-UUID path returns 404. Phase 0's rate-limit
carve-out could not be built as specified — the gateway does not verify JWTs, `EXEMPT_PATHS`
is exact-match, and exempt meant *zero* limit on endpoints that AssumeRole into a customer
account — so F0 replaced it instead.

- `Application.envs` is stored in plaintext. F3 and F6 are designed around that (H7, H6)
  rather than fixing it; encrypting it at rest is not scheduled by any feature.
