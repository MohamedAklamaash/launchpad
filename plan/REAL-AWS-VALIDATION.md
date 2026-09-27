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

## F1b part 2 — TLS activation

- [ ] **ACM issuance time.** `RequestCertificate` → `DomainValidationOptions[].ResourceRecord`
      appearing is assumed near-instant (the ~2min bound in `cert_bootstrap._poll_for_resource_record`
      is generous); confirm the real latency and that it never exceeds the bound under
      normal conditions — a bound that's routinely hit would silently strand every first
      provision in `tls_status=PENDING` for no real reason.
- [ ] **Validation record shape.** `naming.VALIDATION_LEAF_RE`/`VALIDATION_VALUE_RE`
      (`_<hex>.{domain}.` / `_<32 hex>.<alnum>.acm-validations.aws.`) are inferred from
      published ACM documentation, not observed against a real `DescribeCertificate`
      response — confirm the exact `Name`/`Value` shapes for a real DNS-validated wildcard
      cert, including whether the leaf is always exactly 32 hex characters or can vary.
- [ ] **`acm:RequestCertificate` with `aws:RequestTag` condition.** Confirm ACM actually
      enforces `aws:RequestTag/ManagedBy=launchpad` on `RequestCertificate` (some AWS
      services only support `aws:RequestTag` on resource-creating calls that accept `Tags`
      inline, which `RequestCertificate` does) and that `acm:DeleteCertificate`'s
      `aws:ResourceTag` condition evaluates against tags set via that same call, not only
      via a separate `AddTagsToCertificate`.
- [ ] **Certificate reuse.** `list_certificates` + `list_tags_for_certificate` filtering by
      `DomainName` + `ManagedBy=launchpad` tag — confirm `ListCertificates` supports enough
      certificates per account for this scan to stay cheap, and that a `PENDING_VALIDATION`
      certificate whose validation never completed is still returned (not filtered out by
      some default ACM behavior).
- [ ] **certificateARNs in EKS Auto Mode.** `IngressClassParams.spec` accepting a
      `certificateARNs` list and `listenPorts` — confirmed against AWS's EKS Auto Mode ALB
      documentation, not yet exercised against a real cluster from this codebase (the EKS
      per-app Ingress host-rule wiring itself is scaffolded, not connected to the deploy
      path — see plan/F1b-tls-activation.md's Deferred section). Confirm a patch to an
      existing `IngressClassParams` object (adding `certificateARNs` after first bootstrap,
      once TLS is issued) takes effect without recreating the ALB.
- [ ] **TLS1.3 policy on ALB.** `ELBSecurityPolicy-TLS13-1-2-2021-06` is assumed to exist
      and be selectable via `aws_lb_listener.ssl_policy` in every region Launchpad supports —
      confirm against a real `elbv2:CreateListener`/apply in more than one region.
- [ ] **`modify_target_group` health-path cutover without downtime.** Confirm ALB applies a
      `HealthCheckPath` change to a target group with already-healthy targets without a
      health-check gap that would flip targets to unhealthy mid-cutover (the intended
      sequence: nginx redeploys with the new location *before* `modify_target_group` runs,
      but the two are not one atomic operation).
- [ ] **Host rule priorities and limits.** Confirm the per-listener rule limit (AWS default
      quota is commonly cited as 100 rules per listener, adjustable) is enough headroom for
      `create_host_forward_rule`/`create_host_redirect_rule` alongside the existing
      per-app path rules on the same ALB, and that `get_next_priority`'s gap-filling scan
      stays cheap at that rule count.
- [ ] **SNI cap.** ALB's per-listener SNI certificate limit (commonly cited as 25,
      `add_listener_certificates`) is relevant once custom domains (F1b part 3) attach
      additional SNI certs to the same 443 listener the wildcard cert already serves —
      confirm the real limit and that `describe_listener_certificates` reflects the
      wildcard cert set at `CreateListener`/`aws_lb_listener.certificate_arn` alongside any
      later `add_listener_certificates` additions, not as a separate, uncounted slot.
