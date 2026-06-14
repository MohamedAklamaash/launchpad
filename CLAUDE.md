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
| `app_scripts/` | bash | Customer-run onboarding scripts (`create_aws_role.sh`, `update_aws_role.sh`). IAM action list must stay in sync between them — CI enforces via `_check_policy_sync.py`. |
| `infra/.docker/` | docker compose | Local dev stack: Postgres, MySQL, Mongo, Redis, RabbitMQ, Prometheus/Grafana. Ports come from `.env`; `docker-compose.override.yml` is applied automatically. |
| `infra/aws/` | Terraform | Platform infrastructure modules (vpc, ecs, ecr, alb, iam, secrets). |

## Auth model

- **User auth:** JWT issued by auth-service; Django services verify it in `shared/middleware/authentication.py`.
- **Service-to-service:** gateway injects `X-INTERNAL-TOKEN`; enforced by `shared/middleware/internal_auth.py` with per-service exempt paths/prefixes in each `core/settings.py`.
- **Onboarding token:** single-use, SHA-256-hashed token on `Infrastructure` (`issue_onboarding_token()`), burned by the onboarding callback.
- **GitHub webhooks:** per-app secret, validated with `hmac.compare_digest` on `X-Hub-Signature-256`.

## Onboarding flow

1. Dashboard `POST /api/infrastructures/` → gateway → infrastructure-service creates infra + PENDING environment, mints onboarding token (returned once).
2. Frontend renders a `create_aws_role.sh` command with `LAUNCHPAD_INFRA_ID`, `LAUNCHPAD_ONBOARDING_TOKEN`, `LAUNCHPAD_EXTERNAL_ID`, `LAUNCHPAD_CALLBACK_URL` exported.
3. Customer runs the script in their AWS account: creates role + policy, then POSTs `{infra_id, account_id, onboarding_token}` to `/api/infrastructures/onboarding/callback` (no JWT; token-authenticated).
4. Callback verifies `infra.code == account_id` and the token hash, runs `authenticate_infrastructure` (AssumeRole with ExternalId), burns the token, publishes `infra.created` to RabbitMQ, enqueues provisioning.
5. `update_aws_role.sh` refreshes the IAM policy + trust policy in place for already-onboarded accounts.

## Dev environment

```bash
cd infra/.docker && docker compose up -d   # DBs/MQs; env vars from .env
```
Each Python service has an `env.example`. Identity services: `pnpm install` at `identity-services/`. Frontend: `pnpm dev` at `launchpad-frontend/`.

## CI (.github/workflows/ci.yml)

- Python services (gateway, payment, deployment-services): `python -m compileall <dir> -q` and `ruff check <dir>` (config: root `ruff.toml`).
- identity-services: `pnpm install --frozen-lockfile`, `pnpm --filter @launchpad/common build`, `pnpm format` (prettier --check), `pnpm lint`, `pnpm -r --workspace-root=false exec tsc --noEmit`.
- frontend: lint + typecheck.
- `Onboarding script policy sync`: `app_scripts/_check_policy_sync.py` fails if the IAM action lists in `create_aws_role.sh` and `update_aws_role.sh` drift apart.

Run the matching commands locally before pushing; prettier failures are the most common CI break in identity-services.

## Tests

- infrastructure-service: `pytest` from `deployment-services/infrastructure-service/` (uses `test_settings.py`, see `pytest.ini`).
- gateway-service: `pytest gateway-service/tests/`.
- app_scripts: `pytest app_scripts/test_check_policy_sync.py`.

## Conventions

- Django routes are versioned `/api/v1/...`; the gateway exposes them unversioned as `/api/...`.
- Each Django service derives `ALLOWED_HOSTS` from `core/allowed_hosts_config.py` (env-driven).
- RabbitMQ events: producers/consumers under each service's `messaging/`; DLQ tooling in `inspect_dlq.py`.
- Auth-exempt endpoints (webhooks, onboarding callback) must be listed in BOTH the JWT middleware exemptions and `INTERNAL_AUTH_EXEMPT_*` settings.
