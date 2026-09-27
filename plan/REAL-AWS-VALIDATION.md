# Real-AWS validation checklist

Everything on this list was built and tested against mocks (`MODE=dev` /
`LAUNCHPAD_MOCK=1`, `Infrastructure.is_mock`, botocore `Stubber`). Mocks prove our code
calls AWS the way we think it should; they cannot prove AWS behaves the way we think it
does. Each item below is a claim only a real account can settle. **Every feature PR
appends its own items here.** Tick them when the dedicated accounts exist.

Accounts needed: one **customer** test account (runs `create_aws_role.sh`), one **platform
DNS** account (`infra/platform-dns`).

## Onboarding and policy

- [ ] `create_aws_role.sh` first-time bootstrap with `LAUNCHPAD_COMPUTE_TYPE=ecs_fargate`
      and `=eks`; callback records `policy_version`.
- [ ] Refresh from an older policy version to the current one; stale flag clears.
- [ ] `NEXT_PUBLIC_LAUNCHPAD_SCRIPT_REF` points at a commit containing the current policy
      version (EKS onboarding is broken if it predates #72).

## Platform DNS (#75)

- [ ] `terraform apply` in the DNS account; NS delegated; `dig NS launchpad.aklamaash.me`.
- [ ] Guard: a write two labels below the apex **succeeds**.
- [ ] Guard: a write at the apex is **denied**.
- [ ] Guard: a single-label write (`x.launchpad.aklamaash.me`) is **denied**.

## F3 rollback

- [ ] `ecr describe_images` on a tag the retention policy has actually expired returns
      `ImageNotFoundException` with the shape `ECRClient.image_exists` expects — mocks
      always answer "found", so this is unverified against real ECR.
- [ ] EKS: the `$APP_NAME-$RESOLVED_SHA` tag the buildspec pushes actually exists in ECR by
      the time `_deploy_to_eks` reads it (real CodeBuild push latency vs. the mock's
      instant, synchronous push).
- [ ] Rollback on a real ECS service: `update_service` + `forceNewDeployment` actually
      drains old tasks and starts new ones on the restored task definition; the
      `deploymentCircuitBreaker` rolls back automatically if the pinned image can no
      longer boot (e.g. a since-changed execution role).
- [ ] Rollback on a real EKS cluster: `patch_namespaced_deployment` triggers a real rolling
      update and `_wait_for_rollout` observes real `availableReplicas`, not the mock's
      always-ready state.
- [ ] End-to-end request latency of a rollback (task-definition register + service update +
      wait-for-stable + wait-for-target-healthy, or the EKS rollout wait) run synchronously
      on the deployment worker — confirm it comfortably finishes within
      `DEPLOYMENT_LOCK_TIMEOUT`/the lock heartbeat window under real AWS latencies, not just
      the mock's instant responses.
- [ ] A real GitHub webhook redelivery while `auto_deploy_paused` is set, followed by a
      real push after `Resume auto-deploy` — confirm the resumed deploy builds the latest
      push and not a stale `project_commit_hash`.

## F4 cost tagging

- [ ] Managed tags actually reach the Fargate **task**, not just the service: deploy an
      app, describe the running task, confirm `launchpad:infra`/`launchpad:app` are
      present (this is what `enableECSManagedTags`/`propagateTags='SERVICE'` claims to do
      — mocks can't verify propagation, only that the flags were sent).
- [ ] Cost Explorer actually groups by tag once activated: activate
      `launchpad:app`/`launchpad:infra` via the real callback flow, wait the documented
      ~24h lag, then `GetCostAndUsage` with `GroupBy=[{"Type":"TAG","Key":"launchpad:app"}]`
      returns non-empty groups. Also check `GetCostAndUsage` **before** a tag is active
      (or within 24h of first use): confirm AWS actually returns `ValidationException`
      in that case (mapped to 422 `cost_tags_not_activated` in `_actual_ecs_costs`) rather
      than a different code or an empty result — the mapping is unverified against real
      AWS.
- [ ] `UpdateCostAllocationTagsStatus` in a **standalone** account (Launchpad's call
      should succeed) vs an **AWS Organizations member** account (should fail with the
      payer-only error `cost_service._activate_cost_allocation_tags` maps to
      `reason: "payer_account_required"` — confirm the actual error code AWS returns
      matches `AccessDenied`/`AccessDeniedException` and isn't a third code this mapping
      misses). Also check what happens when a key was first used <24h ago — AWS may
      refuse activation with a distinct error the daily cache means Launchpad silently
      retries the next day; confirm that's the actual behavior, not a permanent failure.
      Also confirm `ListCostAllocationTags` actually reports `Active` once activation
      succeeds, so the skip-the-write fast path in `_activate_cost_allocation_tags`
      engages on the next cache miss instead of re-attempting the write every time.
- [ ] Terraform's `default_tags` now sets `launchpad:infra` (alongside the pre-existing
      `InfraID`) on every resource the provider creates, including inside child modules
      (vpc/ecs/alb/ecr) — confirm this against a real applied account: (a) a **new**
      infra's ALB/NAT gateway/VPC/ECS cluster carry `launchpad:infra` = the infra's full
      id immediately after apply; (b) an infra provisioned **before** this change picks up
      the tag via an in-place update on its next reconcile-apply, with terraform reporting
      it as a plain tag update (no resource replacement) in the plan; (c) once activated,
      `GetCostAndUsage` folds that spend into `cost_service`'s `shared` line, not just the
      CodeBuild project it covered before this fix.
- [ ] **Tenant isolation on a shared AWS account**: onboard two infrastructures into the
      *same* customer AWS account (two different `Infrastructure.id`s, one account),
      deploy an app under each, and confirm a `GetCostAndUsage` call filtered on one
      infra's `launchpad:infra` value returns only that infra's tagged spend — never the
      other infra's apps or shared resources. Mocks can't exercise this: the Filter is
      exercised in tests, but a real account is the only way to confirm Cost Explorer
      itself doesn't fold same-account spend together in a way the tag filter doesn't
      actually separate.
- [ ] `tag_existing_app_resources --dry-run` then for real against an infra with apps
      deployed before this feature; confirm tags land, then confirm a task replacement
      (`update_service` without `forceNewDeployment`) actually results in the *next*
      naturally-scheduled task picking up the tag rather than requiring a manual force.
- [ ] v2 → v3 policy refresh on a real customer account: `create_aws_role.sh` refresh
      installs the `ce:*` statement, `policy_version` updates to 3 on the callback, and a
      cost query that previously 422'd with `policy_refresh_required` now succeeds.

## F5 evidence pack

- [ ] `GetPolicyVersion.PolicyVersion.Document` and `GetRole.Role.AssumeRolePolicyDocument`
      encoding against a real account: botocore's `json_decode_policies` handler decodes
      both to a dict before boto3 returns them (confirmed empirically against a Stubber
      client), but `diff_live_policy`'s `_decode_policy_document` also handles a raw
      URL-encoded string defensively — confirm a real IAM response never reaches that
      fallback path in a way that changes the diff.
- [ ] Live drift against a real account after a manual policy edit in the AWS console
      (add a statement, remove an action from an existing one, delete the EKS Deny):
      confirm `missing_allows` / `missing_denies` / `extra_allows` / `extra_denies` match
      what was actually changed, and that removing the whole `LaunchpadDeploymentPolicy`
      attachment reports `policy_not_attached` rather than an unhandled error.
- [ ] Attach a second real managed policy (e.g. a narrow, harmless one) and add a real
      inline policy to `LaunchpadDeploymentRole`; confirm `other_policies` lists both and
      `identical` goes false, using the real `ListRolePolicies` response shape (mocks
      only exercise the Stubber's model-conformant shape).
- [ ] Trust policy shape on a real role created by `create_aws_role.sh`: confirm
      `Principal.AWS` is a plain ARN (not rewritten to an `AIDA...` unique id — AWS does
      this when the referenced IAM user is later deleted) and that the
      `LAUNCHPAD_ALLOW_NO_EXTERNAL_ID=1` escape-hatch shape (`Condition` entirely absent)
      round-trips through `_diff_trust_policy` as `external_id_present: false` rather than
      raising.
- [ ] Add a second `sts:AssumeRole` statement to a real role's trust policy (e.g. via a
      manual `UpdateAssumeRolePolicy` in the console) and confirm it surfaces in
      `extra_statements` and flips `identical` to false against a real `GetRole` response.
- [ ] `iam:SimulatePrincipalPolicy`-adjacent calls (`ListAttachedRolePolicies`,
      `ListRolePolicies`, `GetPolicy`, `GetPolicyVersion`, `GetRole`) against a role whose
      trust policy denies the platform principal: confirm `AccessDenied` surfaces as
      `policy.reason` / `trust_policy.reason` without leaking `Error.Message` (it carries
      the assumed-role session ARN) into the pack or the access log.
- [ ] `LAUNCHPAD_PLATFORM_PRINCIPAL_ARN` is set correctly in every real deployment env
      (it now has no default outside `MODE=dev` and the service refuses to start without
      it) — confirm the deploy pipeline sets it before this ships to an environment that
      isn't dev.
