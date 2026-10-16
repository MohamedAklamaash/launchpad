# F8 — One-click CloudFormation onboarding

**Status:** design only, not started · **Depends on:** nothing · **Blocked by:** nothing —
every open question below is non-blocking, forward-looking, and explicitly deferred.

## Summary

Add a CloudFormation "Launch stack" quick-create link as a second onboarding path,
offered **alongside** `create_aws_role.sh`, never replacing it. The template is generated
from the same `policy.json` source of truth that already drives the script and
`docs/IAM_POLICIES.md`, hosted as a pinned, versioned object in a new platform-owned S3
bucket, and completed by the dashboard itself calling `AssumeRole` ("Verify connection")
rather than a custom-resource Lambda posting a callback from inside the customer's
account. The shared-role problem (several infras in one AWS account sharing one
`LaunchpadDeploymentRole` with a merged `ExternalId` list) is modeled as **one
account-level stack, named `LaunchpadDeploymentRole`, whose trust `ExternalId` list is a
stack parameter** — the same sharing boundary the script already uses, just owned by CFN
instead of ad-hoc `iam:update-assume-role-policy` calls. An account onboarded by one
method stays on that method permanently; no import/migration path ships in this round.

## Goals

- A customer who prefers a GUI can onboard `LaunchpadDeploymentRole` + the deployment
  policy with zero shell access, in one click, with the same security properties the
  script already guarantees (mandatory `ExternalId`, no wildcard account on the EKS
  `Deny`/`NotResource` backstop, no secret baked into a long-lived artifact).
- The CFN path and the script path are generated from one source (`policy.json`) and
  gated by the same CI drift check, so they can never grant different permissions.
- Policy-refresh and infra-removal semantics for CFN-onboarded accounts are
  CFN-idiomatic (stack update / change set), never a direct out-of-band IAM write that
  would drift the stack's own view of its resources.
- A customer never has to choose between the two methods mid-account-lifetime by
  accident — the system detects and enforces exclusivity.

## Non-goals

- Deprecating or hiding `create_aws_role.sh`. Both paths ship and stay fully supported;
  this is additive.
