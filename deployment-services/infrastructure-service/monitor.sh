#!/bin/bash

# Infrastructure Orchestration Monitoring

echo "📊 Infrastructure Orchestration Status"
echo "======================================"
echo ""

# Redis Queue Status
echo "🔄 Queue Status:"
PROVISION_COUNT=$(redis-cli LLEN infra:provision 2>/dev/null || echo "N/A")
DESTROY_COUNT=$(redis-cli LLEN infra:destroy 2>/dev/null || echo "N/A")
echo "  Provision queue: $PROVISION_COUNT jobs"
echo "  Destroy queue:   $DESTROY_COUNT jobs"
echo ""

# Worker Status
echo "👷 Worker Status:"
WORKER_COUNT=$(pgrep -f "python.*worker.py" | wc -l)
if [ "$WORKER_COUNT" -gt 0 ]; then
    echo "  ✅ $WORKER_COUNT worker(s) running"
    pgrep -f "python.*worker.py" | while read pid; do
        echo "     PID: $pid"
    done
else
    echo "  ❌ No workers running"
fi
echo ""

# Database Status (requires psql access)
if command -v psql >/dev/null 2>&1 && [ -n "$DB_NAME" ]; then
    echo "🗄️  Infrastructure Status:"
    psql -h ${DB_HOST:-localhost} -U ${DB_USER:-postgres} -d ${DB_NAME} -t -c "
        SELECT 
            status, 
            COUNT(*) as count 
        FROM environments 
        GROUP BY status
    " 2>/dev/null | while read line; do
        echo "  $line"
    done
    echo ""
fi

# Recent Logs
echo "📋 Recent Worker Activity:"
if [ -f "worker.log" ]; then
    tail -n 5 worker.log
else
    echo "  No log file found"
fi
echo ""

# System Resources
echo "💻 System Resources:"
echo "  Memory: $(free -h | awk '/^Mem:/ {print $3 "/" $2}')"
echo "  /dev/shm: $(df -h /dev/shm | awk 'NR==2 {print $3 "/" $2 " (" $5 " used)"}')"
echo ""

# Terraform State
if command -v aws >/dev/null 2>&1; then
    echo "☁️  Terraform State:"
    STATE_COUNT=$(aws s3 ls s3://launchpad-tf-state/infra/ --recursive 2>/dev/null | wc -l || echo "N/A")
    echo "  State files: $STATE_COUNT"
fi
