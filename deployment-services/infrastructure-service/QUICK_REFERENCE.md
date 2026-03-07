# Quick Reference Card

## 🚀 Quick Start

```bash
# 1. Install Redis
sudo apt install redis-server
sudo systemctl start redis

# 2. Add to .env
echo "REDIS_HOST=localhost" >> .env
echo "REDIS_PORT=6379" >> .env

# 3. Install dependencies
pip install redis==5.0.1

# 4. Run migrations
python manage.py migrate

# 5. Start worker
./start-worker.sh
```

## 📋 Common Commands

### Worker Management
```bash
# Start worker
python worker.py

# Start multiple workers
python worker.py & python worker.py & python worker.py &

# Stop workers
pkill -f worker.py

# Check worker status
ps aux | grep worker.py
```

### Queue Management
```bash
# Check queue length
redis-cli LLEN infra:provision
redis-cli LLEN infra:destroy

# View queue contents
redis-cli LRANGE infra:provision 0 -1

# Clear queue (emergency)
redis-cli DEL infra:provision
redis-cli DEL infra:destroy
```

### Monitoring
```bash
# Full status
./monitor.sh

# Watch queue
watch -n 1 'redis-cli LLEN infra:provision'

# View logs
tail -f worker.log

# Database status
psql -d $DB_NAME -c "SELECT status, COUNT(*) FROM environments GROUP BY status;"
```

## 🔧 Troubleshooting

### Worker not processing jobs
```bash
# 1. Check Redis
redis-cli ping

# 2. Check queue
redis-cli LLEN infra:provision

# 3. Check worker
ps aux | grep worker.py

# 4. Restart worker
pkill -f worker.py && python worker.py
```

### Out of memory
```bash
# Increase /dev/shm
sudo mount -o remount,size=4G /dev/shm

# Check usage
df -h /dev/shm
```

### Terraform state locked
```bash
# List locks
aws dynamodb scan --table-name launchpad-tf-locks

# Force unlock (dangerous!)
terraform force-unlock <lock-id>
```

## 📊 API Endpoints

### Create Infrastructure
```bash
POST /api/v1/infrastructures
{
  "name": "my-infra",
  "cloud_provider": "AWS",
  "code": "123456789012",
  "max_cpu": 2.0,
  "max_memory": 4096,
  "metadata": {
    "aws_region": "us-west-2",
    "vpc_cidr": "10.0.0.0/16"
  }
}
```

### Get Status
```bash
GET /api/v1/infrastructures/{infra_id}
```

### Delete Infrastructure
```bash
DELETE /api/v1/infrastructures/{infra_id}
```

## 🎯 Key Files

| File | Purpose |
|------|---------|
| `worker.py` | Worker process |
| `api/services/terraform_worker.py` | Terraform execution |
| `api/services/infra_queue.py` | Queue management |
| `api/services/notification.py` | Notifications |
| `start-worker.sh` | Quick start |
| `monitor.sh` | Monitoring |

## 🔐 Environment Variables

```bash
# Required
REDIS_HOST=localhost
REDIS_PORT=6379
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...

# Optional
DB_HOST=localhost
DB_PORT=5432
DB_NAME=infrastructure_db
```

## 📈 Scaling

### Horizontal Scaling
```bash
# Docker
docker-compose -f docker-compose.worker.yml up -d --scale infra-worker=10

# Manual
for i in {1..10}; do python worker.py & done
```

### Auto-Scaling
```bash
# Add to cron
*/5 * * * * /path/to/autoscale.sh
```

## 🚨 Emergency Procedures

### Stop all workers
```bash
pkill -f worker.py
```

### Clear all queues
```bash
redis-cli FLUSHDB
```

### Reset infrastructure
```bash
# Mark all as failed
psql -d $DB_NAME -c "UPDATE environments SET status='FAILED' WHERE status='PROVISIONING';"
```

## 📞 Health Checks

```bash
# Redis
redis-cli ping

# Worker
pgrep -f worker.py

# Queue
redis-cli LLEN infra:provision

# Database
psql -d $DB_NAME -c "SELECT 1;"
```

## 🎓 Best Practices

1. **Always run at least 2 workers** for redundancy
2. **Monitor queue length** - scale if > 10
3. **Check /dev/shm usage** - increase if needed
4. **Review logs daily** for errors
5. **Test rollback** in staging first
6. **Backup S3 state** regularly

## 📚 Documentation

- Full guide: `TERRAFORM_WORKER.md`
- Implementation: `IMPLEMENTATION_SUMMARY.md`
- Comparison: `COMPARISON.md`
- This file: `QUICK_REFERENCE.md`