- [ ] **ACM condition keys for RequestCertificate.** Security review R3 asked for
      `acm:ValidationMethod`/`acm:DomainNames` conditions scoping `RequestCertificate` to
      DNS validation and the platform base domain. Deliberately NOT added to policy.json:
      AWS's published IAM reference for ACM does not document any ACM-specific condition
      keys (only the global `aws:*` ones), and adding a condition key that never appears in
      the request context evaluates to false — for an Allow statement that means the
      action is denied outright, which would break cert bootstrap entirely rather than
      narrow it. Confirm against the current AWS IAM JSON policy reference for `acm:*`
      (or an actual `iam:SimulatePrincipalPolicy` call) whether such keys exist before
      ever adding them; do not guess.
- [ ] **RequestCertificate tag-on-create vs. AddTagsToCertificate.** `cert_bootstrap.py`
      passes `Tags` directly on `RequestCertificate`. AWS documents this as requiring
      `acm:AddTagsToCertificate` permission in addition to `acm:RequestCertificate` (tagging
      on create is implemented as an implicit `AddTagsToCertificate` call) — the policy
      already grants both, so this should work, but has not been confirmed against a real
      `RequestCertificate` call with `Tags` and *only* the v4 grant set (no broader
      `acm:*`). If it turns out tag-on-create is NOT covered by a conditioned
      `AddTagsToCertificate` grant the way it is by an unconditioned one, certificates
      would come back untagged and reuse/delete would silently never find them.