- Importing an existing script-created role into a CFN stack (CFN's `CreateChangeSet
  --change-set-type IMPORT` flow exists but needs an exact property match and isn't a
  one-click UX). An account picks a method at its first successful onboarding and keeps
  it. See Open questions.
- A `ComputeType` CFN parameter with conditional statement splicing. Two complete,
  separately-generated templates (one per `compute_type`) are generated instead,
  mirroring how `policy_data.document(compute_type)` already produces two concrete
  documents today — simpler and needs no untested CFN list-conditional idiom.
- A custom-resource Lambda completing onboarding from inside the customer's account.
  Decided against; see "Completing onboarding" below.
- CloudFront (or any non-S3 URL) in front of the hosted templates. Not a deferred
  optimization — `cloudformation:CreateStack`'s `TemplateURL` parameter only resolves
  against an S3 object location, so this is foreclosed by the API itself, not a choice.
- Fixing the pre-existing `create_aws_role.sh` / `nuke_worker.py` region-default mismatch
  for *data-plane* resources (script defaults `LAUNCHPAD_REGION=us-east-1`;
  `_NukeContext.region` defaults to `us-west-2`). Unrelated to and untouched by this
  design: the CFN stack's own region (`CFN_STACK_REGION`, see "Completing onboarding"
  below) is a separate, fixed platform constant that never reads `infra.metadata
  .aws_region` at all — chosen specifically so this design doesn't inherit the
  pre-existing mismatch rather than because it was fixed. Out of scope here.
- Adding a `Deny`/`NotResource` backstop around the new `cloudformation:` action grant.
  The existing `iam:*` base grant already dominates it (same "defense-in-depth, not a
  hard boundary" reasoning `policy.json`'s notes already use for
  `acm:AddTagsToCertificate`) — note that this grant is an explicit action list, not
  `cloudformation:*`, for the unrelated reason given under "Template generation" below
  (least privilege on a customer-facing policy document).

## Context & constraints

- `deployment-services/infrastructure-service/api/cloud_providers/aws/iam_policy/policy.json`
  / `policy_data.py` / `generate.py` — the single source of truth and its renderer. Per
  root `CLAUDE.md`'s "IAM policy source of truth": never hand-edit a generated region;
  bump `version` for any granted-action change; CI (`check-iam-policy`) runs
  `generate.py --check`.
- `app_scripts/create_aws_role.sh` — the shared-role merge logic (lines ~130–230: reads
  the role's existing `sts:ExternalId` condition and `launchpad:compute-types` tag before
  writing, so a second infra in the same account never clobbers the first's trust) that
  this design must replicate in CFN's ownership model, not duplicate with different
  semantics.
- `api/models/infrastructure.py` — `Infrastructure.compute_type` (immutable after
  create — no code path ever updates it post-creation; `InfraUpdateSerializer` at
  `api/views/infrastructure.py:62-66` only allows `name`/`max_cpu`/`max_memory`/`code`),
  `policy_version`, `onboarding_token_hash`/`_used_at`/`_expires_at`,
  `issue_onboarding_token()`, `policy_refresh_required()`,
  `parse_reported_policy_version()`. `shared/enums/orchestrator.py` —
  `ComputeType.{ECS_FARGATE,EKS}`.
- `api/views/infrastructure.py:284-431` (`infrastructure_onboarding_callback`) and
  `api/views/script_api_key.py:84-143` (`infrastructure_policy_refresh_callback`) — the
  two callback contracts this design must stay interoperable with (same
  `policy_version`-write-then-`maybe_reenqueue_after_policy_refresh` tail, same
  `infra.created` publish + `InfraQueue.enqueue_provision`).
- `api/cloud_providers/aws/authenticate.py:41` — `RoleArn` is always
  `arn:aws:iam::{infrastructure.code}:role/LaunchpadDeploymentRole` (role name hardcoded,
  not per-infra). This is why "stack per infra with a distinct role name" is rejected
  outright (Option B below) — it would require threading a per-infra role name through
  this call, `nuke_worker.py`'s `_ROLE_NAME` constant, and every doc/runbook that assumes
  the fixed name, for zero added isolation (every role still gets the identical
  account-wide policy).
- `api/services/nuke_worker.py` — `_manage_deployment_role` (639-648),
  `_remove_external_id_from_trust` (651-678), `_delete_deployment_role_and_policy`
  (681-705), `_other_infras_same_account` (580-581, filters by `Infrastructure.code`
  only — the sharing boundary is the AWS account, not the owning user, confirmed
  pre-existing behavior this design does not change). `deployment_role` runs **last**,
  after `verify`, specifically because it's the credential every earlier step
  authenticates with — unchanged for the CFN branch.
- `launchpad-frontend/lib/onboarding-scripts.ts` — the pinning/override/misconfiguration
  idiom (`NEXT_PUBLIC_LAUNCHPAD_SCRIPT_REF` mandatory in prod, `NEXT_PUBLIC_..._BASE_URL`
  override validated to HTTPS-or-localhost) to mirror for the new CFN module, with one
  change: the override must validate an **S3** URL shape, not arbitrary HTTPS.
- `launchpad-frontend/app/dashboard/infrastructures/new/page.tsx` (bootstrap snippet,
  ~line 236), `.../infrastructures/[id]/page.tsx` (`policyState`, ~140-180; "Verify"/
  "Refresh" buttons, ~447-660), `components/policy-refresh-dialog.tsx` (script API key
  issue + refresh snippet) — the UI surfaces this design adds a CFN sibling to.
- `.github/workflows/ci.yml` — `check-iam-policy` (stdlib-only `generate.py --check`),
  `check-terraform` (offline `fmt`/`init -backend=false`/`validate`, no AWS creds — the
  precedent for an offline-only CFN structural check).
- `docs/IAM_POLICIES.md` — generated regions (script heredoc, docs JSON blocks, docs CLI
  block, policy version) plus hand-written prose sections this design adds a CFN
  subsection to.
- Root `CLAUDE.md`'s "Two trees are the exception" — `infra/platform-dns/` and
  `infra/platform-builder/` are platform-owned Terraform outside the customer tree and
  outside the provisioning worker's reach. The new template-hosting bucket belongs in
  that same family, not inside `deployment-services/infrastructure-service/infra/aws/`
  (see `terraform-layout-gotchas` memory: that tree's root config is Python-generated and
  dead on disk — a new platform bucket must not be added there).
- Critical path: this touches the cross-account trust boundary
  (`LaunchpadDeploymentRole`'s `AssumeRolePolicyDocument`) and the one grant (`iam:*`)
  that makes every other grant in `policy.json` non-isolating. Bar is elevated
  accordingly — every new write path (template, verify endpoint, nuke branch) is designed
  to never leave the trust policy in a state neither the script nor CFN can reconcile.

## Options considered

### Shared-role modeling

**Option A — one account-level stack (`LaunchpadDeploymentRole`), `ExternalIds` as a
stack parameter, merged server-side.** CFN's native unit of ownership (a stack) models
exactly what already exists: one role, one trust policy, N infras. The dashboard computes
the merged `ExternalIds` CSV the same way the script already does on the IAM side, just
read from `Infrastructure` rows instead of a live `get-role` call.
- Pros: matches the existing account-is-the-sharing-boundary semantics exactly; no new
  resource-naming scheme; CFN's own drift detection (`DetectStackDrift`) becomes available
  as a bonus diagnostic nobody has today.
- Cons: a second infra onboarding into an *existing* CFN account can't be a pure
  "quick-create" link (`CreateStack` on an already-existing stack name fails
  `AlreadyExistsException`) — needs a different, customer-driven "update the existing
  stack" flow (see "Completing onboarding" and Decision below).
- Cost/complexity: M. One new model field, one new backend endpoint, two frontend flows
  (create vs. update-instructions), one nuke branch.

**Option B — stack per infra, distinct role name per stack.** Rejected outright — see
Context & constraints above (`authenticate.py:41`'s hardcoded role name, and zero added
isolation for a much larger blast-radius change).

**Option C — CFN template only launches a custom-resource Lambda that performs the IAM
work; no declarative `AWS::IAM::Role`/`ManagedPolicy` resources.** Rejected: this doesn't
avoid the shared-role problem (the Lambda would need the exact same merge logic Option A
needs, just hidden inside opaque compute instead of declarative, console-diffable
resources), and for a template whose entire purpose is granting IAM permissions, shipping
arbitrary Lambda code into the customer's account is a *worse* trust-boundary expansion
than a declarative resource a security reviewer can read at a glance. (A narrower, 
different use of a Lambda — just to fire the completion callback, not to touch IAM — is
considered separately below and also rejected, for different reasons.)

### Completing onboarding

**Option 1 — CFN custom-resource Lambda posts the callback.** Requires shipping Lambda
code into every customer account; needs outbound internet access from inside a
potentially VPC-restricted account with no good customer-side diagnosis if that fails;
needs periodic runtime-version bumps in the template; and puts the single-use onboarding
token inside a CFN stack parameter — visible in the console's Parameters tab and
CloudTrail for the stack's lifetime, a materially worse exposure surface than today's
~24h env-var-in-a-shell lifetime.

**Option 2 — dashboard-driven "Verify connection" (chosen).** The platform already has a
fully-hardened `authenticate_infrastructure`/`AssumeRole` path used by every other
customer-account action. Reusing it means: zero new in-account compute, no Lambda
outbound-network failure class, and the onboarding token never needs to leave the
platform's own database — the backend minted it, so it can burn it itself once
`AssumeRole` succeeds, authenticated by the user's real JWT instead of a public
`AllowAny` token-bearing endpoint. Strictly simpler and strictly more secure than
Option 1.

## Decision

**Option A** for shared-role modeling, **Option 2** for completion. Concretely:

### 1. Template generation (`generate.py`)

Two complete, fully-generated JSON templates (not partial "generated regions" — nobody
hand-edits a `.template.json`, so the whole file is machine output, checked for drift the
same way `_render_all`/`run()` already diffs rendered-vs-committed bytes for the script
and docs):

```
deployment-services/infrastructure-service/api/cloud_providers/aws/iam_policy/cloudformation/
  launchpad-deployment-role.ecs-fargate.template.json
  launchpad-deployment-role.eks.template.json
```

Each template:
- `Parameters`: `ExternalIds` (**`CommaDelimitedList`**, required, no default — CFN
  resolves a `CommaDelimitedList` `Ref` to a native JSON array, which is exactly the
  "always render as a list, even for one" shape `create_aws_role.sh` already commits to
  for the same reason), `LaunchpadPlatformAccountId` (String, `AllowedPattern` 12 digits),
  `LaunchpadPlatformUser` (String). No `ComputeType` parameter (see Non-goals) and no
  `LAUNCHPAD_ALLOW_NO_EXTERNAL_ID`-style escape hatch — the script's escape hatch exists
  only for advanced manual setups piping an `ExternalId` some other way; CFN's required
  parameter already can't be omitted, so there's nothing to add.
- `Resources`: `AWS::IAM::Role` (`RoleName: LaunchpadDeploymentRole` — fixed, matches
  `authenticate.py:41`; `MaxSessionDuration: 7200` baked into the `eks` template only,
  omitted — AWS default 3600 — from the `ecs-fargate` template, mirroring the script's
  conditional `--max-session-duration`; `AssumeRolePolicyDocument` with
  `"sts:ExternalId": {"Ref": "ExternalIds"}`; four tags, each read back through the
  assumed-role session rather than assumed — see "Completing onboarding" below for why
  tags, not a `DescribeStacks` call, are the source of truth here:
  - `launchpad:managed-by` = `cloudformation` (literal) — the exclusivity check (see
    "Cross-path exclusivity" below).
  - `launchpad:policy-version` = `policy_data.version()` (literal, baked in at generation
    time) — what `Infrastructure.policy_version` is set from; **not** the `PolicyVersion`
    Output below, which exists only for a human checking the console.
  - `launchpad:compute-types` = a **static, per-template literal**: `ecs_fargate` on the
    `ecs-fargate` template, `ecs_fargate+eks` on the `eks` template (not a computed union
    — templates are static files with no account-level state to compute from; the `eks`
    template's value over-reports relative to a lone `ecs_fargate` sibling's actual grant
    set, which is harmless — `_expected_policy_document`'s union means `ecs_fargate`
    contributes no extra statements regardless). `live_diff.py:238-256`
    (`_served_compute_types`) reads exactly this tag key to decide which compute types'
    extra statements a *shared* role is expected to carry, falling back to "just this
    infra's own compute_type" when the tag is missing or unrecognized — a CFN-managed
    role with no `launchpad:compute-types` tag would make F5's evidence pack silently
    under-report expected grants the moment two infras of different compute types share
    one CFN-onboarded account.
  - `launchpad:cfn-stack-name` = `{"Ref": "AWS::StackName"}` — the quick-create link
    pre-fills a stack name but the console lets the customer edit it before clicking
    Create, so this is how the backend later finds "this account's one CFN stack" without
    assuming it kept the suggested name. See "Completing onboarding" below for how this
    is read and persisted.

  …and `AWS::IAM::ManagedPolicy` (`ManagedPolicyName: LaunchpadDeploymentPolicy`, `Roles:
  [!Ref Role]`, `PolicyDocument` = `policy_data.document(compute_type)` verbatim, with one
  rendering difference: every `Resource`/`NotResource` string containing
  `__LAUNCHPAD_ACCOUNT_ID__` becomes `{"Fn::Sub": "<same string with
  ${AWS::AccountId}>"}` — CFN's native pseudo-parameter replaces the script's `sed`
  substitution, and needs no customer action at all. This wrapping changes only
  `Resource`/`NotResource` encoding, never `Action`/`Effect`/`Condition`, so
  `statements_hash()`/`document_hashes()` binding (and therefore the staleness flag a
  customer sees) remains an accurate measure of what's actually granted, regardless of
  which of the three render targets — script heredoc, docs block, or CFN template —
  produced the bytes a given customer actually applied.
- `Outputs`: `PolicyVersion` = `policy_data.version()`, the same literal as the
  `launchpad:policy-version` tag — kept purely so a customer reading the CFN console sees
  the version without having to know IAM tags exist. The backend never reads this Output;
  see "Completing onboarding" below.

`generate.py` gains a private `_cfn_statement(stmt)` helper (lives in `generate.py`, not
`policy_data.py` — keeps `policy_data.py` provider-agnostic, per its own stated stdlib-
only/no-CFN-knowledge design) that does the `Fn::Sub` wrapping, plus the two whole-file
renderers. **New CFN-specific invariants** (NOT a verbatim reuse of
`_assert_invariants` — that function's existing "no `$`/backtick in the rendered policy"
check is a bash-heredoc-safety rule and would fail permanently on the `eks` template,
which legitimately renders `${AWS::AccountId}` inside every `Fn::Sub`):
- No bare `__LAUNCHPAD_ACCOUNT_ID__` substring survives in the rendered CFN JSON text —
  reuses the existing stray-placeholder check's *intent*, just run against CFN output
  instead of the heredoc.
- The `ecs-fargate` template contains zero `Fn::Sub` objects and zero occurrences of the
  placeholder at all — the parity check for "the default branch never substitutes."
- **Round-trip check**: for every `Fn::Sub` string in the rendered CFN `PolicyDocument`,
  substituting `${AWS::AccountId}` back to the literal `__LAUNCHPAD_ACCOUNT_ID__` and
  stripping the `Fn::Sub` wrapper reproduces exactly one `Resource`/`NotResource` string
  from `policy_data.document(compute_type)` — a static, CI-time guarantee that the CFN
  template grants the identical action/resource set `policy_data` says it should, closing
  the gap a real-AWS-only "byte-identical" claim would otherwise leave until ticket 8.

**Explicit `cloudformation:` action list, added as a *base* statement** (applies to
both compute types, not an EKS-only extra): `Resource: "arn:aws:cloudformation:*:*:stack/*/*"`
— account *and* stack-name wildcarded, deliberately, on both axes. Stack-name
wildcarding follows directly from "Completing onboarding" below: the customer can
rename the stack before creating it, so a resource pattern pinned to literal stack
name `LaunchpadDeploymentRole` would deny `DescribeStacks`/`UpdateStack`/`CreateChangeSet`
on a renamed one — narrowing by name is unenforceable once the name itself isn't
fixed. Root `CLAUDE.md`'s "never widen to `*`" warning is specifically about the EKS
`Deny`/`NotResource` backstop, not a plain `Allow`, and the resource pattern here is
the same double-wildcard shape every other base-statement `Resource: "*"` already
uses — the `iam:*` base grant already dominates this as a trust boundary regardless
(same "defense-in-depth, not a hard boundary" reasoning `policy.json`'s own notes use
for `acm:AddTagsToCertificate`).

**The action list itself is not `cloudformation:*`, even though `iam:*` already makes
it defense-in-depth rather than a hard boundary.** This policy is what a customer's
security reviewer reads, and `cloudformation:*` on every stack in the account reads as
"Launchpad can delete any CloudFormation stack here" — least privilege on a
customer-facing document is worth the explicit list even where the real boundary is
elsewhere. Grant exactly what the tickets below call, nothing else, and deliberately
exclude `DeleteStack` (Launchpad never calls it on its own stack — see "Removal — the
last-infra-in-an-account case" below for why: `AWS::IAM::ManagedPolicy` detaches
before the role deletes, so the assumed-role session performing a self-`DeleteStack`
would lose its own permissions mid-teardown), `CreateStack` (only the customer's
quick-create click does this), and `ExecuteChangeSet` (a customer always reviews and
applies a widening change set themselves, never Launchpad). Current list, grown only
when a later ticket's design calls a new action — add it here when that happens:

- `DescribeStacks` — verify's best-effort stack read (§4); nuke's poll-to-terminal (§6)
- `DescribeStackEvents` / `DescribeStackResources` — diagnosing a stuck update
- `GetTemplate` / `GetTemplateSummary` — reading the currently applied template
- `CreateChangeSet` / `DescribeChangeSet` / `ListChangeSets` / `DeleteChangeSet` — the
  refresh flow's change-set review link (§5)
- `UpdateStack` — nuke's non-last-infra `ExternalIds` narrowing (§6)

This needs `version` bumped (a base-statement change, so `required_version_for` rises
for *both* compute types — same mechanical consequence every prior base bump already
had per `plan/README.md`'s Standing release actions; the exact version number is
whatever's next when this ticket lands, not necessarily the number a stale draft of
this plan names). **Accepted side effect, stated explicitly**: this flags every
script-only account as policy-stale for a grant it will never exercise, since
`policy_data`/`document_hashes` model `compute_type`, not onboarding method — adding a
third axis to that scheme to avoid flagging script-only accounts is explicitly not worth
it; a refresh is cheap, and the ref-bump release action already exists.

**CI**: new job `check-cfn-template` (separate from `check-iam-policy`, mirroring how
`check-terraform` is its own job) installs a **pinned** `cfn-lint` version (`pip install
cfn-lint==<X>`, same discipline as `check-python`'s `ruff==0.16.5` — an unpinned install
means an unrelated commit can turn CI red when cfn-lint cuts a new release; `generate.py`
itself stays stdlib-only, this is a structural-validation step over the *output*, not a
dependency of the generator) and lints both generated templates. No AWS credentials,
same posture as `check-terraform`'s offline `validate`.

### 2. Cross-path exclusivity

A CFN-onboarded role carries `Tags: [{"Key": "launchpad:managed-by", "Value":
"cloudformation"}]`, written by the template itself (not CFN's automatic
`aws:cloudformation:stack-name` tag — an explicit tag the script can check for without
assuming anything about CFN's own bookkeeping). `create_aws_role.sh`'s EXISTING ROLE
STATE section (~lines 146-194) gains one more read: after `aws iam get-role`, if
`Role.Tags` contains `launchpad:managed-by=cloudformation`, print an error and `exit 1`
instructing the customer to use the stack's Update flow instead of the script, rather than
silently calling `update-assume-role-policy` out-of-band and drifting the stack. (This is
the mirror image of the dashboard-side check below — belt and suspenders, since a customer
might run the script from a stale bookmark/docs page without ever touching the dashboard.)

On the dashboard side, `Infrastructure.onboarding_method` (new field, below) is computed
per AWS account from **successfully-authenticated** siblings only
(`Infrastructure.objects.filter(code=account_id, is_cloud_authenticated=True)`), never
from an abandoned attempt — an infra where the customer opened a quick-create link (or
even created the stack) but never completed "Verify connection" must not lock the account
to a method it never actually finished adopting.

### 3. Hosting

New platform-owned Terraform tree `infra/platform-templates/` (joins
`infra/platform-dns/` and `infra/platform-builder/` as the third documented exception to
"customer-account Terraform lives under infrastructure-service" — root `CLAUDE.md`'s
"Two trees are the exception" section gets a one-line addition in the same ticket). One
S3 bucket, versioning on, Block Public Access configured to allow only `s3:GetObject` on
the templates prefix (no `ListBucket` — no reason to let anyone enumerate every
historical pinned ref). `TemplateURL` for `cloudformation:CreateStack` **must** be an S3
object location (AWS's own API contract — not a raw-HTTPS URL like the script's GitHub
raw hosting), so the new frontend module's override-env validation enforces an S3 URL
shape (virtual-hosted or path-style), rejecting anything else, including a hypothetical
CloudFront URL (which wouldn't work for `TemplateURL` at all — see Non-goals).

Publish is a **standing release action** (same operational category as bumping
`NEXT_PUBLIC_LAUNCHPAD_SCRIPT_REF` today — "lives in the frontend's deploy environment,
not the repo," per `plan/README.md`): upload both generated templates to
`s3://<bucket>/<git-ref>/launchpad-deployment-role.{ecs-fargate,eks}.template.json` using
an **S3 conditional PUT (`If-None-Match: *`)** so a pinned key can never be silently
overwritten — a stronger guarantee than "bucket versioning plus discipline," since the
write itself fails if the key already exists. Versioning stays on as defense-in-depth
against an accidental delete. A new `NEXT_PUBLIC_LAUNCHPAD_CFN_TEMPLATE_REF` env var (same
mandatory-in-prod, throws-at-build-time-if-unset contract as `..._SCRIPT_REF`) pins the
frontend to one published ref.

**Quick-create URL limits**: `ExternalIds` grows with every infra in an account. If the
merged list would exceed ~20 entries (matches the existing custom-domains-per-infra cap
convention) or the encoded URL would exceed ~1800 characters, don't build a quick-create
link at all — render the parameter values as a copy-paste block instead, with instructions
for the console's manual "Specify template" → paste-parameters flow. Degrades gracefully,
no new endpoint.

### 4. Completing onboarding ("Verify connection")

New endpoint, e.g. `POST /api/v1/infrastructures/{id}/cfn-verify/`, **JWT-authenticated**
(ownership-checked, unlike the script callback's public `AllowAny`), wrapped in the same
**F0 per-user Redis budget** every other customer-account-calling endpoint uses (this
endpoint calls `AssumeRole` into a customer account, exactly the class of call F0 exists
to bound).

**Order matters and the first draft of this plan had it backwards**: the platform holds
*no* credentials in the customer's account until `AssumeRole` succeeds, so nothing CFN-
specific can be read before that — there is no "check the stack, then assume the role."
`AssumeRole` has to come first; everything CFN-specific is read *through* the session it
grants, same as F5's live-diff already does. Corrected flow:

1. Atomically claim the onboarding token exactly like the script callback does today
   (`onboarding_token_used_at__isnull=True` conditional update) — this is the **sole**
   idempotency gate; see below for why `is_cloud_authenticated` must not also gate it.
2. `authenticate_infrastructure(infra)` through the **existing bounded-retry wrapper**
   (`infrastructure.py:349-374` today: 3 attempts, 2s sleep between, `403` +
   `Retry-After: 30` on exhaustion) — not a single unwrapped call. That retry exists for
   IAM's eventual consistency right after role/policy creation
   (`test_backend_iam_propagation_retry.py` is the regression test for the same race on
   the provisioning path); a CFN stack reaching `CREATE_COMPLETE` is the same kind of
   "just created" moment. On failure: release the token claim, return the same
   `403`/`Retry-After` shape the script callback returns today.
3. With the now-assumed session, `iam:ListRoleTags(RoleName="LaunchpadDeploymentRole")`.
   Reject (release claim, **400**) if `launchpad:managed-by` isn't `cloudformation` —
   this is the one place that actually confirms the customer used the CFN path at all,
   since `AssumeRole` alone would also succeed against a script-created role. Read
   `launchpad:policy-version` (the source of truth for `policy_version` — not the
   template's `PolicyVersion` Output, which is a convenience echo of the same literal for
   a human checking the console, never re-derived or trusted over the tag) and
   `launchpad:cfn-stack-name` (`{"Ref": "AWS::StackName"}` baked in by the template —
   **discovered here, not assumed to be the literal `LaunchpadDeploymentRole`**, because
   a quick-create link's stack name is editable by the customer before they click
   Create). Persist `launchpad:cfn-stack-name`'s value onto a new
   `Infrastructure.cfn_stack_name` field (nullable) — nuke's CFN branch and the refresh
   endpoint read it back rather than assuming a name.
4. `cloudformation:DescribeStacks(StackName=<the discovered name>)` is **best-effort**,
   not load-bearing: logged and surfaced for observability/debugging (confirms the stack
   this tag points at is actually reachable with the stack region decided below) but a
   failure here does not fail the verify call — step 3 already proved the role exists,
   is trusted, and is CFN-managed, which is the actual thing being verified.
5. On success: set `onboarding_method='cloudformation'`, `policy_version` from the role
   tag, and run the **same post-AssumeRole tail** the script callback already runs —
   confirm the token claim (no separate "burn" step; the claim from step 1 *is* the
   burn), `maybe_reenqueue_after_policy_refresh`, publish `infra.created`, enqueue
   provisioning. This whole flow, steps 2-5, is **factored out of
   `infrastructure_onboarding_callback` (api/views/infrastructure.py:284-431) into one
   shared `InfrastructureService` method** both the existing script-callback view and
   this new view call, so the two paths can't drift in error handling or accidentally
   lose the retry/claim-release behavior.

   **`policy_version` is written onto every `onboarding_method='cloudformation'` sibling
   sharing this AWS account (`code`), not just the infra that called this endpoint** —
   they share one stack and therefore one live policy document; the script path has this
   exact gap today (only the calling infra's row gets updated, a pre-existing issue noted
   but out of scope for F8 to fix), and there's no reason to reintroduce it on a path
   being built fresh. The same sibling-wide write applies after a successful refresh (see
   below).

**Idempotency is the token claim alone, never `is_cloud_authenticated`.**
`authenticate_infrastructure` sets `is_cloud_authenticated=True` as a side effect of a
successful `AssumeRole` (`authenticate.py:106`, mirrored for mock infras at line 30) —
*before* steps 3-5 run, and nothing downstream of it ever resets it back to `False` on a
later failure. If step 3 or
4 then failed and released the claim, a naive "already authenticated → 409" precondition
on a retry would permanently block the one thing a retry needs to do. The claim
(`onboarding_token_used_at`) is the only state this endpoint gates on: not yet claimed →
proceed; claimed and not released (a prior attempt is still succeeding, or already
succeeded) → `403`/`409`.

**Token expiry is intentionally not enforced on this path.** The script's bootstrap
snippet runs synchronously end-to-end in one shell session, so a short
`onboarding_token_expires_at` TTL only ever bites a slow typist. The CFN path is
structurally asynchronous — a customer may open the quick-create link, create the stack,
and not come back to click "Verify connection" for days — so this endpoint ignores
`onboarding_token_expires_at` entirely (the token's *single-use* claim is still the real
security property; expiry was defense-in-depth against a leaked-but-unused token, not the
mechanism this flow depends on). If `infra.onboarding_token_hash` is `None` (token never
issued, or cleared by some other path), return a clear **400** directing the customer to
re-trigger token issuance from the infra detail page (`issue_onboarding_token()` is
already idempotent-safe to re-call; no new mechanism needed, just a UI entry point — see
Frontend below).

**Stack region is a fixed platform constant, not a customer choice or a read of
`infra.metadata.aws_region`.** The role/policy this template creates are IAM resources —
global, not regional — so the CFN *stack's* region is purely where AWS happens to file
the stack's own bookkeeping, with zero bearing on where any data-plane resource runs.
Letting it vary invites exactly the bug this plan almost shipped: the quick-create link,
`verify`, `refresh`, and `nuke_worker.py`'s CFN branch must all agree on one region to
find the same stack, and `infra.metadata.aws_region` already means something unrelated
(where *data-plane* resources provision — `_NukeContext.region` defaults `us-west-2`
there, vs. the script's own unrelated `LAUNCHPAD_REGION=us-east-1` default — conflating
the two was this plan's own near-miss). Decision: every CFN stack this design creates is
pinned to **`us-east-1`**, hardcoded in the quick-create URL builder (no `region=`
parameter exposed to the customer at all) and in one shared backend constant
(`CFN_STACK_REGION`) used by verify, refresh, and nuke. This is the IAM-is-global
equivalent of the fixed role/policy names — not configurable, by design, because nothing
needs it to be.

### 5. Policy refresh vs. infra removal — two different CFN mechanisms, deliberately

These look similar (both mutate the stack's parameters) but have an important difference:
refresh **widens** what's granted and is **customer-initiated**; removal **narrows** trust
and is a **background system action** during nuke. They get different mechanisms:

- **Refresh (customer-initiated, widening)**: for an already-onboarded, already-
  authenticated CFN infra, a new endpoint calls `cloudformation:CreateChangeSet` with the
  newer-version template's S3 `TemplateURL` and the existing parameters, then hands the
  customer the documented change-set review link (`#/stacks/changeset/review` with that
  `changeSetId` — confirm the exact query-string shape in the real-AWS checklist below,
  since quick-*update* deep links with parameter prefill are not an AWS-documented
  feature the way quick-*create* links are). The customer reviews and executes it in the
  console — one click, same "customer reviews and applies" posture the script already has
  (the script doesn't auto-apply anything the customer didn't invoke either). This reuses
  a session Launchpad **already legitimately holds** for that infra — it is not a new
  trust boundary, just exercising an existing one, the same way every other
  provisioning/deploy/destroy call already does.
- **Removal (system-initiated, narrowing, nuke's `deployment_role` step)**: direct
  `cloudformation:UpdateStack` with the recomputed `ExternalIds` parameter (this infra's
  id removed), no change set, no customer review — matches the *existing*,
  already-unreviewed `_remove_external_id_from_trust` precedent for the script path
  exactly (narrowing is this system's own cleanup, not a request the customer needs to
  bless; widening a human should always see first).
- **A second infra onboarding into an *existing* CFN-managed account** gets neither
  mechanism automatically. Launchpad has no established, legitimate session for the *new*
  infra yet (no trust exists for its `ExternalId` until the customer grants it) — using
  an *existing* infra A's session to script trust changes for a possibly-different-owner
  infra B would cross a tenant boundary (`Infrastructure.code` isn't user-scoped; the
  account-is-the-sharing-boundary design is pre-existing and intentional, but using one
  tenant's established credential to act on another's behalf is not the same thing as two
  tenants choosing to share an account). This case stays **fully customer-driven**: the
  dashboard computes the merged `ExternalIds` and renders them as copy/paste parameters
  plus console instructions — deliberately no automated shortcut, because the customer
  manually performing this update **is** the proof-of-account-control that replaces what
  the script's own execution proves today. **Which template** those instructions point at
  is not always "use current template" — see "Never-downgrade on template swap" below;
  getting this wrong is the one way this flow can silently break a sibling infra instead
  of just failing loudly.

### 5a. Never-downgrade on template swap

The script's `EFFECTIVE_COMPUTE_TYPE` logic (`create_aws_role.sh` ~213-226) exists
because a shared role must never be narrowed back to `ecs_fargate` once any infra sharing
it is `eks` — doing so silently drops the EKS statements and the 7200s
`MaxSessionDuration` out from under a live sibling. Two complete templates (this design's
"Non-goals" choice over one parameterized template) reintroduces that exact hazard at the
*template-selection* layer instead of inside one script: pointing an "update existing
stack" instruction at `launchpad-deployment-role.ecs-fargate.template.json` when the
account already has an authenticated `eks` sibling would apply a **replacement**
`PolicyDocument` and `MaxSessionDuration` — CFN does not merge properties across a
template swap, it replaces them — breaking that sibling's `AssumeRole`/EKS provisioning
with no warning until it next tries to use the role.

Decided: the backend computes "which template to recommend" the same way the script
computes `EFFECTIVE_COMPUTE_TYPE` — the widest `compute_type` across every
`is_cloud_authenticated=True` sibling sharing this AWS account (`code`), including the
new infra being onboarded — never the new infra's own compute_type in isolation, and
never literally "whatever is currently applied." This is a backend computation exposed to
the dashboard (no new field; derived from existing `Infrastructure` rows each time
instructions are rendered), used for:
- the second-infra-onboarding instructions in this section (always recommend the `eks`
  template whenever any sibling, old or new, is `eks` — even if the infra actually being
  onboarded is itself `ecs_fargate`);
- the customer-initiated refresh flow above (a version-bump refresh must recommend
  whichever template already covers the widest compute type any sibling needs, not just
  "the next version of whatever this infra individually maps to").

Because the `launchpad:compute-types` tag is a static per-template literal (see
"Template generation" above — `eks`'s template already carries `ecs_fargate+eks`),
recommending the correct template is *sufficient*: there is no separate tag-value
computation to get right or wrong. What can still go wrong is the recommendation itself
(a bug recommends the narrower template anyway) — so the check that matters is not
"did some value decrease" but **"does the live tag still cover every sibling."** The
verify (`cfn-verify`) and refresh endpoints both re-read the role's
`launchpad:compute-types` tag after any update (through the already-assumed session —
see "Completing onboarding" above) and confirm it's a superset of every
`is_cloud_authenticated=True` sibling's own `compute_type` sharing the account. A gap
here is a structural impossibility if the recommendation logic above is correct, so a
check catching one means that logic has a bug, not that the customer did something
wrong — surfaced as a **500 with an alert**, never a 4xx telling the customer to fix
anything themselves.

### 6. Removal — the last-infra-in-an-account case

**The platform never calls `cloudformation:DeleteStack` on itself.**
`AWS::IAM::ManagedPolicy` depends on the Role it attaches to, so CFN detaches and deletes
the policy *before* deleting the role — the assumed-role session performing the delete
loses its own permissions mid-teardown. Result, if attempted: `DELETE_FAILED`, an
orphaned permissionless role, nuke's last step stuck forever. (The script path has the
same *latent* race today — `_delete_deployment_role_and_policy` detaches, deletes the
policy, then deletes the role, all on the role's own assumed credentials — three fast
sequential calls may usually win on IAM's propagation lag where CFN's slower, more
observable orchestration cannot. `plan/REAL-AWS-VALIDATION.md` has no entry for this step
at all — it has never been exercised on real AWS for either path. Out of scope to fix the
script's behavior here, but flagged as a pre-existing, shared, unverified risk — see Risks
below.)

Decided instead: `nuke_worker.py`'s CFN branch of `_manage_deployment_role`, when this is
the **last** infra in the account, does not attempt any IAM/CFN mutation at all. It
returns a normal **success** step whose `detail` carries `{"action":
"customer_delete_required", "console_url": "<stack info page for
infra.cfn_stack_name in CFN_STACK_REGION>"}` — built from the `cfn_stack_name` persisted
at verify time (see "Completing onboarding" above), never the assumed literal
`LaunchpadDeploymentRole`, since the customer may have renamed the stack. If
`cfn_stack_name` is unset (onboarded before this field existed, or the verify endpoint's
best-effort stack lookup never ran) the console URL falls back to IAM's own role page
rather than a broken CFN deep link — still enough for the customer to find and delete the
role's stack manually. From Launchpad's side there is genuinely nothing further it can
safely do — it has already decided never to self-revoke its own last credential.
`NukeWorker._notify_failure`'s sibling, the destroy-success notification, surfaces this
console URL so the customer has one clear manual step left: delete the CFN stack
themselves whenever they're ready. This is a **soft success with a trailing manual
step**, not a new blocking run-status — the step is still non-resumable and re-evaluates
"am I last in the account" on every nuke attempt, same as today.

For the **non-last** case, the CFN branch calls `cloudformation:UpdateStack` against
`infra.cfn_stack_name` in `CFN_STACK_REGION` with `ExternalIds` recomputed minus this
infra's id (the list is guaranteed non-empty here, since "last infra" is checked first
and short-circuits to the branch above — an empty-list `UpdateStack` is never attempted,
sidestepping whatever `MalformedPolicyDocument` behavior an empty `sts:ExternalId`
condition array might trigger, which `_remove_external_id_from_trust`'s existing
single-string `= []` branch for the script path has also never been real-AWS-validated),
passing `Capabilities=["CAPABILITY_NAMED_IAM"]` (required on every `UpdateStack` against
a stack with named IAM resources — there's no customer in the loop to click a console
checkbox for a system-initiated call, so the SDK call must supply it directly). This
call is asynchronous: the step polls `cloudformation:DescribeStacks` on a bounded
interval until the stack reaches a terminal status — `UPDATE_COMPLETE` is success;
`UPDATE_ROLLBACK_COMPLETE`/`UPDATE_FAILED` is a step failure surfaced like any other
nuke step error (not silently retried as a transient error, since a rollback here means
the update itself was rejected, not that it raced something); a bounded number of
`_IN_PROGRESS` polls before giving up is also a step failure rather than an indefinite
hang.

### 7. New model fields

- `Infrastructure.onboarding_method` — `CharField(max_length=20, choices=[("script",
  "script"), ("cloudformation", "cloudformation")], default="script")`.
- `Infrastructure.cfn_stack_name` — `CharField(max_length=128, null=True, blank=True)`.
  Written once, at verify time, from the `launchpad:cfn-stack-name` role tag (see
  "Completing onboarding" above); read by nuke's CFN branch and the refresh endpoint.
  Null for every script-onboarded row and for a CFN-chosen infra that hasn't completed
  verify yet.

Both are `AddField` with a `default=`/`null=True` — Django backfills every existing row
at migration time, no separate data migration needed. Purely additive; rollback is a
plain reverse migration dropping the columns, since no code path depends on their absence
and every read path treats a missing/default `onboarding_method` as `"script"` (today's
only method), which is correct for every pre-existing row.

`CFN_STACK_REGION` (see "Completing onboarding" above) is a plain Python constant, not a
model field or a setting — there is nothing per-infra or per-deployment to configure, by
design.

### 8. Frontend

- `lib/onboarding-cloudformation.ts` — new sibling to `onboarding-scripts.ts`, same
  pinning/override/misconfiguration idiom, S3-URL-shape validation instead of
  HTTPS-or-localhost, quick-create URL builder (`ExternalIds`,
  `LaunchpadPlatformAccountId`, `LaunchpadPlatformUser` query params, region fixed to
  `CFN_STACK_REGION` — never a customer-facing parameter, see "Completing onboarding"
  above).
- `app/dashboard/infrastructures/new/page.tsx` — a "CloudFormation" tab alongside the
  existing bootstrap snippet. Three states per target AWS account: fresh account →
  quick-create link; account has an authenticated CFN sibling → the manual
  "update existing stack" copy/paste instructions from section 5; account has an
  authenticated **script** sibling → tab disabled with a tooltip pointing at the script
  tab.
- `app/dashboard/infrastructures/[id]/page.tsx` — a "Verify connection" action for a
  CFN-chosen, not-yet-authenticated infra (replaces waiting on an inbound callback, the
  GUI-path equivalent of the script flow's existing "show setup command" recovery
  screen). The `policyState` banner's action button branches between the existing
  `PolicyRefreshDialog` (script) and a new `CfnRefreshDialog` (the change-set review
  link from section 5) based on `infra.onboarding_method`.

### 9. Rollout

Gated behind a new `CFN_ONBOARDING_ENABLED` flag (default off), following the exact
precedent `EKS_ENABLED` and `PLATFORM_BUILD_ENABLED` already set in this codebase for a
new onboarding/compute surface pending real-AWS validation. Flipped on in the final
ticket once the checklist below is clean — nobody who never opens the CFN tab is affected
either way, so this is a belt-and-suspenders kill switch, not a load-bearing rollout
mechanism.

## Risks & mitigations

- **Self-revocation ordering (last-infra teardown)** — covered above: the platform never
  calls `DeleteStack`; "customer_delete_required" is a success state with a manual
  follow-up, not a failure. *Residual risk*: the pre-existing script-path equivalent
  (`_delete_deployment_role_and_policy`) has never been real-AWS-validated either; flagged
  for whoever eventually runs that checklist item, not blocking F8.
- **Trust-policy drift from running the script against a CFN-managed role** — mitigated
  by the `launchpad:managed-by` tag check added to `create_aws_role.sh`'s EXISTING ROLE
  STATE section, and by the dashboard computing method-lock from authenticated siblings
  only (no silent cross-method writes from either surface).
- **Security/auth**: `ExternalId` stays mandatory (required CFN parameter, no bypass);
  the onboarding token never enters a CFN parameter, template, or any CloudTrail-visible
  surface; `CAPABILITY_NAMED_IAM` is required on every `CreateStack`/`UpdateStack` call
  because the role/policy names are fixed — this is a standard CFN console checkbox
  prompt, nothing new to build, but must be documented so customers aren't confused by it.
- **Data loss**: none — no data-plane resources are touched by this feature; the only
  mutable state is IAM trust/policy documents and one new additive DB column with a safe
  default.
- **Cost regressions**: negligible — one small S3 bucket (pennies/month); zero new
  customer-account billable resources beyond what the script already creates.
- **Performance regressions**: N/A — onboarding/refresh/removal are not hot paths; the
  new "Verify connection" endpoint is explicitly budget-limited the same way every other
  customer-account-calling endpoint is (F0).
- **Rollback path**: every change is additive and flag-gated (`CFN_ONBOARDING_ENABLED`);
  the script path is never modified in a way that changes its own behavior for existing
  customers, only extended with one new read (the tag check) that only fires for a role
  it doesn't currently recognize as its own.
- **Observability gap**: add an `audit.nuke`-style structured log entry for every
  `UpdateStack`/`CreateChangeSet` call this design makes (mirroring
  `nuke_worker.py`'s existing `audit_logger` pattern), and a dashboard-visible counter of
  CFN-vs-script onboarding adoption so method-lock behavior is observable in production,
  not just inferred from support tickets.
- **Known, accepted side effect**: the base-statement bump (the explicit
  `cloudformation:` action grant) flags every account, regardless of compute type *or*
  onboarding method, as policy-stale — including script-only accounts that will never
  need it. Accepted; see "Template generation" above.
- **Silent cross-infra downgrade**: two complete templates (not one parameterized
  template) reintroduces the script's "never narrow a shared role already serving a
  wider compute type" hazard at the template-selection layer — see "Never-downgrade on
  template swap" above. Mitigated by computing the recommended template from the widest
  compute type across authenticated siblings, never the acting infra's own type in
  isolation, plus a server-side guard that re-reads the live `launchpad:compute-types`
  role tag after any update and treats a sibling's compute_type no longer being covered
  as a 500/alert rather than a customer-facing error. Detection, not prevention,
  is the backstop if the computation itself has a bug — flagged honestly as a risk this
  design reduces but cannot structurally eliminate the way one parameterized template
  with an explicit "widen-only" transition would (rejected in Non-goals for a different
  reason: avoiding an unverified CFN list-conditional idiom).

## Execution plan

Each ticket is independently shippable and reviewable as one PR; 1→4 are prerequisites
for 3/5/6; 7 only needs 1-6 merged; 8-9 are sequential validation/rollout.

1. **`generate.py` + `policy.json` — CFN template renderer.** Two generated templates,
   explicit `cloudformation:` action-list base-statement addition (resource pattern
   `stack/*/*` — account *and* stack-name wildcarded, see "Template generation" above
   for the exact action list and why it's not `cloudformation:*`),
   `_cfn_statement` Fn::Sub helper, four role tags baked in
   (`launchpad:managed-by`, `launchpad:policy-version`, `launchpad:compute-types` — the
   F5/`live_diff.py` parity fix — and `launchpad:cfn-stack-name`), new
   `check-cfn-template` CI job (pinned `cfn-lint`, offline). Tests: zero-stray-placeholder
   assertion, ecs-fargate-template-has-no-`Fn::Sub` assertion, the `Fn::Sub` round-trip
   check against `policy_data.document(compute_type)`, round-trip `--check`/`--write`.
2. **Cross-path exclusivity + model.** `Infrastructure.onboarding_method` and
   `Infrastructure.cfn_stack_name` migration (both additive/nullable-or-defaulted).
   `create_aws_role.sh`'s tag-check refusal. Bump `NEXT_PUBLIC_LAUNCHPAD_SCRIPT_REF`
   (standing release action — v7 is a base bump, required for both compute types).
3. **Backend: refactor + verify + refresh endpoints.** Extract the existing
   claim→bounded-retry-AssumeRole→post-AssumeRole-tail block out of
   `infrastructure_onboarding_callback` into `InfrastructureService` (retry loop and
   token claim/release included — this is the one ticket most likely to accidentally
   regress onboarding if the retry or claim ordering is dropped during the extraction;
   the existing script-callback test suite must keep passing unmodified against the
   refactored method as the regression gate). New JWT-authenticated, F0-budgeted
   `cfn-verify` endpoint (claim → AssumeRole-with-retry → `ListRoleTags` →
   best-effort `DescribeStacks` → shared tail; writes `policy_version` from the
   `launchpad:policy-version` tag and `cfn_stack_name` from the
   `launchpad:cfn-stack-name` tag onto **every** `onboarding_method='cloudformation'`
   sibling sharing `code`, not just the calling infra). New CFN-refresh endpoint
   (widest-compute-type template selection from "Never-downgrade on template swap"
   above → `CreateChangeSet` + review-link response; same sibling-wide write on
   completion). Tests for success/role-not-tagged-cloudformation (400)/token-already-
   claimed (403)/IAM-propagation-retry/never-downgrade-template-selection/sibling-write
   paths. No test asserts on `is_cloud_authenticated` as a precondition — see
   "Completing onboarding" above for why that check was rejected.
4. **Platform hosting.** `infra/platform-templates/` Terraform (S3 bucket, versioned,
   Block Public Access allowing only `GetObject` on the templates prefix). Standing
   release action: conditional-PUT (`If-None-Match: *`) publish of both templates keyed
   by git ref. One-line addition to root `CLAUDE.md`'s "Two trees are the exception."
5. **Frontend.** `lib/onboarding-cloudformation.ts`, create-wizard CFN tab (three
   account states, always recommending the widest-compute-type template per "Never-
   downgrade on template swap" above — never "whichever template this new infra's own
   compute_type maps to" in isolation), detail-page "Verify connection" + `CfnRefreshDialog`,
   new pinned env vars.
6. **`nuke_worker.py` CFN branch.** Non-last-infra `UpdateStack` against
   `infra.cfn_stack_name` in `CFN_STACK_REGION` (direct, no change set,
   `CAPABILITY_NAMED_IAM`, polled to a terminal status); last-infra
   `customer_delete_required` success detail (with a `cfn_stack_name`-built console URL,
   falling back to the IAM role page if unset) + notification surfacing. Tests with a
   mocked `cloudformation` boto client for both branches, including an
   `UPDATE_ROLLBACK_COMPLETE` poll result surfacing as a step failure.
7. **Docs.** `docs/IAM_POLICIES.md` gains an "Automated Setup — CloudFormation" section
   (links to the hosted templates; notes the `launchpad:managed-by` exclusivity rule) and
   a note under "Revoking Access" that CFN-onboarded accounts should delete the stack,
   not just the role, for cleanup to be complete.
8. **Real-AWS validation** (appended to `plan/REAL-AWS-VALIDATION.md`):
   - Quick-create link opens the console pre-filled with correct parameters in
     `CFN_STACK_REGION`; `CAPABILITY_NAMED_IAM` prompt appears; stack creates
     successfully for both compute types **including with the suggested stack name
     edited to something else** — confirms `cfn-verify` discovers the real name via the
     `launchpad:cfn-stack-name` tag rather than assuming it.
   - The EKS template's `Fn::Sub`-rendered `${AWS::AccountId}` produces a policy document
     byte-identical (net of JSON encoding) to the script's `sed`-substituted equivalent.
   - `cfn-verify` on a freshly-`CREATE_COMPLETE` stack: confirm it succeeds without
     needing the retry (or, run it fast enough after stack completion to confirm the
     retry actually engages and still succeeds — whichever happens naturally).
   - Second infra, same account, same compute type: the manual "update existing stack"
     flow correctly merges `ExternalIds`; both infras' `AssumeRole` succeed afterward,
     and both rows' `policy_version` match after either one calls verify/refresh.
   - Second infra, same account, **wider** compute type (ecs_fargate account gains an eks
     infra): confirm the dashboard recommends the `eks` template (not "current template"),
     the resulting role has `MaxSessionDuration: 7200` and the EKS statements, the live
     `launchpad:compute-types` tag reads `ecs_fargate+eks`, and the *first* (ecs_fargate)
     infra's `AssumeRole` still succeeds unchanged afterward.
   - Confirm the exact change-set-review console URL query-string shape (section 5 flags
     this as unverified-by-design).
   - Refresh: bump to a throwaway next version, confirm `CreateChangeSet` + customer
     execution actually widens the live policy, confirm "Verify connection" (re-run)
     correctly advances `policy_version` (read from the role tag) on every sibling.
   - Nuke, non-last infra: `UpdateStack` (with `CAPABILITY_NAMED_IAM`, polled to
     `UPDATE_COMPLETE`) removes just that `ExternalId`; the surviving
     infra's `AssumeRole` still works.
   - Nuke, last infra: confirm the `customer_delete_required` detail/console URL is
     correct and that manually deleting the stack leaves nothing behind.
   - `create_aws_role.sh` against a real CFN-created role: confirm the
     `launchpad:managed-by` tag check actually refuses and prints the expected message.
   - Confirm `cloudformation:CreateStack`'s `TemplateURL` genuinely rejects a non-S3 URL
     (closes out the Non-goals claim definitively).
9. **Flip `CFN_ONBOARDING_ENABLED` default on**, once ticket 8 is clean — mirrors F7
   ticket 10's pattern exactly.

## Open questions

- **Should an account ever get a one-time "migrate script → CFN" import flow?** Not
  needed now (Non-goals), reversible later. Needs the owner's call once there's real
  adoption data showing customers actually want it — not derivable from the code or from
  first-principles design.
- **Should CloudFormation eventually become the *recommended* (not just offered) path,**
  demoting the script to a secondary/manual-setup option in the dashboard's default view?
  A product-priority call, not a technical one — this plan offers both with no
  preference, by design, per the task's framing ("offered alongside").
