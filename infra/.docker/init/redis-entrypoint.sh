#!/bin/sh
set -e

# H7 (dns_writer isolation): opt-in only. With DNS_WRITER_REDIS_PASSWORD unset (the
# default), this starts redis exactly as before — plain `--requirepass`, no ACL file —
# so a dev who hasn't opted into testing the isolation locally sees no change at all,
# and every other service keeps authenticating to the `default` user with
# REDIS_PASSWORD exactly as it does today.
#
# When opted in, renders an ACL file that keeps `default` on that SAME password (never
# nopass, never a different one) and adds a `launchpad_dns_writer` user limited to
# `platform_dns:*` keys — see docs/PLATFORM_DNS_ISOLATION.md. Verified against a real
# redis:7 container: the default user still authenticates with REDIS_PASSWORD,
# launchpad_dns_writer gets NOPERM on `infra:*` and OK on `platform_dns:*`.
if [ -n "$DNS_WRITER_REDIS_PASSWORD" ]; then
    ACL_FILE=/tmp/users.acl
    cat >"$ACL_FILE" <<-EOF
user default on >${REDIS_PASSWORD} ~* &* +@all
user launchpad_dns_writer on >${DNS_WRITER_REDIS_PASSWORD} ~platform_dns:* +@read +@write +@keyspace -@dangerous +get +set +setnx +setex +del +exists
EOF
    exec redis-server --aclfile "$ACL_FILE"
else
    exec redis-server --requirepass "$REDIS_PASSWORD"
fi