- [ ] **ALB overwrites the client's own X-Forwarded-Proto on :80.** The :80 listener's
      host-header redirect rule (`create_host_redirect_rule`) assumes a client hitting the
      app's own hostname over plain HTTP should always be redirected to HTTPS — true for
      an external client, but the ALB itself sets `X-Forwarded-Proto` on every request it
      forwards (overwriting anything the original client sent), so nginx's
      `$http_x_forwarded_proto` in host mode is trustworthy specifically because it always
      reflects the ALB's own view (http on :80, https on :443), never a value a client
      could spoof by setting the header directly *if* the ALB is the only path in —
      confirm no other ingress path (e.g. a customer VPC route hairpinning traffic
      directly to a target's IP, bypassing the ALB) exists in any supported topology.
- [ ] **EKS in-cluster spoofing of X-Forwarded-Proto to the sidecar.** On EKS, the AWS
      Load Balancer Controller sets `X-Forwarded-Proto` the same way as ECS's ALB, but the
      request path from the controller-managed ALB to the pod runs over the cluster's own
      network (not necessarily as tightly closed as an ECS task's loopback-only nginx
      sidecar setup) — confirm no other in-cluster caller (another pod, a NetworkPolicy
      gap) can reach the app's nginx sidecar directly and spoof `X-Forwarded-Proto` without
      going through the ALB at all, which would let it claim an HTTP request is HTTPS.

## F1b part 3a — host URLs

- [ ] **`GetChange` propagation time in practice.** `converge.py`'s bounded poll
      (`_SYNC_POLL_ATTEMPTS=6` × `_SYNC_POLL_INTERVAL_SECONDS=10` ≈ 60s) is a guess at how
      long real Route53 UPSERT/DELETE changes typically take to reach INSYNC. Confirm the
      real distribution (AWS docs describe INSYNC as "typically" under a minute but give no
      hard bound) and whether the poll needs lengthening, or whether the "next reconcile
      catches up any still-PENDING row" fallback (`_stamp_pending_syncs`) is exercised often
      enough in practice that the poll bound barely matters. If a real infra is observed
      sitting with `dns_synced=False` for an extended period with no further reconcile ever
      triggered, that is exactly the documented gap — build the periodic sweep this file's
      F1b part 3a plan section flags as a candidate follow-up, do not just extend the bound.
- [ ] **`set_rule_priorities` and the priority-1 reservation.** `ALBClient.
      ensure_host_redirect_rule` creates the wildcard `:80` redirect at whatever priority
      `get_next_priority` returns, then swaps it to `1` via one `set_rule_priorities` call
      (also moving whatever rule held `1` to the vacated slot). Confirm this API call is
      genuinely atomic against a real listener under concurrent rule creation (two apps on
      the same infra deploying into host mode for the first time at nearly the same moment,
      each racing to become the one that creates the shared rule) — the code takes the
      per-listener lock (`_get_listener_lock`) around the whole
      describe-then-create-then-swap sequence, which should already serialize this within
      one process, but confirm across the `MAX_PROVISION_WORKERS`/multi-worker-process
      topology this runs under in production, not just within one Python process.
- [ ] **ALB per-listener rule limit headroom with the wildcard redirect.** Re-confirm the
      part 2 "Host rule priorities and limits" item above now that `:80` carries one
      wildcard redirect rule per infra (not one per app) plus the existing per-app path
      rules — this should only ever *improve* headroom versus what part 2 flagged, but
      confirm the actual count against a real infra with many apps.
- [ ] **EKS `IngressClassParams` merge-patch semantics.** `apply_eks_tls`'s
      `patch_cluster_custom_object` call is assumed to merge `spec.certificateARNs`/
      `spec.listenPorts` into the existing object (created by part 2's
      `_ensure_ingress_class` with only `spec.scheme`/`spec.group`) via a strategic/JSON
      merge patch, not replace the whole `spec`. Confirm against a real EKS Auto Mode
      cluster that `group.name` (the ALB Auto Mode group every app's Ingress shares) survives
      this patch unchanged, and that the ALB actually starts serving `:443` with the patched
      certificate without a control-plane restart or manual `kubectl rollout` of anything.
- [ ] **EKS Ingress host-rule interaction with the shared ALB group.** Every app's Ingress in
      one infra shares one ALB (via `IngressClassParams.spec.group.name`). Confirm the AWS
      Load Balancer Controller correctly merges each app's own `host=` rule from its own
      Ingress object into that one shared ALB's listener rules — i.e. that two apps on the
      same infra, each with their own exact-host Ingress rule, do not collide or shadow one
      another the way an accidental wildcard or path overlap would.
- [ ] **B1 (security review, revised): per-Ingress `listen-ports` honoured inside a shared
      `IngressGroup` in EKS Auto Mode.** The first B1 fix attempt (`listenPorts:
      [{HTTP:80},{HTTPS:443}]` on the shared `IngressClassParams` plus an out-of-band boto3
      `:80` redirect rule to intercept the resulting plaintext forward) was rejected by a
      second review round: that `:80` listener is owned by the AWS Load Balancer
      Controller, which reconciles it independently of any manually created rule, so nothing
      guaranteed the redirect survived the Ingress apply that ran right after it. The final
      design instead gives each app's host rule its own Ingress (`{slug}-host`,
      `EKSDeployer._host_ingress_manifest`) in the same `IngressGroup` as the existing path
      Ingress, scoped to `HTTPS: 443` only via the per-Ingress annotation
      `alb.ingress.kubernetes.io/listen-ports: '[{"HTTPS": 443}]'`. **This item is the crux
      of the fix and is completely unverified against a real cluster:** confirm the
      controller actually scopes an individual Ingress's own rules to the listen ports THAT
      Ingress declares, rather than reconciling every member of a shared group onto the
      union of every declared port (which would put the host rule back on :80 despite the
      annotation). `EKS_HOST_MODE_ENABLED` (default off, `application-service`) gates host
      mode on EKS entirely until this is confirmed. Note: `apply_eks_tls` was found during
      this same round to also set `spec.listenPorts` at the `IngressClassParams` (class)
      level, and the AWS Load Balancer Controller documents class-level fields as
      overriding the per-Ingress annotation — which would have made this whole fix inert.
      It now sets only `certificateARNs`, and both Ingresses (path and host) carry their
      own explicit `listen-ports` annotation, so confirming this item also means confirming
      there is no other class-level or cluster-wide `listenPorts` source anywhere else in
      the stack that could reintroduce the same conflict.
      **`MockClient.describe_listeners` (application-service `api/mock/mock_session.py`)
      unconditionally returns both a `:80` and a `:443` listener, so no mock-mode test can
      exercise the controller actually withholding or creating either — every assertion
      about listen-port scoping and precedence in this item is real-cluster-only.**
- [ ] **B1: `apply_eks_tls` is what must first establish the group ALB's `:443` listener,
      ahead of any app.** Removing `listenPorts` from the `IngressClassParams` patch
      (previous item) left nothing else to declare `:443` before an app's own host Ingress
      does — but `EKSDeployer._verify_eks_https_listener` refuses to grant host mode until a
      `:443` listener already exists, which would deadlock every EKS infra permanently once
      `EKS_HOST_MODE_ENABLED` is turned on (nothing would ever create the first `:443`
      listener for a check that requires one to already exist). A first attempt put
      `HTTPS: 443` directly on the bootstrap Ingress at bootstrap-creation time
      (`_ensure_bootstrap_ingress`) — caught before push: that runs before any certificate
      exists, and an ALB HTTPS listener cannot be created without one, which would time out
      EKS cluster bootstrap entirely, not just host mode. **Fixed instead in
      `apply_eks_tls`**, the point where a certificate is actually known to exist: it patches
      `certificateARNs` onto the class, then patches the bootstrap Ingress's `listen-ports`
      annotation to `'[{"HTTP": 80}, {"HTTPS": 443}]'`, class first so the certificate is
      already resolvable when the Ingress patch triggers the controller's reconcile of it.
      Confirm against a real cluster: (a) this patch sequence actually provisions a working
      `:443` listener with no further manual intervention, and that patching the class before
      the Ingress (rather than the reverse, or both in one call) is sufficient ordering — the
      k8s API gives no cross-object transactional guarantee here; (b) a cluster that already
      has a `bootstrap` Ingress from before this fix shipped receives the annotation patch
      correctly (`_get_or_create` never updates on creation, but `apply_eks_tls`'s
      `patch_namespaced_ingress` call is a normal patch, not a create, so this should not be
      affected by that — confirm it isn't); (c) the bootstrap Ingress's `default_backend` (an
      empty-selector Service with no real pods) returns something other than a silent hang
      for unmatched `:443` traffic — likely a 503 given no healthy targets, not the
      fixed-404 ECS's terraform listener returns, which is an acceptable but not identical
      default action and should be confirmed as such, not assumed.
- [ ] **B1: no `:80` rule for the host hostname appears on the shared ALB after a reconcile.**
      With the host Ingress scoped to `HTTPS: 443` only, deploy an app into EKS host mode
      against a real cluster and inspect the shared group ALB's `:80` listener rules
      (`DescribeRules`) both immediately after the apply and after triggering at least one
      more reconcile (e.g. deploying a second app, or an unrelated Ingress change in the same
      group) — confirm no rule matching the host's exact hostname is ever created on `:80`,
      at any point, not just at first apply. If one appears, `EKS_HOST_MODE_ENABLED` must
      stay off until the controller version/annotation combination in use is confirmed not
      to leak the rule onto `:80`.
- [ ] **B1: `:443` host routing actually works end to end.** With the host Ingress applied
      against a real cluster and the shared ALB's `:443` listener carrying the issued
      certificate (part 2's `apply_eks_tls`), confirm an HTTPS request to the app's exact
      hostname (`{slug}.{dns_label}.{base}`) reaches the app's nginx sidecar and gets the
      expected response, and that the existing path URL
      (`http://{alb_dns}/{slug}`) on the SAME shared ALB keeps working unaffected — the two
      Ingresses in one group must not shadow or otherwise interfere with each other.
- [ ] **`_apply_eks_tls_for_issued_certs` retries every ~30s tick indefinitely on a
      transient failure.** Unlike the ECS re-enqueue sweep (which only calls
      `InfraQueue.enqueue_provision`, cheap and deduped), a stuck EKS infra whose
      `apply_eks_tls` call fails transiently (unreachable cluster endpoint, a k8s API
      throttle) gets a fresh `assume_role_credentials_only` + `describe_cluster` + patch
      attempt on every tick with no backoff or age-based give-up, unlike the PENDING-cert
      loop's `ISSUED_CHECK_TIMEOUT`. Confirm this is an acceptable customer-account call
      rate in practice, or add the same age-gated give-up the PENDING loop uses.
- [ ] **`describe_task_definition`-free backfill correctness.** `backfill_host_routing`
      re-registers a task definition from a `Deployment` row's `image_tag`/`image_digest`
      rather than reading the currently-registered task definition back from ECS — confirm
      this never silently changes cpu/memory/port/env away from what is actually running
      when the row it picks is the most recent SUCCEEDED one (it should always match
      `application`'s current fields for a normal, non-rolled-back app, but confirm against
      an app that was rolled back and then had its `alloted_cpu`/`alloted_memory` edited
      without a subsequent deploy — an edge case this command does not special-case).
