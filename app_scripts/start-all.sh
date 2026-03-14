#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

cleanup() {
    echo ""
    echo "Stopping all services..."
    pkill -P $$ 2>/dev/null || true
    exit 0
}

trap cleanup SIGINT SIGTERM

echo "Cleaning up stale processes..."
pkill -f "tsx watch" 2>/dev/null || true
pkill -f "manage.py runserver" 2>/dev/null || true
pkill -f "python app.py" 2>/dev/null || true
sleep 2

echo "Starting Launchpad Microservices..."
echo "Root directory: $ROOT_DIR"
echo ""

########################################
# Identity Service
########################################
echo "Starting Identity Services..."
(
  cd "$ROOT_DIR/identity-services" || exit 1
  pnpm dev
) &

sleep 10

########################################
# Gateway Service
########################################
echo "Starting Gateway Service..."
(
  cd "$ROOT_DIR/gateway-service" || exit 1
  "$ROOT_DIR/gateway-service/venv/bin/python" main.py
) &

########################################
# Shared Python for Deployment Services
########################################
DEPLOY_PYTHON="$ROOT_DIR/deployment-services/venv/bin/python"

run_django_service() {
  SERVICE_PATH="$1"
  PORT="$2"
  PYTHON_EXEC="$3"

  echo "Starting $(basename "$SERVICE_PATH")..."

  (
    cd "$ROOT_DIR/$SERVICE_PATH" || exit 1

    echo "→ Checking for model changes..."

    if ! "$PYTHON_EXEC" manage.py makemigrations --check --dry-run > /dev/null 2>&1; then
      echo "   Creating new migrations..."
      "$PYTHON_EXEC" manage.py makemigrations
    else
      echo "   No new migrations needed."
    fi

    echo "→ Checking for unapplied migrations..."

    if ! "$PYTHON_EXEC" manage.py migrate --check > /dev/null 2>&1; then
      echo "   Applying migrations..."
      "$PYTHON_EXEC" manage.py migrate
    else
      echo "   Database up to date."
    fi

    echo "→ Starting server on port $PORT..."
    "$PYTHON_EXEC" manage.py runserver 0.0.0.0:$PORT
  ) &
}

########################################
# Deployment Services
########################################
run_django_service "deployment-services/application-service" 8001 "$DEPLOY_PYTHON"
run_django_service "deployment-services/infrastructure-service" 8002 "$DEPLOY_PYTHON"

########################################
# Payment Service
########################################
run_django_service "payment-service" 8003 "$ROOT_DIR/payment-service/venv/bin/python"

echo ""
echo "All services started."
echo "Press Ctrl+C to stop all services."
echo ""

wait