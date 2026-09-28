# H — Hardening follow-ups

**Status:** not started · Found while building and reviewing F0–F6 and F1b; none was
owned by any feature. Each item is one PR, built mock-first, with an independent security
review before merge — same process as the features.

## H1 — Encrypt `Application.envs` at rest

`Application.envs` (`application-service/api/models/application.py`) is a plaintext
`JSONField`. F3 (H7) and F6 (H6) were designed around that rather than fixing it.

- Envelope-free field-level encryption with a platform key (`cryptography` Fernet via
  `MultiFernet` so keys rotate without a flag day); key from env, required outside dev.
- Data migration encrypting existing rows; reads accept only ciphertext after migration.
- Every reader of `envs` goes through the model — the deploy path (task definition, k8s
  manifest), rollback, exit inventory, the API (which already returns values to the owner
  only) — so no new plaintext copy appears anywhere.
- Rotation command re-encrypting under the newest key.

## H2 — Enforce exit in application-service

F6 Decision 14: after *Complete exit* infrastructure-service refuses reprovision/config
changes, but application-service still deploys and creates apps — its read-model never
learns `exited_at`. Publish an `infrastructure.exited` event (or extend the readiness
contract), mirror `exited_at`, refuse deploy / rollback / app create / webhook deploys /
custom-domain attach with a clear error.

## H3 — Unauthenticated identity endpoints

- user-service `GET /users/:userId` and `GET /users?q=` (`user.controller.ts`) have no
  auth; the gateway exposes search publicly (`gateway-service/app/api/endpoints/user.py`),
  so anyone can enumerate users by email.
- The notification route `/notifications/user/{user_id}` passes a caller-chosen user id.
- Audit every identity-service and notification-service route for the same pattern
  (id from path/body, no verified caller). Fix: derive the caller from a verified token;
  scope lookups to what the caller may see.

## H4 — Truncated-UUID resource names

`CLAUDE.md` forbids UUIDv7 prefixes as uniqueness keys.

- **Target groups** — `f"{slug}-{infrastructure_id[:8]}-tg"`
  (`application_deployment_service.py`). Two infras in one AWS account created within ~65s
  with the same app slug get the same name, and `CreateTargetGroup` on an existing name
  with the same settings **returns the existing group** — app B registers into app A's
  target group. Use a full-id hash (the `Database.module_name()` pattern); existing TGs keep
  their stored ARN, new ones get the safe name.
- **ECS log groups** — `/ecs/{slug}-task` is shared by same-name apps across infras in one
  account (F2 pre-review). Add a per-infra discriminator for new deploys; runtime-logs and
  cleanup read the stored name.
- Review the remaining `[:8]` sites (`naming.environment_name`,
  `app_security_group_name`, `eks_bootstrap` legacy fallback) — they carry a full-id hash
  suffix, so they are not uniqueness keys; confirm and document rather than rename live
  resources.

## H5 — Gateway path ids typed as UUID

Only routes added in F0–F6/F1b take `uuid.UUID`; 59 older path params are `str`, which lets
`%3F`/`%23` change the upstream URL (confused deputy — upstream authz still applies).
Type them all; a malformed id returns 422 at the edge.

## H6 — Small items

- EKS: unmatched `:443` traffic gets 503 (empty-backend Service) rather than a fixed 404.
- Custom domains: an operator path to force-disable a domain (abuse/takedown).
- Databases: a tighter per-user write bucket for create/delete (F0 follow-up).
- dns_writer Redis ACL + RabbitMQ user wired into `infra/.docker` for local dev (documented
  in `docs/PLATFORM_DNS_ISOLATION.md`, not automated).
