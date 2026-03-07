# Stateless Terraform Infrastructure Orchestration

Production-ready stateless infrastructure provisioning system using Terraform, Redis queue, and worker processes.

## Architecture

```
Client → API Server → Redis Queue → Worker Pool → AWS/Cloud
                          ↓
                      Database
```

### Key Features

- **Stateless**: No disk persistence, uses S3 for Terraform state
- **Async**: Queue-based architecture with Redis
- **Automatic Rollback**: Failed provisions trigger `terraform destroy`
- **Scalable**: Horizontal worker scaling
- **Idempotent**: Safe to retry operations

## Components

### 1. API Server
- Accepts infrastructure requests
- Validates and stores metadata in DB
- Enqueues provisioning jobs

### 2. Redis Queue
- `infra:provision` - Provision jobs
- `infra:destroy` - Destroy jobs

### 3. Worker Pool
- Stateless Terraform execution
- Uses `/dev/shm` (RAM) for ephemeral workspace
- Automatic cleanup after each job

### 4. Database
- Infrastructure metadata
- Environment status tracking
- Terraform outputs

## Infrastructure Lifecycle

```
pending → provisioning → active
                ↓
              error (auto-destroyed)
```

## Setup

### Prerequisites

```bash
# Install Terraform
wget https://releases.hashicorp.com/terraform/1.7.0/terraform_1.7.0_linux_amd64.zip
unzip terraform_1.7.0_linux_amd64.zip
sudo mv terraform /usr/local/bin/

# Install Redis
sudo apt install redis-server
```

### Environment Variables

Add to `.env`:

```bash
REDIS_HOST=localhost
REDIS_PORT=6379
```

### Run Worker

#### Option 1: Direct Python

```bash
cd deployment-services/infrastructure-service
source ../venv/bin/activate
python worker.py
```

#### Option 2: Docker Compose

```bash
docker-compose -f docker-compose.worker.yml up -d
```

#### Option 3: Systemd Service

```bash
sudo cp infra-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable infra-worker
sudo systemctl start infra-worker
sudo systemctl status infra-worker
```

### Scale Workers

```bash
# Run multiple workers
python worker.py &
python worker.py &
python worker.py &

# Or with Docker
docker-compose -f docker-compose.worker.yml up -d --scale infra-worker=5
```

## API Usage

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

Response:
```json
{
  "id": "019cc6ec-8fc0-761b-a170-fa5edbd05dd3",
  "status": "provisioning"
}
```

### Check Status

```bash
GET /api/v1/infrastructures/019cc6ec-8fc0-761b-a170-fa5edbd05dd3
```

Response:
```json
{
  "id": "019cc6ec-8fc0-761b-a170-fa5edbd05dd3",
  "status": "active",
  "environment": {
    "vpc_id": "vpc-abc123",
    "cluster_arn": "arn:aws:ecs:...",
    "alb_dns": "my-alb-123.us-west-2.elb.amazonaws.com"
  }
}
```

### Delete Infrastructure

```bash
DELETE /api/v1/infrastructures/019cc6ec-8fc0-761b-a170-fa5edbd05dd3
```

## Terraform State Management

State is stored in S3 with DynamoDB locking:

- **Bucket**: `launchpad-tf-state`
- **Key**: `infra/{infra_id}/terraform.tfstate`
- **Lock Table**: `launchpad-tf-locks`

Backend is automatically bootstrapped on first use.

## Failure Handling

### Provision Failure

1. `terraform apply` fails
2. Worker runs `terraform destroy -auto-approve`
3. Status set to `FAILED`
4. Error logged in database
5. Notification sent (TODO)

### Worker Crash

- Job remains in queue
- Another worker picks it up
- Idempotent operations prevent duplicates

## Monitoring

### Queue Length

```bash
redis-cli LLEN infra:provision
redis-cli LLEN infra:destroy
```

### Worker Logs

```bash
# Systemd
sudo journalctl -u infra-worker -f

# Docker
docker-compose -f docker-compose.worker.yml logs -f
```

### Database Status

```sql
SELECT status, COUNT(*) FROM environments GROUP BY status;
```

## Security

- Credentials stored encrypted in database
- No credentials in logs
- Ephemeral workspace in `/dev/shm`
- Automatic cleanup after execution

## Scaling Strategy

### Horizontal Scaling

```bash
# Add workers based on queue depth
if redis-cli LLEN infra:provision > 10; then
  docker-compose -f docker-compose.worker.yml up -d --scale infra-worker=10
fi
```

### Kubernetes

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: infra-worker
spec:
  replicas: 5
  template:
    spec:
      containers:
      - name: worker
        image: launchpad/infra-worker:latest
        volumeMounts:
        - name: shm
          mountPath: /dev/shm
      volumes:
      - name: shm
        emptyDir:
          medium: Memory
```

## Troubleshooting

### Worker not processing jobs

```bash
# Check Redis connection
redis-cli ping

# Check queue
redis-cli LLEN infra:provision

# Check worker logs
python worker.py
```

### Terraform state locked

```bash
# Check DynamoDB locks
aws dynamodb scan --table-name launchpad-tf-locks

# Force unlock (dangerous)
terraform force-unlock <lock-id>
```

### Out of memory

- Increase `/dev/shm` size: `mount -o remount,size=2G /dev/shm`
- Or use `/tmp` (slower but more space)

## Migration from Old System

1. Stop existing threading-based provisioning
2. Deploy Redis
3. Start workers
4. Update API to use queue
5. Migrate existing `/tmp` state to S3

## TODO

- [ ] Add notification service integration
- [ ] Implement retry logic with exponential backoff
- [ ] Add metrics (Prometheus)
- [ ] Add distributed tracing
- [ ] Implement job priorities
- [ ] Add webhook callbacks
