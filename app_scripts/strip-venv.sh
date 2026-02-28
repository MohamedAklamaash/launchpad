#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "Starting structured environment cleanup..."
echo "Root directory: $ROOT_DIR"
echo ""

############################################
# 1. Identity Services
############################################
echo "Cleaning Identity Services..."

cd "$ROOT_DIR/identity-services" || exit 1

rm -rf node_modules
rm -rf packages/common/node_modules
rm -rf services/*/node_modules
rm -rf packages/common/dist
rm -rf services/*/dist

############################################
# 2. Gateway Service
############################################
echo "Cleaning Gateway Service..."

cd "$ROOT_DIR/gateway-service" || exit 1

rm -rf venv
find . -name "__pycache__" -type d -exec rm -rf {} +
find . -name "*.pyc" -type f -delete

############################################
# 3. Deployment Services
############################################
echo "Cleaning Deployment Services..."

cd "$ROOT_DIR/deployment-services" || exit 1

rm -rf venv
find . -name "__pycache__" -type d -exec rm -rf {} +
find . -name "*.pyc" -type f -delete

############################################
# 4. Payment Service
############################################
echo "Cleaning Payment Service..."

cd "$ROOT_DIR/payment-service" || exit 1

rm -rf venv
find . -name "__pycache__" -type d -exec rm -rf {} +
find . -name "*.pyc" -type f -delete

echo ""
echo "Structured cleanup complete."