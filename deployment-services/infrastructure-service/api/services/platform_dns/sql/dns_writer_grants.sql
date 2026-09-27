-- Least-privilege Postgres role for the platform DNS writer (LAUNCHPAD_PROCESS_ROLE=dns_writer).
--
-- Run once against the infrastructure_db database, as a superuser or the role that owns
-- these tables. The writer connects with DNS_WRITER_DB_USER/DNS_WRITER_DB_PASSWORD (see
-- api/common/envs/database.py) instead of the broader DATABASE_USER_NAME every other
-- process in this service uses.
--
-- Scope: SELECT only on the three tables desired_state.py reads to compute what DNS state
-- should exist (api_infrastructure, environments, api_infrastructurecertificate); SELECT
-- and INSERT/UPDATE/DELETE on the one table the writer owns outright, the PlatformDnsRecord
-- ledger (api_platformdnsrecord). No grant on any other table in this database — in
-- particular no grant on api_user, api_database, or anything holding a customer secret.
--
-- Table-level, not column-level: Django's ORM issues a full-row SELECT for these models by
-- default (no .only() restriction in desired_state.py), so a column-level GRANT would
-- reject those queries outright. Table-level SELECT is still materially narrower than the
-- broad DATABASE_USER_NAME grant every other process uses — this role cannot read
-- api_user.password_hash, api_database's connection secrets, or anything else in the
-- database it doesn't need.
--
-- Update the table names here if a migration ever renames them; nothing enforces this file
-- staying in sync with api/migrations/ automatically.

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'launchpad_dns_writer') THEN
        CREATE ROLE launchpad_dns_writer LOGIN PASSWORD 'CHANGE_ME_SET_VIA_ENV_NOT_THIS_FILE';
    END IF;
END
$$;

GRANT CONNECT ON DATABASE infrastructure_db TO launchpad_dns_writer;
GRANT USAGE ON SCHEMA public TO launchpad_dns_writer;

GRANT SELECT ON api_infrastructure TO launchpad_dns_writer;
GRANT SELECT ON environments TO launchpad_dns_writer;
GRANT SELECT ON api_infrastructurecertificate TO launchpad_dns_writer;

GRANT SELECT, INSERT, UPDATE, DELETE ON api_platformdnsrecord TO launchpad_dns_writer;

-- No sequence grants needed: every id column in these models is a UUID with a Python-side
-- default (shared.utils.uuid.uuid7_pk), never a Postgres SERIAL/IDENTITY sequence.

-- Verify with (as launchpad_dns_writer):
--   SELECT 1 FROM api_infrastructure LIMIT 1;                 -- succeeds
--   SELECT 1 FROM api_user LIMIT 1;                            -- must fail: permission denied
--   INSERT INTO api_platformdnsrecord (...) VALUES (...);      -- succeeds
--   UPDATE api_infrastructure SET name = 'x' WHERE id = '...'; -- must fail: permission denied
