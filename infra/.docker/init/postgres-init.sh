#!/bin/bash
set -e

echo "Creating microservice databases..."

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<-EOSQL
    CREATE DATABASE auth_db;
    CREATE DATABASE payments_db;
    CREATE DATABASE infrastructure_db;
    CREATE DATABASE application_db;

    GRANT ALL PRIVILEGES ON DATABASE auth_db TO "$POSTGRES_USER";
    GRANT ALL PRIVILEGES ON DATABASE payments_db TO "$POSTGRES_USER";
    GRANT ALL PRIVILEGES ON DATABASE infrastructure_db TO "$POSTGRES_USER";
    GRANT ALL PRIVILEGES ON DATABASE application_db TO "$POSTGRES_USER";
EOSQL