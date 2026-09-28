# Platform DNS writer — least-privilege isolation

The `LAUNCHPAD_PROCESS_ROLE=dns_writer` process (`manage.py run_dns_writer`,
`manage.py sweep_platform_dns`) is the only process allowed to hold `PLATFORM_DNS_*`
AWS credentials — see `infra/platform-dns/README.md` and
`api/common/envs/application.py`. That isolation is enforced in code (env-var presence
asserted both directions at startup), but the same process still connects to the same
Postgres database, the same Redis instance, and the same RabbitMQ broker as every other
infrastructure-service process. This document covers narrowing those three down to what
the writer actually needs, so a compromise of the writer's process doesn't also hand over
read/write access to customer secrets in Postgres, the ability to manipulate unrelated
Redis keys, or the ability to consume/publish on queues that belong to other consumers.

## Postgres

`api/services/platform_dns/sql/dns_writer_grants.sql` creates a `launchpad_dns_writer`
role and grants:

| Access | Tables |
|---|---|
| SELECT only | `api_infrastructure`, `environments`, `api_infrastructurecertificate` |
| SELECT, INSERT, UPDATE, DELETE | `api_platformdnsrecord` (the ledger) |

Nothing else — no access to `api_user`, `api_database`, or any other table in
`infrastructure_db`.

Apply it once, after infrastructure-service's migrations have created the tables being
granted on (the role itself can be created before that; the per-table grants cannot):

```bash
cd deployment-services/infrastructure-service
python manage.py migrate
python manage.py apply_dns_writer_grants
```

Set the role's password out of band (`ALTER ROLE launchpad_dns_writer PASSWORD '...'`,
or a secrets manager), then wire it into the writer's environment — **not** the shared
`.env` every other process reads, since that file also carries `DATABASE_USER_NAME`/
`DATABASE_PASSWORD` for the broader role:

```
DNS_WRITER_DB_USER=launchpad_dns_writer
DNS_WRITER_DB_PASSWORD=...
```

`api/common/envs/database.py` reads these instead of `DATABASE_USER_NAME`/
`DATABASE_PASSWORD` whenever `LAUNCHPAD_PROCESS_ROLE=dns_writer`, and refuses to start in
production if they're unset (falls back to the shared credential only in `MODE=dev`, for
local convenience). For local dev via `infra/.docker`, `postgres-init.sh` creates the role
if `DNS_WRITER_DB_PASSWORD` is set in `infra/.docker/.env` — see that file's comment.

## Redis

The writer's only Redis usage is the reconcile coalescing key,
`platform_dns:reconcile_pending:<infra_id>` (`api/services/platform_dns/producer.py`). It
never touches `infra:*` (the provisioning queue's locks/dedup keys) or any other
namespace. Scope it with a Redis ACL user:

```
ACL SETUSER launchpad_dns_writer on >CHANGE_ME_PASSWORD \
    ~platform_dns:* \
    +@read +@write +@keyspace -@dangerous \
    +get +set +setnx +setex +del +exists
```

`~platform_dns:*` restricts every command this user runs to keys under that prefix —
an attempt to touch `infra:provision` or `lock:infra:*` fails with `NOPERM`. Wire the
resulting credential into `REDIS_HOST`/`REDIS_PORT`/`REDIS_PASSWORD` for the dns_writer
process's environment (Redis's `AUTH` takes a single password for a non-default ACL user
as `AUTH <username> <password>`; `redis-py`'s `Redis(..., username=..., password=...)`
supports this — `ApplicationConfig` does not currently expose a separate Redis username
for the writer, since no other process in this service uses one either. Wiring it through
is a small follow-up once an ACL-aware Redis deployment exists to test against).

**Wired into `infra/.docker` (H7), opt-in.** Set `DNS_WRITER_REDIS_PASSWORD` in
`infra/.docker/.env` and `infra/.docker/init/redis-entrypoint.sh` (the redis service's
entrypoint) renders an `aclfile` at container start instead of the plain
`--requirepass` line — the `default` user keeps `REDIS_PASSWORD`, the exact same
password every other service in the compose file already authenticates with, and a
`launchpad_dns_writer` user is added with the same `~platform_dns:*` scope as the
`ACL SETUSER` above. Leave the variable unset (the default) and nothing changes: the
entrypoint falls back to the original `redis-server --requirepass "$REDIS_PASSWORD"`
line verbatim. Verified against a real `redis:7` container: the default user still
authenticates with `REDIS_PASSWORD`, `launchpad_dns_writer` gets `NOPERM` on `infra:*`
and `OK` on `platform_dns:*`. `docker compose config` confirms no other service's
environment changes when the variable is set.

## RabbitMQ

Restrict the writer's AMQP user to only its own queues (the main reconcile queue and its
DLQ) plus, since F1b part 3a, publish-only access to `infrastructure.events` for the
`infrastructure.host_readiness_updated` event — see below — via `rabbitmqctl`'s permission
patterns, which are regexes matched against configure/write/read operations independently:

