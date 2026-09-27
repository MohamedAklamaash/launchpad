#!/bin/bash
set -e

echo "Creating microservice databases..."

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<-EOSQL
    SELECT 'CREATE DATABASE auth_db' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'auth_db')\gexec
    SELECT 'CREATE DATABASE payments_db' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'payments_db')\gexec
    SELECT 'CREATE DATABASE infrastructure_db' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'infrastructure_db')\gexec
    SELECT 'CREATE DATABASE application_db' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'application_db')\gexec

    GRANT ALL PRIVILEGES ON DATABASE auth_db TO "$POSTGRES_USER";
    GRANT ALL PRIVILEGES ON DATABASE payments_db TO "$POSTGRES_USER";
    GRANT ALL PRIVILEGES ON DATABASE infrastructure_db TO "$POSTGRES_USER";
    GRANT ALL PRIVILEGES ON DATABASE application_db TO "$POSTGRES_USER";
EOSQL

# Platform DNS writer role (F1b) — created here (init runs once, before any table exists)
# so the role is available for local dev; the actual per-table GRANTs from
# deployment-services/infrastructure-service/api/services/platform_dns/sql/dns_writer_grants.sql
# have to wait until after migrations create those tables — run
# `manage.py apply_dns_writer_grants` once infrastructure-service's migrations have run.
# Skipped entirely if DNS_WRITER_DB_PASSWORD isn't set, so a dev who hasn't opted into
# testing the isolation locally doesn't get an extra role with a guessable password.
if [ -n "${DNS_WRITER_DB_PASSWORD:-}" ]; then
    echo "Creating launchpad_dns_writer role for the platform DNS writer..."
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname infrastructure_db <<-EOSQL
        DO \$\$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'launchpad_dns_writer') THEN
                CREATE ROLE launchpad_dns_writer LOGIN PASSWORD '${DNS_WRITER_DB_PASSWORD}';
            END IF;
        END
        \$\$;
        GRANT CONNECT ON DATABASE infrastructure_db TO launchpad_dns_writer;
EOSQL
fi