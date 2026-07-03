#!/bin/bash
#
# Start the infra tier only (Postgres, MySQL, Mongo, Redis, RabbitMQ,
# Prometheus, Grafana) via the infra-only compose file. Apps and workers
# run on the host — see start-apps.sh and start-workers.sh.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DOCKER_DIR="$ROOT_DIR/infra/.docker"
COMPOSE_FILE="$DOCKER_DIR/docker-compose.infra.yml"

echo "Starting infra stack from $COMPOSE_FILE ..."
docker compose -f "$COMPOSE_FILE" up -d

echo ""
echo "Waiting for services with healthchecks to become healthy..."

wait_healthy() {
  local container="$1"
  local retries=30
  while [ "$retries" -gt 0 ]; do
    local status
    status="$(docker inspect -f '{{.State.Health.Status}}' "$container" 2>/dev/null || echo "missing")"
    case "$status" in
      healthy)
        echo "  ✓ $container healthy"
        return 0
        ;;
      missing)
        echo "  ! $container not found"
        return 1
        ;;
    esac
    sleep 2
    retries=$((retries - 1))
  done
  echo "  ✗ $container did not become healthy in time"
  return 1
}

for c in postgres mysql redis rabbitmq; do
  wait_healthy "$c" || true
done

echo ""
echo "Infra is up. Bring it down with:"
echo "  docker compose -f $COMPOSE_FILE down"
