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