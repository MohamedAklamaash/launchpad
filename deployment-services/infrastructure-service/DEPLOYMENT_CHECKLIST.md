# Deployment Checklist

## Pre-Deployment

### Infrastructure
- [ ] Redis installed and running
  ```bash
  sudo apt install redis-server
  sudo systemctl start redis
  sudo systemctl enable redis
  redis-cli ping  # Should return PONG
  ```

- [ ] Terraform installed
  ```bash
  terraform --version  # Should be >= 1.0
  ```

- [ ] AWS credentials configured
  ```bash
  aws sts get-caller-identity  # Should return account info
  ```

- [ ] Sufficient /dev/shm space
  ```bash
  df -h /dev/shm  # Should have at least 2GB
  # If not: sudo mount -o remount,size=4G /dev/shm
  ```

### Configuration
- [ ] Add Redis config to `.env`
  ```bash
  echo "REDIS_HOST=localhost" >> .env
  echo "REDIS_PORT=6379" >> .env
  ```

- [ ] Verify all environment variables
  ```bash
  cat .env | grep -E "REDIS|AWS|DB"
  ```

- [ ] Install Python dependencies
  ```bash
  pip install redis==5.0.1
  ```

### Database
- [ ] Run migrations
  ```bash
  python manage.py migrate
  ```

- [ ] Verify environment table exists
  ```bash
  python manage.py dbshell
  \dt environments
  \q
  ```

## Deployment

### Step 1: Test Worker Locally
- [ ] Start worker in foreground
  ```bash
  python worker.py
  ```

- [ ] Verify worker connects to Redis
  ```
  # Should see: "Infrastructure worker started"
  ```

- [ ] Stop worker (Ctrl+C)

### Step 2: Test Provision Flow
- [ ] Create test infrastructure via API
  ```bash
  curl -X POST http://localhost:8000/api/v1/infrastructures \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $TOKEN" \
    -d '{
      "name": "test-infra",
      "cloud_provider": "AWS",
      "code": "123456789012",
      "max_cpu": 1.0,
      "max_memory": 2048
    }'
  ```

- [ ] Verify job in queue
  ```bash
  redis-cli LLEN infra:provision  # Should be 1
  ```

- [ ] Start worker and watch logs
  ```bash
  python worker.py
  ```

- [ ] Verify provision completes
  ```
  # Should see: "Infrastructure ... provisioned successfully"
  ```

- [ ] Check database status
  ```bash
  psql -d $DB_NAME -c "SELECT id, status FROM environments;"
  ```

### Step 3: Test Failure Rollback
- [ ] Create infrastructure with invalid config
  ```bash
  # Use invalid AWS region or CIDR
  ```

- [ ] Verify automatic destroy runs
  ```
  # Worker logs should show: "Apply failed, triggering destroy"
  ```

- [ ] Verify status is FAILED
  ```bash
  psql -d $DB_NAME -c "SELECT status FROM environments WHERE status='FAILED';"
  ```

### Step 4: Production Deployment

#### Option A: Systemd Service
- [ ] Copy service file
  ```bash
  sudo cp infra-worker.service /etc/systemd/system/
  ```

- [ ] Update service file paths
  ```bash
  sudo nano /etc/systemd/system/infra-worker.service
  # Update WorkingDirectory and ExecStart paths
  ```

- [ ] Reload systemd
  ```bash
  sudo systemctl daemon-reload
  ```

- [ ] Enable service
  ```bash
  sudo systemctl enable infra-worker
  ```

- [ ] Start service
  ```bash
  sudo systemctl start infra-worker
  ```

- [ ] Check status
  ```bash
  sudo systemctl status infra-worker
  ```

- [ ] View logs
  ```bash
  sudo journalctl -u infra-worker -f
  ```

#### Option B: Docker
- [ ] Build worker image
  ```bash
  docker build -f Dockerfile.worker -t infra-worker .
  ```

- [ ] Start services
  ```bash
  docker-compose -f docker-compose.worker.yml up -d
  ```

- [ ] Check status
  ```bash
  docker-compose -f docker-compose.worker.yml ps
  ```

- [ ] View logs
  ```bash
  docker-compose -f docker-compose.worker.yml logs -f
  ```

## Post-Deployment

### Monitoring Setup
- [ ] Set up monitoring script
  ```bash
  chmod +x monitor.sh
  ./monitor.sh
  ```

- [ ] Add to cron for alerts
  ```bash
  crontab -e
  # Add: */5 * * * * /path/to/monitor.sh | mail -s "Infra Status" admin@example.com
  ```

### Health Checks
- [ ] Redis health
  ```bash
  redis-cli ping
  ```

- [ ] Worker health
  ```bash
  ps aux | grep worker.py
  ```

- [ ] Queue health
  ```bash
  redis-cli LLEN infra:provision
  redis-cli LLEN infra:destroy
  ```

- [ ] Database health
  ```bash
  psql -d $DB_NAME -c "SELECT COUNT(*) FROM environments;"
  ```

### Scaling
- [ ] Determine worker count
  ```
  # Rule of thumb: 1 worker per 10 expected concurrent provisions
  ```

- [ ] Start additional workers
  ```bash
  # Systemd: Edit service file, increase instances
  # Docker: docker-compose up -d --scale infra-worker=5
  # Manual: python worker.py & (repeat)
  ```

- [ ] Verify all workers running
  ```bash
  ps aux | grep worker.py | wc -l
  ```

## Validation

### Functional Tests
- [ ] Create infrastructure
- [ ] Check status becomes READY
- [ ] Verify outputs in database
- [ ] Delete infrastructure
- [ ] Verify status becomes DESTROYED

### Performance Tests
- [ ] Create 10 infrastructures simultaneously
- [ ] Monitor queue length
- [ ] Verify all complete successfully
- [ ] Check average provision time

### Failure Tests
- [ ] Test with invalid credentials
- [ ] Test with invalid region
- [ ] Test with invalid CIDR
- [ ] Verify automatic rollback
- [ ] Verify notifications sent

## Rollback Plan

If deployment fails:

1. [ ] Stop workers
   ```bash
   sudo systemctl stop infra-worker
   # or
   docker-compose -f docker-compose.worker.yml down
   ```

2. [ ] Revert code changes
   ```bash
   git revert HEAD
   ```

3. [ ] Restart old system
   ```bash
   python manage.py runserver
   ```

4. [ ] Mark in-progress provisions as failed
   ```bash
   psql -d $DB_NAME -c "UPDATE environments SET status='FAILED' WHERE status='PROVISIONING';"
   ```

## Documentation

- [ ] Update team wiki with new architecture
- [ ] Document worker management procedures
- [ ] Create runbook for common issues
- [ ] Train team on new system

## Sign-Off

- [ ] Development tested
- [ ] Staging tested
- [ ] Production deployed
- [ ] Monitoring configured
- [ ] Team trained
- [ ] Documentation complete

---

**Deployed by:** _________________

**Date:** _________________

**Sign-off:** _________________
