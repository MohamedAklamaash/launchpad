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

## Post-implementation audit — done

A second security audit ran against the *implementation* (not the design) and returned
BLOCK with 1 CRITICAL and 3 HIGH. All are now fixed:

| Finding | Fix |
|---|---|
| CRITICAL: uniqueness enforced on `name`, not the derived slug — `"MyApp"` vs `"myapp"` collapse onto one namespace and silently overwrite another user's workload | `ApplicationService._reserve_slug`, under `select_for_update` on the infra row; covers create **and** rename; 3 regression tests |
| HIGH-2: caller-controlled `metadata` interpolated unescaped into generated HCL = RCE on the provisioning worker | `validate_infra_metadata` allow-lists keys and validates region/CIDR/version at the create boundary; EKS builder interpolates via `json.dumps` |
| HIGH-3: NodeClass `spec.networkPolicy=DefaultAllow` patch was a no-op writing the default value | Removed. The `amazon-vpc-cni` ConfigMap **is** the documented Auto Mode enable step and stays |
| HIGH-4: all three teardown paths called the reap, but the reap never deletes the cluster | ERROR-state EKS now routes to a real `terraform destroy` and stays `DESTROYING`, instead of reap-and-drop |
| MEDIUM-1: `allow-alb-to-nginx` had no `from`, matching every source | Scoped to own namespace + VPC CIDR, renamed `allow-serving-port` |
| MEDIUM-2: quota/limits/policies only applied when the namespace was newly created | Applied unconditionally (all 409-tolerant), so a partial failure self-heals |
| MEDIUM-5: compute-type CPU/memory gating was dead code (serializer never runs) | `_validate_compute_shape` on the real create path |
| LOW-1: `Deny` wildcards missed the plural List APIs | `eks:*AccessEntr*` / `eks:*AccessPolic*` |
| LOW-2: `0.0.0.0/0` refusal was a literal match (`0.0.0.0/1`+`128.0.0.0/1` walked through) | `ipaddress` parse + `/16` minimum prefix, in Python and in the TF validation block |

**HIGH-1 not fixed, deliberately**: the same customer policy grants `iam:*` on `*`, so the
EKS scoping is defense-in-depth rather than a containment boundary. Pre-existing, not
introduced here. Do not describe the scoping as a boundary in customer-facing docs. Tracked
as a follow-up: scope `iam:*` to `role/launchpad-*` + `infra-*`, with `PassRole` limited to
those and a `Deny` against `LaunchpadDeploymentRole` itself.

**Audit finding that did not reproduce**: the refresh-policy snippet omitting
`LAUNCHPAD_COMPUTE_TYPE`. `resolveOnboardingScript`'s `refresh` variant exists in the
library but no UI renders it, so there is no live snippet to fix; the manual instruction in
`USER_ONBOARDING_GUIDE.md` is the correct coverage until that UI exists.

## Remaining work

1. **Runtime verification against real AWS.** Mock-mode E2E now exists
   (`test_eks_e2e_mock.py`: create → onboard → provision → ACTIVE → destroy), but nothing has
   run against a real account. Workers must be started via `app_scripts/start-workers.sh` —
   they die if backgrounded from a tool call.
2. **NetworkPolicy enforcement can only be proven on a live cluster.** A manifest-shape test
   passes against an unenforced policy. Make a real denied-connection check part of the
   sandbox smoke test before flipping `EKS_ENABLED`.
3. **Code review never completed** — the reviewer agent died to a rate limit before
   reporting. The security audit completed; a general correctness review did not.
4. **ruff**: 524 findings on this branch vs **497 on unmodified `main`** with the same ruff
   version (0.16.5). The +27 are all in rule classes the codebase already carries (RUF012 on
   migrations, I001, BLE001). CI does an unpinned `pip install ruff`, so CI is red on main
   independently of this work.
5. **`terraform validate`** was run by an agent using a terraform binary it downloaded;
   terraform is not installed on this machine. Re-verify if that matters.

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
