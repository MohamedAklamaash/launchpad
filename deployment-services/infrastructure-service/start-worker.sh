#!/bin/bash
set -e

echo "🚀 Starting Infrastructure Orchestration System"

# Check prerequisites
command -v terraform >/dev/null 2>&1 || { echo "❌ Terraform not installed"; exit 1; }
command -v redis-cli >/dev/null 2>&1 || { echo "❌ Redis not installed"; exit 1; }

# Check Redis is running
redis-cli ping >/dev/null 2>&1 || { echo "❌ Redis not running. Start with: sudo systemctl start redis"; exit 1; }

echo "✅ Prerequisites OK"

# Install Python dependencies
if [ ! -d "../venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv ../venv
fi

echo "📦 Installing dependencies..."
source ../venv/bin/activate
pip install -q -r ../requirements.txt

# Run migrations
echo "🗄️  Running migrations..."
python manage.py migrate

# Start worker in background
echo "👷 Starting worker..."
python worker.py &
WORKER_PID=$!

echo "✅ Worker started (PID: $WORKER_PID)"
echo ""
echo "📊 Monitor queue: redis-cli LLEN infra:provision"
echo "📋 View logs: tail -f worker.log"
echo "🛑 Stop worker: kill $WORKER_PID"
echo ""
echo "Press Ctrl+C to stop..."

# Wait for interrupt
trap "kill $WORKER_PID 2>/dev/null; echo ''; echo '👋 Stopped'; exit 0" INT TERM

wait $WORKER_PID
