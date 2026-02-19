#!/bin/bash

set -e

echo "=============================="
echo "🔥 RESETTING ALL DATABASES 🔥"
echo "=============================="

#######################################
# PostgreSQL
#######################################
echo "→ Resetting PostgreSQL databases..."

PG_CONTAINER="psql_db"
PG_USER="aklamaash"

PG_DATABASES=(
  "auth_db"
  "payments_db"
  "infrastructure_db"
  "application_db"
)

docker exec $PG_CONTAINER psql -U $PG_USER -d postgres -c "
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname = ANY(ARRAY['auth_db','payments_db','infrastructure_db','application_db']);
" >/dev/null

for db in "${PG_DATABASES[@]}"
do
  echo "   ↳ Resetting $db"
  docker exec $PG_CONTAINER psql -U $PG_USER -d postgres -c "DROP DATABASE IF EXISTS $db;"
  docker exec $PG_CONTAINER psql -U $PG_USER -d postgres -c "CREATE DATABASE $db;"
done

echo "✓ PostgreSQL databases recreated."


#######################################
# MySQL
#######################################
echo "→ Resetting MySQL database..."

MYSQL_CONTAINER="mysql_users_db"
MYSQL_USER="aklamaash"
MYSQL_PASSWORD="akla1123"
MYSQL_DB="users_db"

docker exec $MYSQL_CONTAINER mysql -u$MYSQL_USER -p$MYSQL_PASSWORD -e "
DROP DATABASE IF EXISTS $MYSQL_DB;
CREATE DATABASE $MYSQL_DB;
"

echo "✓ MySQL database recreated."


#######################################
# MongoDB
#######################################
echo "→ Resetting MongoDB database..."

MONGO_CONTAINER="mongo"
MONGO_URI="mongodb://aklamaash:akla123@localhost:27017/admin"

docker exec $MONGO_CONTAINER mongosh "$MONGO_URI" --quiet --eval "
db = db.getSiblingDB('notifications');
db.dropDatabase();
"

echo "✓ MongoDB database reset."


#######################################
# Redis
#######################################
echo "→ Flushing Redis databases 0,1,2..."

REDIS_CONTAINER="redis-auth"
REDIS_PASSWORD="akla123"

for db in 0 1 2
do
  echo "   ↳ Flushing Redis DB $db"
  docker exec $REDIS_CONTAINER redis-cli -a $REDIS_PASSWORD -n $db FLUSHDB >/dev/null
done

echo "✓ Redis DB 0,1,2 flushed."


# #######################################
# # RabbitMQ
# #######################################
# echo "→ Resetting RabbitMQ..."

# RABBIT_CONTAINER="rabbitmq"

# # Delete all queues in default vhost
# docker exec $RABBIT_CONTAINER rabbitmqctl list_queues -p / name --quiet \
# | sed '1d' \
# | while read queue
# do
#   if [ -n "$queue" ]; then
#     echo "   ↳ Deleting queue $queue"
#     docker exec $RABBIT_CONTAINER rabbitmqctl delete_queue -p / "$queue"
#   fi
# done

# # Delete all custom exchanges (preserve amq.*)
# docker exec $RABBIT_CONTAINER rabbitmqctl list_exchanges -p / name --quiet \
# | sed '1d' \
# | while read exchange
# do
#   if [[ -n "$exchange" && ! "$exchange" =~ ^amq\. ]]; then
#     echo "   ↳ Deleting exchange $exchange"
#     docker exec $RABBIT_CONTAINER rabbitmqctl delete_exchange -p / "$exchange" || true
#   fi
# done

# echo "✓ RabbitMQ cleaned."



echo "=============================="
echo "✅ ALL SYSTEMS RESET SUCCESSFULLY"
echo "=============================="
