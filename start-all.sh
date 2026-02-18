#!/bin/bash
cleanup() {
    echo "Stopping all services..."
    pkill -P $$
    exit
}

trap cleanup SIGINT SIGTERM

echo "Starting Launchpad Microservices..."

echo "Starting Identity Services..."
(cd identity-services && pnpm dev) &

sleep 5

echo "Starting Gateway Service..."
(cd gateway-service && venv/bin/python app.py) &

echo "Starting Application Service..."
(cd deployment-services/application-service && ../venv/bin/python manage.py runserver 8001) &

echo "Starting Infrastructure Service..."
(cd deployment-services/infrastructure-service && ../venv/bin/python manage.py runserver 8002) &

echo "Starting Payment Service..."
(cd payment-service && venv/bin/python manage.py runserver 8003) &

echo "All services started. Press Ctrl+C to stop all."
wait
