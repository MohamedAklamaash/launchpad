#!/bin/bash
#
# Start all app services on the host: identity (auth/user/notification),
# gateway, and the three Django web servers (application, infrastructure,
# payment). Databases must already be running (see start-dbs.sh); workers
# are started separately (see start-workers.sh).
#
# Ports: gateway 8000, application 8001, infrastructure 8002, payment 8003,
# identity 5001-5003 — matching gateway-service/.env.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

cleanup() {
    echo ""
    echo "Stopping app services..."
    pkill -P $$ 2>/dev/null || true
    exit 0
}

trap cleanup SIGINT SIGTERM

echo "Cleaning up stale app processes..."
pkill -f "tsx watch" 2>/dev/null || true
pkill -f "manage.py runserver" 2>/dev/null || true
pkill -f "gateway-service/venv/bin/python main.py" 2>/dev/null || true
sleep 2

echo "Starting Launchpad app services..."
echo "Root directory: $ROOT_DIR"
echo ""

wait_port() {
  HOST="$1"
  PORT="$2"
  retries=30
  while [ "$retries" -gt 0 ]; do
    if (exec 3<>"/dev/tcp/$HOST/$PORT") 2>/dev/null; then
      exec 3>&- 3<&-
      return 0
    fi
    sleep 1
    retries=$((retries - 1))
  done
  return 1
}

########################################
# Identity Services (auth/user/notification)
########################################
echo "Starting Identity Services..."
(
  cd "$ROOT_DIR/identity-services" || exit 1
  exec pnpm dev
) &

echo "Waiting for identity (auth-service:5001) to accept connections..."
if wait_port 127.0.0.1 5001; then
  echo "  ✓ auth-service is up"
else
  echo "  ! auth-service not reachable yet; continuing anyway"
fi

########################################
# Gateway Service
########################################
echo "Starting Gateway Service..."
(
  cd "$ROOT_DIR/gateway-service" || exit 1
  exec "$ROOT_DIR/gateway-service/venv/bin/python" main.py
) &

########################################
# Django web servers (migrate then runserver)
########################################
DEPLOY_PYTHON="$ROOT_DIR/deployment-services/venv/bin/python"

run_django_service() {
  SERVICE_PATH="$1"
  PORT="$2"
  PYTHON_EXEC="$3"

  echo "Starting $(basename "$SERVICE_PATH")..."

  (
    cd "$ROOT_DIR/$SERVICE_PATH" || exit 1

    echo "→ Applying migrations..."
    "$PYTHON_EXEC" manage.py makemigrations
    "$PYTHON_EXEC" manage.py migrate

    echo "→ Starting server on port $PORT..."
    exec "$PYTHON_EXEC" manage.py runserver 0.0.0.0:$PORT
  ) &
}

run_django_service "deployment-services/application-service" 8001 "$DEPLOY_PYTHON"
run_django_service "deployment-services/infrastructure-service" 8002 "$DEPLOY_PYTHON"
run_django_service "payment-service" 8003 "$ROOT_DIR/payment-service/venv/bin/python"

echo ""
echo "All app services started."
echo "Press Ctrl+C to stop them."
echo ""

wait