```bash
rabbitmqctl add_user launchpad_dns_writer CHANGE_ME_PASSWORD
rabbitmqctl set_permissions -p / launchpad_dns_writer \
    "^(platform_dns\.events|infrastructure\.events)$" \
    "^(platform_dns\.events|infrastructure\.events)$" \
    "^infrastructure-service\.platform-dns-writer(\.dlq)?$"
```

The three patterns are configure / write / read. This user can declare and publish to the
`platform_dns.events` exchange (its own reconcile/DLQ topology) and the `infrastructure.events`
exchange, and consume only from `infrastructure-service.platform-dns-writer` or its `.dlq`
queue — it cannot touch `application_events`, or the Redis-backed `infra:provision`/
`infra:destroy` queues (those aren't AMQP at all, but the same user also can't declare or
bind any other exchange/queue).

**`infrastructure.events` is shared, publish-only, and the routing key is not enforceable
by RabbitMQ permissions.** That exchange already exists — infrastructure-service's main
process (not the writer) declares it and publishes `infrastructure.created`/`.updated`/
`.deleted`/`.user_removed`/`environment.updated` on it; the writer's own publish
(`api/services/host_readiness.py:publish_host_readiness`, called from
`platform_dns/dispatch.py`'s converge loop) only ever sends
`infrastructure.host_readiness_updated`, and needs no read access to it at all — it never
consumes anything bound there. RabbitMQ's permission model matches **exchange and queue
names**, not routing keys, so this grant cannot be narrowed to "only the
`infrastructure.host_readiness_updated` routing key": the writer's AMQP user, if
compromised, could technically publish a forged `infrastructure.created`/`.deleted`/etc.
message onto the same exchange. The application-layer defenses this depends on instead:
every consumer of `infrastructure.events` (application-service's
`InfraEventConsumer`/`InfraUpdatedEventConsumer`/`InfraDeletedEventConsumer`/
`EnvironmentEventConsumer`/`HostReadinessEventConsumer`) treats every payload as an
untrusted read-model snapshot, never an authorization grant, and the write-once (R4) and
version-ordering (RECOMMENDED item 2) checks in `HostReadinessEventConsumer` specifically
bound what a forged `host_readiness_updated` message could actually change (dns_synced/
https_ready/tls_status booleans and strings, never dns_label once set, never anything
security-relevant like `is_cloud_authenticated` or `code`). A forged `infrastructure.created`/
`.deleted` from this credential is a real residual risk this permission grant alone does not
close — narrowing it further needs either a routing-key-aware broker (RabbitMQ does not
support this natively) or moving `host_readiness_updated` onto its own dedicated exchange
the writer is the sole publisher for, tracked as a follow-up.

Wire the resulting credential into a dedicated `PLATFORM_DNS_RABBITMQ_URL`-style variable
for the writer's environment once a broker with a real vhost/user setup exists to test
against — `RABBITMQ_URL` is currently shared with every other process in this service
(it carries no AWS/DB secret, so sharing it is lower risk than the Postgres/AWS
credentials, but a dedicated user is still strictly better).

**Wired into `infra/.docker` (H7), opt-in.** A one-shot `rabbitmq-init` compose service
(`infra/.docker/init/rabbitmq-dns-writer-init.sh`) runs after `rabbitmq` is healthy and,
only when `DNS_WRITER_RABBITMQ_PASSWORD` is set in `infra/.docker/.env`, calls the
management HTTP API to `PUT` a `launchpad_dns_writer` user and the exact permission
patterns shown above on the existing `/` vhost — the same idempotent shape as
`rabbitmqctl add_user`/`set_permissions`, over HTTP so no extra tooling is needed in the
init container. `RABBITMQ_DEFAULT_USER`/`RABBITMQ_DEFAULT_PASS` (`guest`/`guest` by
default) are untouched: this only ever adds one more user, never a `definitions.json`
import, which could otherwise redefine the default user's own permissions or existence
on load. Leave the variable unset (the default) and the init container exits 0
immediately, adding nothing. `docker compose config` confirms no other service's
`RABBITMQ_URL` changes either way.

**Local dev (`infra/.docker`):** not configured — the compose stack's single RabbitMQ user
(`guest`/`guest` or whatever `RABBITMQ_URL` in `.env` points at) is shared by every service,
same as Redis above, and per-user permissions require a dedicated vhost/user that would
have to be threaded through every service's `RABBITMQ_URL`, not just the writer's. Cheap to
add later (`rabbitmqctl` runs fine against the compose container), but changing one
service's broker identity without breaking the other eight consumers/producers sharing this
same instance is more than a "for local dev if cheap" change — tracked as a follow-up
alongside the Redis ACL gap above.

## What's actually enforced today vs documented

The Postgres role and grants are real: `dns_writer_grants.sql`,
`apply_dns_writer_grants`, and `DatabaseConfig.from_env()`'s credential switch all exist
and are exercised by the test suite (mocked at the Django `connection` layer, since tests
run against sqlite). The Redis ACL and RabbitMQ user commands above are documented but not
wired into any automated setup or CI check — they need a Redis/RabbitMQ deployment with
ACLs actually enabled to apply and verify against, which `infra/.docker`'s shared
single-user instances don't provide without changing how every other service
authenticates. Track this as a follow-up once F1b part 2/3 or a broader "give every
service its own least-privilege broker user" effort touches this file again.
