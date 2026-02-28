#!/bin/bash

set -euo pipefail

if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
else
  echo ".env file not found"
  exit 1
fi

: "${PG_CONTAINER:?Missing PG_CONTAINER}"
: "${PG_USER:?Missing PG_USER}"
: "${MYSQL_CONTAINER:?Missing MYSQL_CONTAINER}"
: "${MYSQL_USER:?Missing MYSQL_USER}"
: "${MYSQL_PASSWORD:?Missing MYSQL_PASSWORD}"
: "${MYSQL_DB:?Missing MYSQL_DB}"
: "${MONGO_CONTAINER:?Missing MONGO_CONTAINER}"
: "${MONGO_URI:?Missing MONGO_URI}"
: "${MONGO_DB:?Missing MONGO_DB}"
: "${REDIS_CONTAINER:?Missing REDIS_CONTAINER}"
: "${REDIS_PASSWORD:?Missing REDIS_PASSWORD}"
: "${REDIS_DBS:?Missing REDIS_DBS}"

echo "=============================="
echo "RESETTING ALL DATABASES"
echo "=============================="

#######################################
# PostgreSQL
#######################################
echo "→ Resetting PostgreSQL databases..."

PG_DATABASES=(
  "auth_db"
  "payments_db"
  "infrastructure_db"
  "application_db"
)

docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d postgres -c "
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname = ANY(ARRAY['auth_db','payments_db','infrastructure_db','application_db']);
" >/dev/null

for db in "${PG_DATABASES[@]}"
do
  echo "   ↳ Resetting $db"
  docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d postgres -c "DROP DATABASE IF EXISTS \"$db\";"
  docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d postgres -c "CREATE DATABASE \"$db\";"
done

echo "✓ PostgreSQL databases recreated."


#######################################
# MySQL
#######################################
echo "→ Resetting MySQL database..."

docker exec "$MYSQL_CONTAINER" mysql \
  -u"$MYSQL_USER" \
  -p"$MYSQL_PASSWORD" \
  -e "DROP DATABASE IF EXISTS \`$MYSQL_DB\`; CREATE DATABASE \`$MYSQL_DB\`;"

echo "✓ MySQL database recreated."


#######################################
# MongoDB
#######################################
echo "→ Resetting MongoDB database..."

docker exec "$MONGO_CONTAINER" mongosh "$MONGO_URI" --quiet --eval "
db = db.getSiblingDB('$MONGO_DB');
db.dropDatabase();
"

echo "✓ MongoDB database reset."


#######################################
# Redis
#######################################
echo "→ Flushing Redis databases..."

IFS=',' read -ra DBS <<< "$REDIS_DBS"

for db in "${DBS[@]}"
do
  echo "   ↳ Flushing Redis DB $db"
  docker exec "$REDIS_CONTAINER" redis-cli -a "$REDIS_PASSWORD" -n "$db" FLUSHDB >/dev/null
done

echo "✓ Redis databases flushed."


echo "=============================="
echo "ALL DATABASES RESET SUCCESSFULLY"
echo "=============================="