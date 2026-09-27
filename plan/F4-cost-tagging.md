# F4 — Per-app cost attribution

**Status:** done (mock-verified; see REAL-AWS-VALIDATION.md) · **Depends on:** nothing · **Blocked by:** nothing

**This is the only remaining item that gets worse by waiting.** Tags are not retroactive:
every deploy that happens before this ships produces resources that can never be
attributed. Cost Explorer also lags ~24h and tag activation is payer-only for org member
accounts, so the data does not start the day the tags do.

## Goal

Answer "what did this app cost last month" with actuals on ECS and clearly-labelled
estimates on EKS, without any customer cost data leaving the customer's account except as
aggregates Launchpad queries on demand.

## Verified on main at `9c8743d`

- **Zero `Tags=` anywhere in `application-service/aws/`.** No per-app resource carries a tag
  today. `grep -rn "Tags=" deployment-services/application-service/aws/` returns nothing.
- `policy.json` grants no `ce:` action (`"ce:"` count is 0).
- Per-app resource creation call sites, all untagged:
  - `aws/ecs.py:17` `create_task_definition`
  - `aws/ecs.py:152` `create_service`
  - `aws/alb.py:26` `create_target_group`
  - `aws/alb.py:55` `create_listener_rule`
  - `aws/codebuild.py` — the CodeBuild project and its IAM role
- Terraform's `default_tags` (in `infra/aws/providers.tf`) reaches only terraform-created
  resources: the shared VPC, ALB, cluster, ECR. It cannot reach anything above.

## Design

**ECS — real tags, real actuals.**
Tag every per-app resource with `launchpad:infra` and `launchpad:app`. On `create_service`
also set `enableECSManagedTags=True` and `propagateTags='SERVICE'` so the tags reach the
tasks, which is where the cost actually lands. Then query Cost Explorer grouped by those
tag keys.

**EKS — estimates, labelled as such.**
A pod is not a taggable AWS resource and Cost Explorer cannot see a Kubernetes namespace.
Estimate from pod resource requests × published Auto Mode pricing × runtime. Every number
derived this way must be labelled an estimate in the API response and in the UI — not in a
footnote.

**Infra-level** comes free from the existing `InfraID` default tag once activated.

**The `ce:` grant is a `policy.json` edit**, which bumps the policy to **v3**. That makes
this PR trigger the release step: `NEXT_PUBLIC_LAUNCHPAD_SCRIPT_REF` must move in the same
release or every customer is flagged stale and cannot clear it. `ce:GetCostAndUsage` has no
resource-level permissions — it is account-wide financial-data disclosure covering spend
unrelated to Launchpad. Say so plainly in the policy diff and the docs; do not wildcard it.

## Gaps found in review (now part of the design)

- **Existing apps.** Tags are added only at create time, so every app running today would
  stay untagged until redeployed — undermining the "worse by waiting" argument. Ship a
  one-off `tag_existing_app_resources` management command (idempotent; `tag_resource` on
  task definitions, services, target groups, listener rules, CodeBuild project) and call
  `update_service(propagateTags='SERVICE', enableECSManagedTags=True)` on existing
  services so tasks pick up tags at their next replacement.
- **Activation is needed in every account, not just org members.** Cost Explorer only
  groups by user-defined tags once they are activated, and a key becomes activatable ~24h
  after first use. Launchpad activates its own keys with
  `ce:UpdateCostAllocationTagsStatus`; in an org member account that call fails and the
  UI says so (infra-level only until the payer activates).
- **Cost Explorer charges the customer $0.01 per request.** Results are cached per
  infrastructure per day; the endpoint never calls CE on a cache hit.
- **Shared costs** (ALB, NAT gateway, EKS control plane, VPC) cannot be split per app. The
  response reports them as an explicit `shared` line so per-app figures never silently sum
  to less than the bill.
- **Customers still on v2** get `AccessDenied` on `ce:`. That maps to the existing
  `policy_refresh_required` 422 shape the dashboard already handles.

## Files

- `application-service/aws/ecs.py` — tags on task definition and service; managed tags +
  propagation on the service.
- `application-service/aws/alb.py` — tags on target group and listener rule.
- `application-service/aws/codebuild.py` — tags on the project and its role.
- `application-service/aws/` — a small shared `app_tags(infra_id, app_name)` helper so the
  key names exist once.
- `infrastructure-service/.../iam_policy/policy.json` — `ce:GetCostAndUsage`, version → 3.
- `infrastructure-service/api/services/cost_service.py` — new; CE query for ECS, estimator
  for EKS, source labelling.
- New owner-scoped endpoint + gateway route + dashboard panel.

## Tests

- Every per-app create call passes both tag keys (assert on the boto3 kwargs).
- `create_service` sets `enableECSManagedTags` and `propagateTags`.
- Cost service: estimate arithmetic; every EKS figure carries its `source: "estimate"`
  label; ECS figures carry `source: "actual"`.
- Policy: `ce:GetCostAndUsage` granted; version bumped; drift gate green.

## Security pre-review

**Not required for the tagging half** — it adds no new data path.

**Required before the cost endpoint ships.** `ce:` is account-wide financial disclosure,
and the endpoint is a new read surface. Apply the lessons already paid for: owner-only with
the two-step authz from `database_service.py`, no rate-limit exemption (the gateway does
not authenticate, so "owner-only" cannot justify one), and a bounded query window.

