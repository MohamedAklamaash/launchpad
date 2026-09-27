# F5 — Compliance evidence pack

**Status:** generator half done (#67, #72), pack not started
**Depends on:** nothing · **Blocked by:** nothing

The hard part is already built. What remains is small and self-contained.

## Goal

A machine-generated, honest evidence pack per infrastructure: what Launchpad can do in the
customer's account, what it actually has, where those two differ, and what the policy does
*not* contain.

## Verified on main at `9c8743d`

**Done:**

- `api/cloud_providers/aws/iam_policy/` — `policy.json` as the single source of truth,
  `policy_data.py`, `generate.py`, rendering four generated regions across
  `create_aws_role.sh` and `docs/IAM_POLICIES.md`.
- CI job `check-iam-policy` fails on drift.
- Each version is bound to a content hash of its statements, so changing grants without a
  version bump fails the build, and a released version cannot be silently redefined.
- `Infrastructure.policy_version` records what the customer actually applied; the dashboard
  distinguishes never-recorded from behind.
- EKS grants are data under `compute_type_statements`, rendered per compute type.
- Policy is at **v2**.

**Verified for the honest-limitations section — both claims hold:**

- `iam:*` is granted on `*`. The EKS resource scoping is defense-in-depth, not containment,
  because the same policy lets the role re-grant itself. The EKS work already corrected its
  own docs to say this (`b84f7be`); **reuse that wording rather than writing new**.
- `modules/security` (CloudTrail + KMS), `modules/secrets` and `modules/cloud_optimizer`
  exist in `infra/aws/modules/` but are **never instantiated**. The generator emits only
  `alb`, `ecr`, `ecs`, `eks`, `iam`, `vpc` (plus `rds`/`docdb`/`elasticache` per managed
  database). A pack that enumerated the module directory would over-claim CloudTrail and
  KMS coverage to a security reviewer. Exclude them explicitly and say why.

## Design

Zip of JSON + Markdown. No PDF renderer — browser print covers it.

Contents:

1. The rendered policy for this infrastructure's `compute_type`, plus its version.
2. The trust-policy shape, showing the ExternalId condition.
3. **A live drift diff**: `iam:GetRolePolicy` against the customer's actual attached policy,
   expected vs actual. No new IAM needed — `iam:*` is already granted and the assumed-role
   session already exists.
4. A capability narrative: what each grant is for.
5. **Honest limitations**, in the pack itself, not an appendix: the `iam:*` caveat above,
   the three uninstantiated modules, and that `ce:GetCostAndUsage` (once F4 adds it) is
   account-wide financial visibility with no resource scoping.

The drift diff is also the preflight the roadmap wanted: a customer whose applied policy is
behind gets an actionable message instead of a terraform stack trace.

## Files

- `infrastructure-service/api/services/evidence_pack.py` — new; assembles the zip.
- `infrastructure-service/api/cloud_providers/aws/iam_policy/` — a `diff_live_policy()`
  helper beside the existing data (it belongs with the source of truth).
- New owner-scoped endpoint + gateway route + a dashboard download button.

## Tests

- Drift diff correct against a live-mutated policy (mock returns a policy missing a grant).
- The three uninstantiated modules are **absent** from the pack, and the limitations
  section is present — assert on both, since the failure mode is silent over-claiming.
- Rendered policy in the pack equals the committed one for each compute type.
- Cross-tenant: 404 for a stranger, 403 for an invited user.

## Security pre-review

**Required.** The pack is a new export surface and states security claims a buyer will
rely on. The specific risks: over-claiming (an auditor reads this), and the pack becoming a
second place the policy is written down — it must be generated, never authored.

Lower risk than F6, which carries actual secrets; this one carries claims.

## Open questions

1. Is Markdown + JSON sufficient, or is PDF a buyer requirement?
2. Owner-only, or may an invited ADMIN download it? It contains no secrets but does
   describe the whole account's capability surface.

## Out of scope

Resurrecting `modules/security` / `secrets` / `cloud_optimizer`. Enabling them is a
separate decision with its own cost and blast radius; this feature's job is to report
honestly that they are off.
