#!/bin/sh
set -e

# H7 (dns_writer isolation): opt-in only, skipped entirely when
# DNS_WRITER_RABBITMQ_PASSWORD isn't set — so a dev who hasn't opted into testing the
# isolation locally doesn't get an extra user. Uses the management HTTP API (two
# idempotent PUTs) rather than a `definitions.json` import: importing definitions can
# redefine RABBITMQ_DEFAULT_USER's own existence/permissions on load, where this only
# ever adds the one extra user and its permissions — guest is never touched. See
# docs/PLATFORM_DNS_ISOLATION.md.
if [ -z "$DNS_WRITER_RABBITMQ_PASSWORD" ]; then
    echo "DNS_WRITER_RABBITMQ_PASSWORD not set — skipping launchpad_dns_writer RabbitMQ user"
    exit 0
fi

API="http://rabbitmq:15672/api"
AUTH="${RABBITMQ_DEFAULT_USER}:${RABBITMQ_DEFAULT_PASS}"

# The healthcheck this service depends_on (`rabbitmq-diagnostics ping`) passes as soon
# as the Erlang node is up, which can be a few seconds before the management plugin is
# actually listening on 15672 — wait for the API itself, not just the node, before the
# first PUT below.
for _ in $(seq 1 30); do
    curl -sf -u "$AUTH" "$API/overview" >/dev/null 2>&1 && break
    sleep 1
done

curl -sf -u "$AUTH" -X PUT "$API/users/launchpad_dns_writer" \
    -H 'content-type: application/json' \
    -d "{\"password\":\"${DNS_WRITER_RABBITMQ_PASSWORD}\",\"tags\":\"\"}"

# The three patterns are configure / write / read (rabbitmqctl set_permissions order) —
# same regexes as docs/PLATFORM_DNS_ISOLATION.md's rabbitmqctl example: declare/publish
# to platform_dns.events and infrastructure.events, consume only from this writer's own
# queue or its DLQ.
curl -sf -u "$AUTH" -X PUT "$API/permissions/%2F/launchpad_dns_writer" \
    -H 'content-type: application/json' \
    -d '{
        "configure": "^(platform_dns\\.events|infrastructure\\.events)$",
        "write": "^(platform_dns\\.events|infrastructure\\.events)$",
        "read": "^infrastructure-service\\.platform-dns-writer(\\.dlq)?$"
    }'

echo "launchpad_dns_writer RabbitMQ user configured"