## Decisions

1. **Org member accounts:** infra-level only unless the payer activates; no payer
   onboarding step. The UI states which case applies.
2. **EKS:** show the labelled estimate. Hiding it gives EKS customers nothing; a labelled
   estimate is honest and replaceable when split cost allocation data is adopted.
3. **Grants in v3:** `ce:GetCostAndUsage`, `ce:UpdateCostAllocationTagsStatus`,
   `ce:ListCostAllocationTags` — in their own statement, with the account-wide caveat.
4. **CodeBuild project/role are tagged `launchpad:infra` only, never `launchpad:app`.**
   Verified on `main`: `_trigger_build` names both
   `launchpad-build-{infrastructure.id}` and `launchpad-codebuild-role-{infrastructure.id}`
   (`application_deployment_service.py:226,228`) — per-infrastructure, not per-app. Every
   app on that infra shares one project/role, so an app-level tag would attribute every
   other app's build minutes to whichever app happened to trigger the role/project's
   creation. Cost Explorer reports this spend on the infra-level `shared` line instead.
5. **`cost_service.py` lives in infrastructure-service, not application-service.**
   application-service has the full `Application` row (cpu/memory) this needs for the EKS
   estimate, but lacks `REDIS_HOST`/etc. in `core/settings.py` (`shared/ratelimit/budget.py`
   builds its connection pool at import time and would crash), and lacks the owner/invited
   authz repository, `authenticate_infrastructure`, and the `policy_refresh_required` 422
   convention already built for `database_service.py`. Extended the `Application`
   read-model application-service already replicates into infrastructure-service (via
   `application.created`/`application.updated` events) with `alloted_cpu`/`alloted_memory`
   instead — a smaller, more contained change than moving the whole service.
6. **`Application.status` is deliberately not replicated.** Deploy-time status
   transitions (`BUILDING`/`DEPLOYING`/`ACTIVE`/`FAILED`) are set directly on the row in
   `application_deployment_service.py` without publishing an event — only the explicit
   update endpoint does. A replicated `status` would silently go stale the moment a
   customer redeploys. The EKS estimate instead treats every non-deleted `Application` row
   (deletion *is* published, so the row is removed) as running for the full query window.
   Deliberately coarse; it's why every EKS figure is `source: "estimate"`.
7. **Cache is a DB model (`CostReport`), not Redis**, keyed on
   `(infrastructure, window_start, window_end)` with a `computed_on` date compared against
   today — a cache hit needs no TTL math, and rows from a previous day are pruned on the
   next write for that infra so a polled infra doesn't accumulate one row per day forever.
8. **Query window: `months` (1-3, default 1), a trailing 30-day block per unit** — not
   calendar months — ending today. Simpler arithmetic, and Cost Explorer's own billing
   period distinction (calendar month vs. rolling window) isn't a distinction this feature
   needs to get right.
9. **EKS pricing constants are AWS Fargate on-demand list prices, us-east-1, Linux/x86**,
   fetched from https://aws.amazon.com/fargate/pricing/ ($0.040478/vCPU-hour,
   $0.004446/GB-hour) and https://aws.amazon.com/eks/pricing/ ($0.10/cluster-hour,
   standard support) on 2026-09-27 — verify again before relying on these against a real
   account; they're settings (`COST_ESTIMATE_*`), not hardcoded, specifically so a stale
   figure is a config change, not a code change.
10. **Cost Explorer is queried at `region_name="us-east-1"`** regardless of the
    customer's resource region — it's a global service with a single regional API
    endpoint, not a per-region one.
11. **The `shared` line on ECS actuals currently covers only the CodeBuild project.**
    Verified on `main`: `infra/aws/providers.tf`'s `default_tags` is
    `Environment`/`Owner`/`Project`/`ManagedBy` — no infra-id tag exists on any
    terraform-managed resource (ALB, NAT gateway, VPC, ECS cluster). The plan's original
    text ("infra-level comes free from the existing `InfraID` default tag") does not match
    the code; the CE `Filter` on `launchpad:infra` therefore excludes those resources from
    the query entirely rather than folding them into `shared`. Tagging terraform's
    `default_tags` with the infra id is real follow-up work (touches every module, needs
    its own `terraform fmt`/`validate`/plan review) — out of scope for this PR's file list.
    The response says so explicitly via `shared.note` so the figure isn't mistaken for
    total non-per-app spend.
12. **Security pre-review (required by this file):** run via the `security-review` skill
    against the full diff. Verdict: **APPROVE**, no HIGH/MEDIUM findings. Owner-only authz,
    the `CostReport` cache key, the Cost Explorer `Filter`, and error-message content were
    all checked for cross-tenant leakage and came back clean — the assumed-role session is
    scoped to the customer's own account regardless. Two INFO items, both addressed: the
    `ce:*` grant is organization-wide if the customer onboards their AWS Organizations
    management (payer) account, not just that one account — `policy.json`'s note now says
    so explicitly; and `UpdateCostAllocationTagsStatus` is a billing-config write inside a
    GET, judged non-exploitable (owner-only, limited to the two `launchpad:*` keys,
    documented) and left as designed.

## Out of scope

CUR/Athena pipeline. Feeding cost into Stripe amounts. Retroactive attribution — it does
not exist and cannot be built.
