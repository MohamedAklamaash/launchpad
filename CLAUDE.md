# Launchpad

PaaS that deploys customer applications into the customer's own AWS account via
cross-account IAM AssumeRole (`LaunchpadDeploymentRole`, ExternalId = infrastructure id).

## Services

| Path | Stack | Role |
|---|---|---|
| `gateway-service/` | FastAPI | Public API gateway. Proxies `/api/*` to backend services, injects `X-INTERNAL-TOKEN`, Redis-backed rate limiting. Does NOT verify JWTs — passes `Authorization` through. |
| `deployment-services/infrastructure-service/` | Django/DRF | Customer AWS account onboarding (`Infrastructure` model, onboarding tokens, STS AssumeRole in `api/cloud_providers/aws/authenticate.py`), environment provisioning queue. |
| `deployment-services/application-service/` | Django/DRF | Application CRUD, GitHub webhooks (per-app HMAC secret), deployments via ECS/ECR/CodeBuild (`aws/` clients). |
| `deployment-services/shared/` | Python | Cross-service middleware: `middleware/authentication.py` (JWT), `middleware/internal_auth.py` (`X-INTERNAL-TOKEN`), AMQP resilience helpers. |
| `identity-services/` | pnpm TS monorepo | `services/auth-service` (issues/verifies JWTs), `services/user-service`, `services/notification-service` (Resend email), `packages/common`. |
| `payment-service/` | Django/DRF | Stripe billing. |
| `launchpad-frontend/` | Next.js (app router) | Dashboard. Onboarding script snippets generated in `lib/onboarding-scripts.ts`. |
| `app_scripts/` | bash | Customer-run onboarding script (`create_aws_role.sh`) — one idempotent script for both first-time bootstrap and later policy refresh; picks the callback by which credential is injected (`MODE=dev` sets `LAUNCHPAD_MOCK=1` to skip AWS entirely). Its IAM policy heredoc is **generated** — see *IAM policy source of truth*. |
| `infra/.docker/` | docker compose | Local dev stack: Postgres, MySQL, Mongo, Redis, RabbitMQ, Prometheus/Grafana. Ports come from `.env`; `docker-compose.override.yml` is applied automatically. |
| `infra/aws/` | Terraform | Platform infrastructure modules (vpc, ecs, ecr, alb, iam, secrets). |

## Auth model

- **User auth:** JWT issued by auth-service; Django services verify it in `shared/middleware/authentication.py`.
- **Service-to-service:** gateway injects `X-INTERNAL-TOKEN`; enforced by `shared/middleware/internal_auth.py` with per-service exempt paths/prefixes in each `core/settings.py`.
- **Onboarding token:** single-use, SHA-256-hashed token on `Infrastructure` (`issue_onboarding_token()`), burned by the onboarding callback.
- **GitHub webhooks:** per-app secret, validated with `hmac.compare_digest` on `X-Hub-Signature-256`.

## IAM policy source of truth

`LaunchpadDeploymentPolicy` is defined once, as data, in
`deployment-services/infrastructure-service/api/cloud_providers/aws/iam_policy/policy.json`.
Every customer-facing copy — the heredoc in `create_aws_role.sh` and the three blocks in
`docs/IAM_POLICIES.md` — is a generated region between `BEGIN GENERATED` / `END GENERATED`
markers. **Never hand-edit those regions**; edit `policy.json` and run:

```bash
python deployment-services/infrastructure-service/api/cloud_providers/aws/iam_policy/generate.py --write
```

CI runs the same script with `--check` (job `check-iam-policy`) and fails on any drift.

Changing the granted actions requires bumping `version` in `policy.json` — the generator
binds each version to a content hash of its statements and refuses to redefine a released
one. The script reports that version on both callbacks; it lands on
`Infrastructure.policy_version`, and the dashboard flags infrastructures whose applied
version is behind so customers re-run the *Refresh policy* snippet before a deploy hits
`AccessDenied`.

**Bumping the policy version requires bumping `NEXT_PUBLIC_LAUNCHPAD_SCRIPT_REF` in the
same release.** The frontend pins `create_aws_role.sh` to a commit SHA; until that ref
moves, the refresh snippet fetches the old script, reinstalls the old policy, and reports
the old version — so the stale flag never clears and the customer never gets the grants.

## Onboarding flow

1. Dashboard `POST /api/infrastructures/` → gateway → infrastructure-service creates infra + PENDING environment, mints onboarding token (returned once).
2. Frontend renders a `create_aws_role.sh` command with `LAUNCHPAD_INFRA_ID`, `LAUNCHPAD_ONBOARDING_TOKEN`, `LAUNCHPAD_EXTERNAL_ID`, `LAUNCHPAD_CALLBACK_URL` exported.
3. Customer runs the script in their AWS account: creates role + policy, then POSTs `{infra_id, account_id, onboarding_token}` to `/api/infrastructures/onboarding/callback` (no JWT; token-authenticated).
4. Callback verifies `infra.code == account_id` and the token hash, runs `authenticate_infrastructure` (AssumeRole with ExternalId), burns the token, publishes `infra.created` to RabbitMQ, enqueues provisioning.
5. Re-running `create_aws_role.sh` with a script API key (the dashboard's *Refresh policy* snippet) refreshes the IAM policy + trust policy in place for already-onboarded accounts and posts the policy-refresh callback.

## Dev environment

```bash
cd infra/.docker && docker compose up -d   # DBs/MQs; env vars from .env
```
Each Python service has an `env.example`. Identity services: `pnpm install` at `identity-services/`. Frontend: `pnpm dev` at `launchpad-frontend/`.

## CI (.github/workflows/ci.yml)

- iam-policy: `iam_policy/generate.py --check` — fails if a generated policy region drifted from `policy.json`.
- Python services (gateway, payment, deployment-services): `python -m compileall <dir> -q` and `ruff check <dir>` (config: root `ruff.toml`).
- identity-services: `pnpm install --frozen-lockfile`, `pnpm --filter @launchpad/common build`, `pnpm format` (prettier --check), `pnpm lint`, `pnpm -r --workspace-root=false exec tsc --noEmit`.
- frontend: lint + typecheck.

Run the matching commands locally before pushing; prettier failures are the most common CI break in identity-services.

## Tests

- infrastructure-service: `pytest` from `deployment-services/infrastructure-service/` (uses `test_settings.py`, see `pytest.ini`).
- gateway-service: `pytest gateway-service/tests/`.

## Conventions

- Django routes are versioned `/api/v1/...`; the gateway exposes them unversioned as `/api/...`.
- Each Django service derives `ALLOWED_HOSTS` from `core/allowed_hosts_config.py` (env-driven).
- RabbitMQ events: producers/consumers under each service's `messaging/`; DLQ tooling in `inspect_dlq.py`.
- Auth-exempt endpoints (webhooks, onboarding callback) must be listed in BOTH the JWT middleware exemptions and `INTERNAL_AUTH_EXEMPT_*` settings.
- Never use a truncated UUIDv7 (or any UUIDv7 prefix) as a namespace/uniqueness key. Its leading 48 bits are a Unix-millisecond timestamp, so a short prefix only advances every ~65 seconds platform-wide and is forceable from any timestamp the row exposes (`created_at`, etc). This applies to DNS labels, cert SANs, resource-name prefixes, cluster names, and cache keys — see `Infrastructure.dns_label` (independent random token) and `Database.module_name()` (full-UUID hash, never a slice) for the two correct patterns.

@.claude/CLAUDE.md
