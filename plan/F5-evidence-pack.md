# F5 — Compliance evidence pack

**Status:** done (mock-verified; see REAL-AWS-VALIDATION)
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
3. **A live drift diff** of the customer's actual policy, expected vs actual. The policy is
   a **managed** policy (`create_aws_role.sh` runs `create-policy` / `create-policy-version`
   then `attach-role-policy`), so `iam:GetRolePolicy` — inline policies only — would return
   `NoSuchEntity`. Read it with `ListAttachedRolePolicies` → `GetPolicy` →
   `GetPolicyVersion(DefaultVersionId)`. Also diff the **trust policy** (`GetRole`) against
   the expected shape — ExternalId condition present and equal to the infrastructure id.
   No new IAM needed — `iam:*` is already granted.
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

## Decisions

1. **Markdown + JSON.** Browser print covers PDF; revisit only if a buyer asks.
2. **Owner-only.** It carries no secrets, but it describes the whole account's capability
   surface; consistent with the other owner-only surfaces.
3. **`diff_live_policy(iam, infra, *, platform_principal_arn)` takes an IAM client, not a
   Session.** A Session can't be stubbed directly — `botocore.stub.Stubber` wraps a
   client — and taking the client keeps `live_diff.py` symmetric with `iam_precheck.py`,
   which also builds its own `iam`/`sts` clients from `authenticate_infrastructure`'s
   credentials rather than passing a Session around. `infra` is duck-typed (`.id`,
   `.code`, `.compute_type`) so this module, like `policy_data.py`, never imports Django.
   It is deliberately not re-exported from `iam_policy/__init__.py` — `generate.py` never
   needs it, and it's the one module in the package that takes a live AWS client.
4. **Drift is diffed at grant-row granularity (one row per `(Effect, action, resource
   scope)`), not whole-statement equality.** A live policy missing one action out of a
   multi-action `Allow` statement reports that one action as missing, not the entire
   statement as both missing and extra. `Sid` is ignored; Action/Resource lists are
   order-insensitive by construction (each row is a set member); grant identity is
   case-insensitive (IAM action names are), but the rendered diff shows each action in
   the casing the source document actually used, not a lowercased reconstruction.
5. **The Launchpad platform principal has no existing Python source of truth** — it lives
   only as `create_aws_role.sh` defaults (`LAUNCHPAD_PLATFORM_ACCOUNT_ID=221082203366`,
   `LAUNCHPAD_PLATFORM_USER=aklamaash-terraform`). Added `LAUNCHPAD_PLATFORM_PRINCIPAL_ARN`
   to `core/settings.py` / `test_settings.py` / `env.example` with the matching default,
   passed into `diff_live_policy` as a parameter so the ARN never becomes a second
   hard-coded copy inside the Django-free `iam_policy` package.
6. **Module derivation is split three ways, not one flat "instantiated" list.**
   `_compute_type_modules(compute_type)` and `_managed_database_modules()` each read
   `terraform_worker.py`'s own generator source (`inspect.getsource` + a regex over
   `source = "./modules/X"`) for exactly the function that applies to them —
   `_generate_config_ecs`/`_generate_config_eks` for the compute-type modules,
   `_db_module_blocks` for the managed-database ones — rather than invoking
   `_generate_config` (which needs `EKS_PUBLIC_ACCESS_CIDRS` non-empty and a live DB
   query) or unioning everything into one list. A flat union would have an ECS
   infrastructure's pack claim it applies `eks`, and every infrastructure's pack claim
   `rds`/`docdb`/`elasticache` whether or not a database exists — exactly the
   over-claiming the plan warns about. `instantiated_terraform_modules()` (the union of
   all three) still exists, but only to compute what's excluded; `all_terraform_modules()`
   lists `infra/aws/modules/` on disk the same way. All four numbers are read from the
   codebase, not hard-coded, so they survive the concurrent v3 policy branch (and any
   future module) without a code change here.
7. **Evidence bucket defaults: 10 requests / 300s per user** (`RATE_BUDGET_EVIDENCE_LIMIT`
   / `RATE_BUDGET_EVIDENCE_WINDOW_SECONDS`), tighter than `databases` (60/60) — an
   AssumeRole plus up to four read-only IAM calls, requested by an auditor pulling
   evidence rather than a polling dashboard.
8. **Security pre-review, done inline rather than as a separate pass:** the two risks the
   plan names are both closed by construction — the capability narrative and the rendered
   policy are generated from `policy_data` at request time (never a second hand-authored
   or persisted copy of the policy), and every AWS error surfaced to the pack or the log
   carries only `Error.Code`, never `Error.Message` (which carries the assumed-role ARN —
   the same leak class fixed for provisioning logs and terraform stderr). Covered by
   `test_access_denied_is_reported_without_leaking_the_message` and the rendered-policy
   equality tests.

## Out of scope

Resurrecting `modules/security` / `secrets` / `cloud_optimizer`. Enabling them is a
separate decision with its own cost and blast radius; this feature's job is to report
honestly that they are off.
