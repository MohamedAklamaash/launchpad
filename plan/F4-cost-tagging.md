# F4 — Per-app cost attribution

**Status:** not started · **Depends on:** nothing · **Blocked by:** nothing

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

## Open questions

1. **Tag activation for org member accounts.** Activating a cost allocation tag is a
   *payer-account* setting that Launchpad's role cannot reach. Ship "infra-level only
   unless the payer activates", or add a payer onboarding step?
2. Is a labelled estimate acceptable for EKS, or should EKS simply show no per-app cost
   until split cost allocation data is available?

## Out of scope

CUR/Athena pipeline. Feeding cost into Stripe amounts. Retroactive attribution — it does
not exist and cannot be built.
