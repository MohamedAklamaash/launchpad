#!/bin/bash

set -e  # Exit on failure

echo "Starting Launchpad environment setup..."

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

############################################
# 1. Identity Services (Shared Node Modules)
############################################
echo "Setting up Identity Services (Node)..."

if ! command -v pnpm &> /dev/null; then
    echo "Error: pnpm is not installed. Please install it first (npm install -g pnpm)."
    exit 1
fi

cd "$ROOT_DIR/identity-services"
if [ -f "package.json" ]; then
    echo "Installing Node dependencies with pnpm..."
    pnpm install
    echo "Building Node packages..."
    pnpm build
else
    echo "No package.json found in identity-services"
fi

cd "$ROOT_DIR"

############################################
# 2. Gateway Service (Independent Python venv)
############################################
echo "Setting up Gateway Service (Python)..."

cd "$ROOT_DIR/gateway-service"

if [ ! -d "venv" ]; then
    python3 -m venv venv
fi

source venv/bin/activate

if [ -f "requirements.txt" ]; then
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt
else
    echo "requirements.txt not found in gateway-service"
fi

deactivate

cd "$ROOT_DIR"

############################################
# 3. Deployment Services (Shared Python venv)
############################################
echo "Setting up Deployment Services (Shared Python venv)..."

cd "$ROOT_DIR/deployment-services"

if [ ! -d "venv" ]; then
    python3 -m venv venv
fi

source venv/bin/activate

if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
else
    echo "requirements.txt not found in deployment-services"
fi

deactivate
cd "$ROOT_DIR"

############################################
# 4. Payment Service (Independent Python venv)
############################################
echo "Setting up Payment Service (Python)..."

cd "$ROOT_DIR/payment-service"

if [ ! -d "venv" ]; then
    python3 -m venv venv
fi

source venv/bin/activate
pip install -r requirements.txt
deactivate

cd "$ROOT_DIR"

############################################
# 5. Connect Python Shared Library (Symlinks)
############################################
echo "Symlinking Python shared directories..."

rm -rf deployment-services/application-service/shared
ln -s ../shared deployment-services/application-service/shared

rm -rf deployment-services/infrastructure-service/shared
ln -s ../shared deployment-services/infrastructure-service/shared

rm -rf payment-service/shared
ln -s ../deployment-services/shared payment-service/shared

echo "Environment setup complete."