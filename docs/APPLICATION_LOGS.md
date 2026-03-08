# Application Deployment Logs

## Where Logs Are Stored

### 1. Application Status (Database)

**Table**: `api_application`  
**Database**: `application_db`

**Fields**:
- `status` - Current deployment status (CREATED, BUILDING, DEPLOYING, ACTIVE, FAILED)
- `error_message` - Error details if deployment failed
- `build_id` - CodeBuild job ID for viewing build logs

```sql
-- Check application status
SELECT id, name, status, error_message, build_id, deployment_url 
FROM api_application 
WHERE id = 'your-app-id';
```

### 2. CodeBuild Logs (AWS CloudWatch)

**Location**: AWS CloudWatch Logs  
**Log Group**: `/aws/codebuild/launchpad-build-{infrastructure-id}`  
**Log Stream**: Build ID from `application.build_id`

**Access**:
1. AWS Console → CloudWatch → Log groups
2. Find log group: `/aws/codebuild/launchpad-build-{infrastructure-id}`
3. Find log stream matching `build_id`

**Via AWS CLI**:
```bash
# Get build logs
aws logs tail /aws/codebuild/launchpad-build-{infrastructure-id} --follow

# Get specific build
aws codebuild batch-get-builds --ids {build-id}
```

### 3. Container Runtime Logs (AWS CloudWatch)

**Location**: AWS CloudWatch Logs  
**Log Group**: `/ecs/{app-name}-task`  
**Created**: After ECS service starts

**Access**:
1. AWS Console → CloudWatch → Log groups
2. Find log group: `/ecs/{app-name}-task`
3. View real-time container logs

**Via AWS CLI**:
```bash
# Tail container logs
aws logs tail /ecs/{app-name}-task --follow

# Get recent logs
aws logs tail /ecs/{app-name}-task --since 1h
```

### 4. Deployment Worker Logs (Local)

**Location**: Application service logs  
**Output**: stdout/stderr or systemd journal

**View**:
```bash
# If running in terminal
# Logs appear in stdout

# If running as systemd service
sudo journalctl -u deployment-worker -f

# Or check application service logs
tail -f /path/to/application-service/logs/deployment.log
```

## Log Locations by Deployment Phase

| Phase | Status | Logs Location |
|-------|--------|---------------|
| Queued | CREATED | Database only |
| Building | BUILDING | CodeBuild CloudWatch |
| Pushing | PUSHING_IMAGE | CodeBuild CloudWatch |
| Deploying | DEPLOYING | Deployment worker logs |
| Running | ACTIVE | Container CloudWatch |
| Failed | FAILED | Database (error_message) + CloudWatch |

## Viewing Logs

### 1. Check Application Status

```bash
GET /applications/{app-id}
```

Response includes:
```json
{
  "id": "...",
  "name": "my-app",
  "status": "BUILDING",
  "build_id": "launchpad-build-...:abc123",
  "error_message": null,
  "deployment_url": null
}
```

### 2. View Build Logs (if status = BUILDING)

```bash
# Get build details
aws codebuild batch-get-builds --ids {build-id}

# View logs
aws logs tail /aws/codebuild/launchpad-build-{infra-id} --follow
```

### 3. View Container Logs (if status = ACTIVE)

```bash
# View application logs
aws logs tail /ecs/{app-name}-task --follow
```

### 4. Check Error Details (if status = FAILED)

```sql
SELECT error_message FROM api_application WHERE id = '{app-id}';
```

Or via API:
```bash
GET /applications/{app-id}
# Check error_message field
```

## Common Log Queries

### Find Failed Deployments

```sql
SELECT id, name, status, error_message, updated_at
FROM api_application
WHERE status = 'FAILED'
ORDER BY updated_at DESC;
```

### Find In-Progress Deployments

```sql
SELECT id, name, status, build_id, updated_at
FROM api_application
WHERE status IN ('BUILDING', 'DEPLOYING')
ORDER BY updated_at DESC;
```

### Get Build Logs for Failed Build

```bash
# From database, get build_id
BUILD_ID=$(psql -d application_db -t -c "SELECT build_id FROM api_application WHERE id = '{app-id}'")

# Get build details
aws codebuild batch-get-builds --ids $BUILD_ID

# View logs
aws logs get-log-events \
  --log-group-name /aws/codebuild/launchpad-build-{infra-id} \
  --log-stream-name $BUILD_ID
```

## Log Retention

- **Database**: Permanent (until application deleted)
- **CodeBuild Logs**: 30 days (configurable in CloudWatch)
- **Container Logs**: 7 days (configurable in CloudWatch)
- **Worker Logs**: Depends on systemd/logging configuration

## Debugging Workflow

1. **Check application status**:
   ```bash
   GET /applications/{app-id}
   ```

2. **If BUILDING**: Check CodeBuild logs
   ```bash
   aws logs tail /aws/codebuild/launchpad-build-{infra-id} --follow
   ```

3. **If FAILED**: Check error_message
   ```bash
   GET /applications/{app-id}
   # Look at error_message field
   ```

4. **If ACTIVE but not working**: Check container logs
   ```bash
   aws logs tail /ecs/{app-name}-task --follow
   ```

## Example: Full Log Trace

```bash
# 1. Get application details
curl http://localhost:8001/api/v1/applications/{app-id}

# Response:
{
  "status": "FAILED",
  "build_id": "launchpad-build-019ccc43:abc123",
  "error_message": "Build failed with status: FAILED"
}

# 2. Get build logs
aws codebuild batch-get-builds --ids launchpad-build-019ccc43:abc123

# 3. View detailed logs
aws logs tail /aws/codebuild/launchpad-build-019ccc43 --follow

# 4. Check specific error
aws logs filter-log-events \
  --log-group-name /aws/codebuild/launchpad-build-019ccc43 \
  --filter-pattern "ERROR"
```

## Summary

**Logs are NOT stored in a single table**. They are distributed:

1. **Status & Errors**: `api_application` table (database)
2. **Build Logs**: AWS CloudWatch (`/aws/codebuild/...`)
3. **Runtime Logs**: AWS CloudWatch (`/ecs/...`)
4. **Worker Logs**: systemd journal or stdout

Use the `status` field to determine which logs to check!
