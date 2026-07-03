#!/bin/bash
#
# Start all deployment workers on the host: the application deployment worker
# and the infrastructure provisioning worker (both `manage.py run_worker`).
# Databases and RabbitMQ must already be running (see start-dbs.sh). Migrations
# are owned by the app services (see start-apps.sh) and are NOT run here.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DEPLOY_PYTHON="$ROOT_DIR/deployment-services/venv/bin/python"

cleanup() {
    echo ""
    echo "Stopping workers..."
    pkill -P $$ 2>/dev/null || true
    exit 0
}

trap cleanup SIGINT SIGTERM

echo "Cleaning up stale worker processes..."
pkill -f "manage.py run_worker" 2>/dev/null || true
sleep 2

echo "Starting Launchpad workers..."
echo "Root directory: $ROOT_DIR"
echo ""

run_worker() {
  SERVICE_PATH="$1"

  echo "Starting worker: $(basename "$SERVICE_PATH")..."
  (
    cd "$ROOT_DIR/$SERVICE_PATH" || exit 1
    "$DEPLOY_PYTHON" manage.py run_worker
  ) &
}

run_worker "deployment-services/application-service"
run_worker "deployment-services/infrastructure-service"

echo ""
echo "All workers started."
echo "Press Ctrl+C to stop them."
echo ""

wait
