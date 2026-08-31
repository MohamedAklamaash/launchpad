# EKS deployment target — implementation status

Working handoff for `feat/eks-deployment-target`. Delete before merge.

- **Plan**: `docs/EKS_DEPLOYMENT_TARGET_PLAN.md` (authoritative; includes the security
  review that returned BLOCK and the finding-by-finding resolution table)
- **Draft PR**: https://github.com/MohamedAklamaash/launchpad/pull/65
- **Branch**: `feat/eks-deployment-target`, based on `acc04df`
- **Last verified**: application-service **76 passed**, infrastructure-service **141 passed**,
  `compileall` clean over `deployment-services` + `gateway-service`.
  Test runner is `deployment-services/venv/bin/python -m pytest` (no `pytest` on PATH).

## Done — all 5 phases implemented

| Phase | Content | State |
|---|---|---|
| 1 Plumbing | `ComputeType` enum, `Infrastructure.compute_type` both services (immutable, `EKS_ENABLED`-gated at create **and** at provision dispatch), producer/consumer payloads, gateway body, migrations 0017/0023 | committed `dd7d7cd` |
| 2 Onboarding + STS | scoped+gated EKS IAM in `create_aws_role.sh`, `DurationSeconds=7200` w/ 3600 fallback, **credential persistence removed at both writers**, purge migrations 0018/0024 | committed `dd7d7cd` |
| 3 Provision/destroy | `shared/k8s/{token,client}.py`, `modules/eks` (Auto Mode), vpc `enable_elb_subnet_tags`, per-orchestrator config builders, `eks_bootstrap.py`, `eks_teardown.py`, `kubernetes==31.0.0` | committed `dd7d7cd` |
| 5 Surface | frontend compute selector + EKS CPU/memory, notification buckets, `check-terraform` CI job, docs incl. stale-`infra/aws/` fix | committed `dd7d7cd` |
| 4 Deploy | `EKSDeployer`, `aws/eks.py`, `aws/container_config.py`, `api/common/naming.py`, `mock_k8s.py`, migrations 0025/0026, 6 new test files | **UNCOMMITTED in working tree** |

### Commit Phase 4 first thing
```
git add -A && git commit   # see suggested message at the bottom
git push
```

## Security must-fixes — where each landed

C1 scoped EKS IAM + Deny on access-entry APIs for non-`infra-*` → `app_scripts/create_aws_role.sh`
(verified: renders correctly, ECS document byte-identical to HEAD).
C2 restricted `public_access_cidrs` + audit logs → `modules/eks`, hard-refusal in Python.
C3 CNI network-policy controller + NodeClass → `eks_bootstrap.py`; per-namespace policies in `deployer.py`.
C4 namespace-per-app + slug uniqueness scoped to infrastructure → `deployer.py`, migration 0026.
H1 fail-safe ALB reap (positive signal only + VpcId predicate + batch bound) → `eks_teardown.py`.
H2 both credential writers dropped + purge migrations → `authenticate.py`, `aws/session.py`.
H3 select on model column only, `cluster_version` allowlist, subprocess `env=` allowlist → `terraform_worker.py`.
H4 all three teardown entry points → `terraform_worker.py` (dispatch + inline rollback) and
`infrastructure.py::delete_infrastructure` (wired by hand, refuses delete if reap fails).
H5 `expires_in=60` + signed `x-k8s-aws-id` → `shared/k8s/token.py`, asserted in `test_k8s_token.py`.
M1 `EKS_ENABLED` at dispatch → `run_worker.py`. M2 split access entries → `modules/eks`.
M3 pod security → `deployer.py`. M4 no captured-group interpolation → `infra-email.template.ts`.

## Remaining work

1. **Commit + push Phase 4**, update PR body (drop the "Phase 4 still landing" line).
2. **ruff** — Phase 4 was killed *during* its ruff check; never completed. Note the repo-wide
   run reports ~449 findings on **unmodified main** (local ruff 0.16.5 is much stricter than
   whatever CI last ran; CI does an unpinned `pip install ruff`). Compare per-file against a
   HEAD baseline rather than reading the raw count.
3. **Pipeline not yet run**: `reviewer` on the full diff, then `security-auditor` (must
   re-check C1/C2/H1/H5 *as implemented* vs. promised — they were BLOCK-level and are now
   code written by agents that only read the plan).
4. **Runtime verification never done.** No flow has been exercised. Use `MODE=dev` /
   `LAUNCHPAD_MOCK=1` mock mode + the local multi-tenant harness for
   onboarding→provision→deploy→destroy on an EKS infra. Workers must be started via
   `app_scripts/start-workers.sh` — they die if backgrounded from a tool call.
5. **`terraform validate`** was run by the Phase 3 agent using a terraform binary it downloaded
   into its scratchpad; terraform is not installed on this machine. Re-verify if that matters.

## Decisions taken (do not re-litigate)

- **`EKS_ENABLED` stays `False` in code.** The plan gates the flip on a real-mode sandbox
  smoke test that cannot be run here. It is env-driven, so this is opt-in-by-operator.
- **ECR `imageTagMutability = IMMUTABLE` deliberately NOT implemented** (audit LOW). The
  buildspec re-pushes `$APP_NAME-latest` every build for ECS; an immutable repo rejects that
  and would break ECS deploys. The audit assumed unique tags *replaced* `-latest`; the plan
  has them coexist.
- **Destroy re-auth now raises** instead of proceeding with empty credentials. Phase 2 removed
  the stored-credential fallback the old code degraded to; continuing would skip the EKS
  orphan reap and fail inside terraform anyway. The outer handler only logs, so the env stays
  `DESTROYING` and the reaper retries — correct for a transient auth failure.
- **`AmazonEKSBlockStoragePolicyV2`** (not V1) on the cluster role — current AWS Auto Mode docs.

## Noticed, not addressed (out of plan scope)

- `enforce_rightsizing.py:36` is now a fail-safe no-op post credential-purge, but still burns
  one AssumeRole per infra per run.
- `application_deployment_service.py:170` `_refresh_credentials` pre-warm is now a redundant
  STS call (session build always assumes fresh).
- `notification.py:29-38` still resolves the recipient as the globally-first `super_admin`,
  so infra notifications can be delivered cross-tenant. Pre-existing; more sensitive now that
  EKS error strings can carry a live cluster endpoint.
- GitHub token plaintext fallback at `aws/codebuild.py:128` reaches CodeBuild logs. Pre-existing.

## Suggested Phase 4 commit message

```
feat(eks): deploy applications onto EKS via the Kubernetes API

Namespace-per-application (app-{slug}) with PodSecurity baseline, ResourceQuota,
LimitRange and default-deny NetworkPolicy; nginx sidecar retained since ALB Ingress
cannot rewrite paths. Deploys assume the namespace-scoped ${cluster}-deploy role,
not cluster-admin. Unique per-deploy image tags alongside -latest so ECS is unchanged.

App name uniqueness moves from (user, infrastructure, name) to (infrastructure, name):
the deploy-time slug is derived from name, so two users on a shared infra could
otherwise collide on k8s object names and silently overwrite each other. Migration
0026 scans for duplicates and fails loudly rather than crashing on the constraint.
```
