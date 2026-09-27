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
