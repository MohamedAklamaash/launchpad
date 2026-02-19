#!/bin/bash
cleanup() {
    echo "Stopping all services..."
    pkill -P $$
    exit
}

trap cleanup SIGINT SIGTERM

echo "Cleaning up stale processes..."
pkill -f "tsx watch" || true
pkill -f "manage.py runserver" || true
pkill -f "python app.py" || true
sleep 2

echo "Starting Launchpad Microservices..."

echo "Starting Identity Services..."
(cd identity-services && pnpm dev) &

sleep 10

echo "Starting Gateway Service..."
(cd gateway-service && venv/bin/python app.py) &

# Shared venv for deployment-services (application + infrastructure)
DEPLOY_PYTHON="$(pwd)/deployment-services/venv/bin/python"

echo "Starting Application Service..."
(cd deployment-services/application-service && "$DEPLOY_PYTHON" manage.py migrate && "$DEPLOY_PYTHON" manage.py runserver 0.0.0.0:8001) &

echo "Starting Infrastructure Service..."
(cd deployment-services/infrastructure-service && "$DEPLOY_PYTHON" manage.py migrate && "$DEPLOY_PYTHON" manage.py runserver 0.0.0.0:8002) &

echo "Starting Payment Service..."
(cd payment-service && venv/bin/python manage.py migrate && venv/bin/python manage.py runserver 0.0.0.0:8003) &

echo "All services started. Press Ctrl+C to stop all."
wait
