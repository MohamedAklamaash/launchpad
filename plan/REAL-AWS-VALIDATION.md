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

## F1b part 1 — DNS writer

- [ ] `route53:ChangeResourceRecordSetsRecordTypes` behaves as documented: an otherwise-legal
      two-label CNAME write **succeeds**; the identical name with `Type=NS` is **denied**.
      (README's verification snippet covers this; run it before trusting the condition.)
- [ ] Wildcard UPSERT: `Name=\052.<label>.launchpad.aklamaash.me` (the `\052`-escaped wire
      form, matching `*.*.{base}` in the IAM condition) **succeeds** — confirms
      `route53:ChangeResourceRecordSetsNormalizedRecordNames` normalizes the escaped
      wildcard label the way `naming.denormalize_for_route53` assumes, not e.g. leaving it
      as literal `\052` text for the condition match.
- [ ] `ns.<label>.launchpad.aklamaash.me` (two labels below the apex, type `NS`) is
      **denied** — the record-type condition, not just the name-shape one, is what blocks
      it; a real account is the only way to confirm both conditions combine with AND
      semantics as IAM's default (all conditions on a statement must hold) rather than OR.
- [ ] A `TXT` record at the zone apex is **denied** (name-shape condition) independent of
      the record-type condition above.
- [ ] CAA at the apex is visible externally: `dig CAA launchpad.aklamaash.me +short`
      returns the `issue`/`issuewild "amazon.com"` records, and ACM in a customer account
      can still issue a certificate for `*.<label>.launchpad.aklamaash.me` (CAA does not
      accidentally block Amazon's own CA).
- [ ] CloudTrail → EventBridge alert fires end-to-end: attempt a denied
      `ChangeResourceRecordSets` (e.g. the apex TXT case above), confirm an event lands on
      `aws_cloudtrail.platform_dns`, the `dns_write_denied` rule matches it, and a message
      reaches a subscriber on `dns_write_denied_topic_arn`. Unverified: the exact
      `errorCode` string CloudTrail records for this specific IAM-conditional denial
      (assumed to be prefixed `AccessDenied`, matched with an EventBridge `prefix`
      matcher) and the delivery latency (CloudTrail → EventBridge is typically well under
      15 minutes but is not instantaneous — do not test with a short timeout).
- [ ] `sts:GetCallerIdentity` from the writer's actual credential returns an `Arn` ending
      in `user/launchpad-platform-dns-writer` and `Account` equal to the DNS account id —
      confirms `assert_caller_identity`'s suffix/account check matches the real ARN shape
      (assumed, not yet observed against a real STS response for an IAM user, as opposed
      to an assumed role).
- [ ] Route53 propagation/latency: confirm `request_and_await_dns_teardown`'s default
      20s timeout is enough for a real `change_resource_record_sets` call to return
      `ChangeInfo.Status` (it does not need to reach `INSYNC`, only for the API call
      itself to complete) — mocks return instantly and cannot exercise this.

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

## F2 runtime logs

- [ ] `filter_log_events` with `logStreamNames` against a real ECS log group: confirm the
      request-response shape (mocks assume `events`/`nextToken` only; verify
      `searchedLogStreams` and any other fields don't need handling) and confirm interleaved
      ordering across streams matches what `_cap_events`'s timestamp sort assumes.
- [ ] STOPPED-task log retention vs the 60-minute max window: a STOPPED task's CloudWatch
      stream persists past the task's own ECS visibility window, but confirm no surprise
      (e.g. log group deletion racing a still-STOPPED task) truncates it before that.
- [ ] `list_tasks` STOPPED-task visibility window (documented as up to ~1 hour, but verify
      empirically): confirms whether a very recently stopped task is still discoverable, and
      how quickly it stops being returned — this is what the `app.created_at` clamp is a
      backstop for, not a substitute for measuring the real window.
- [ ] `FilterLogEvents` / `ListTasks` visibility in the customer's own CloudTrail — confirm
      both actions are logged as issued by `LaunchpadDeploymentRole` and are attributable to
      this feature (not indistinguishable from other role usage) if that ever matters to a
      customer's audit.
- [ ] EKS: confirm `read_namespaced_pod_log` and `list_namespaced_pod` through the assumed
      `{cluster}-deploy` role are visible in the cluster's own Kubernetes/EKS audit logs (only
      if the customer has audit logging enabled) — the docs statement claims this is possible
      but unverified against a real cluster.
- [ ] End-to-end timeout budget: with a real `AssumeRole` (network round trip, not the mock's
      instant return) added on top of the dedicated `RUNTIME_LOGS_BOTO_CONFIG`
      (connect=2s/read=4s, 2 retries) and up to two `list_tasks` calls plus one
      `filter_log_events` call, confirm the total stays under the gateway's 10s proxy timeout
      with real network latency, not just under the code's own 6s deadline check.
- [ ] Confirm `ecs:*` / `logs:*` in the existing policy actually cover `ecs:ListTasks` and
      `logs:FilterLogEvents` with no `NotAction`/condition carve-out narrowing them for this
      use — checked against `policy.json`'s statement shape here, not against a real
      `iam:SimulatePrincipalPolicy` call.

## F6 exit export

- [ ] `terraform init` (with a real backend, not `-backend=false`) against the bundled
      `terraform/main.tf` + `terraform/backend.hcl` for an infrastructure whose Terraform
      apply actually ran: confirm it picks up the real remote state with zero drift on a
      subsequent `terraform plan` for everything except the (deliberately blanked)
      `db_app_sg_id` on a managed-database infra — mocks and the sandbox `-backend=false`
      validate in `test_exit_export_terraform.py` only prove the HCL is syntactically valid
      and the module graph resolves, never that it matches a real state file.
- [ ] `db_app_sg_id = ""` on the generated database module block(s): confirm the customer
      can actually find and fill in the right security group id from
      `app_security_group_name()`'s deterministic naming via a real
      `describe-security-groups` call, and that `terraform apply` with that value filled in
      does not attempt to replace the existing RDS/ElastiCache/DocDB resource (i.e. the
      only diff is the previously-blank attribute, not a forced replacement).
- [ ] Real ECS/EKS ARNs: confirm `Application.task_definition_arn` /`service_arn`/
      `target_group_arn`/`listener_rule_arn` and `Environment.cluster_arn`/`alb_arn`/
      `ecr_repository_url` are all still valid, resolvable ARNs at export time for a
      long-lived infrastructure (not stale from an earlier, since-replaced resource) —
      this feature trusts these DB columns rather than a live `describe_*` call, by design
      (H6: strictly read-only), so their staleness is a real, not just theoretical, risk
      this checklist should catch before relying on the README for anything but pointers.
- [ ] CodeBuild project/role ARNs (`launchpad-build-{infra_id}` /
      `launchpad-codebuild-role-{infra_id}`) are computed from the same deterministic
      naming `application_deployment_service.py._trigger_build` uses, never fetched —
      confirm they resolve to the real project/role in a real account for an infra that
      has actually deployed at least once.
- [ ] EKS namespace/object names in the README (from `Application.runtime_refs`) still
      match what's live in the cluster — confirm no manual `kubectl` cleanup or a partial
      failed deploy has left `runtime_refs` pointing at objects that no longer exist.
- [ ] Real `request_and_await_dns_teardown` timing for "Complete exit": confirm the
      `EXIT_COMPLETE_DNS_TEARDOWN_TIMEOUT_SECONDS` (default 7s, chosen to fit under the
      gateway's fixed 10s proxy timeout) is actually enough for the platform DNS writer to
      converge a real Route53 change end to end for a typical infra, not just the
      in-memory ledger check mocks exercise instantly — F1b's own checklist item on
      `request_and_await_dns_teardown`'s 20s default is the closest existing data point,
      and this feature calls it with a shorter budget than that default.
- [ ] The same-origin `APPLICATION_SERVICE_URL` call from infrastructure-service to
      application-service's `export-inventory` endpoint under real network latency
      (not two Django dev servers on localhost): confirm the whole request — internal
      hop plus archive assembly — comfortably stays under the gateway's 10s proxy timeout.
