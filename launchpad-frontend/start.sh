#!/bin/bash

echo "🚀 Starting Launchpad Frontend..."
echo ""

# Check if node_modules exists
if [ ! -d "node_modules" ]; then
    echo "📦 Installing dependencies..."
    npm install
fi

# Check if .env.local exists
if [ ! -f ".env.local" ]; then
    echo "⚙️  Creating .env.local..."
    cat > .env.local << EOF
NEXT_PUBLIC_API_GATEWAY_URL=http://localhost:8000
NEXT_PUBLIC_AUTH_SERVICE_URL=http://localhost:5001
NEXT_PUBLIC_INFRASTRUCTURE_SERVICE_URL=http://localhost:8002
NEXT_PUBLIC_APPLICATION_SERVICE_URL=http://localhost:8001
EOF
fi

echo ""
echo "✅ Starting development server..."
echo "📍 Frontend: http://localhost:3000"
echo ""
echo "Make sure backend services are running:"
echo "  - Auth Service: http://localhost:5001"
echo "  - Gateway: http://localhost:8000"
echo "  - Infrastructure Service: http://localhost:8002"
echo "  - Application Service: http://localhost:8001"
echo ""

npm run dev
