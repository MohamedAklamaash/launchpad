# Deployment Edge Cases & Fixes

## Fixed Edge Cases

### 1. Target Group Not Attached to ALB
**Error**: `The target group does not have an associated load balancer`

**Root Cause**: ECS service creation attempted before target group was fully attached to ALB via listener rule.

**Fix**:
- Added `verify_target_group_attached()` method with retry logic (10 attempts, 2s delay)
- Waits for AWS to propagate the listener rule attachment
- Verifies `LoadBalancerArns` field is populated before proceeding

### 2. Invalid Fargate CPU/Memory Combinations
**Error**: `No Fargate configuration exists for given values: 204 CPU, 204 memory`

**Root Cause**: User input (0.2 vCPU, 0.2 GB) converted incorrectly to Fargate units.

**Fix**:
- Added validation and rounding to nearest valid Fargate combination
- Enforces min/max limits per CPU tier:
  - 0.25 vCPU → 0.5-2 GB
  - 0.5 vCPU → 1-4 GB
  - 1 vCPU → 2-8 GB
  - 2 vCPU → 4-16 GB
  - 4 vCPU → 8-30 GB

### 3. Docker Hub Rate Limiting
**Error**: `429 Too Many Requests - toomanyrequests`

**Root Cause**: CodeBuild pulls base images from Docker Hub without authentication.

**Fix**:
- Documented requirement to use ECR Public Gallery
- Created `DOCKERFILE_REQUIREMENTS.md` with examples
- Users must use `public.ecr.aws/docker/library/*` images

### 4. Queue Dequeue Unpacking Error
**Error**: `cannot unpack non-iterable NoneType object`

**Root Cause**: `blpop()` returns `None` when queue is empty, code tried to unpack.

**Fix**:
- Check if `result` is not None before unpacking
- Return None gracefully when queue is empty

### 5. Duplicate Resource Creation
**Error**: `DuplicateTargetGroupNameException`, `Service already exists`

**Root Cause**: Retry or redeployment attempts create duplicate resources.

**Fix**:
- Added try/except in `create_target_group()` to fetch existing ARN
- Added try/except in `create_service()` to fetch existing ARN
- Idempotent resource creation

### 6. Service Not Stable
**Issue**: Deployment marked ACTIVE before service actually running.

**Fix**:
- Added `wait_for_service_stable()` method
- Polls every 10s for up to 5 minutes
- Verifies `runningCount == desiredCount`
- Only marks ACTIVE after service is truly running

## Remaining Edge Cases to Handle

### 7. Subnet/Security Group Discovery Failure
**Potential Error**: No subnets or security groups found

**Current Handling**: Raises exception, deployment fails

**Improvement Needed**:
- Validate subnet/SG exist during infrastructure provisioning
- Store subnet/SG IDs in environment metadata
- Add fallback to create default SG if missing

### 8. ALB Listener Not Found
**Potential Error**: `No listener found for ALB`

**Current Handling**: Raises exception

**Improvement Needed**:
- Create default HTTP listener on port 80 if missing
- Store listener ARN in environment metadata during provisioning

### 9. ECR Repository Not Found
**Potential Error**: `RepositoryNotFoundException`

**Current Handling**: CodeBuild fails

**Improvement Needed**:
- Verify ECR repo exists before triggering build
- Create repo if missing (should be created by Terraform)

### 10. GitHub Repository Access Denied
**Potential Error**: `fatal: could not read Username`

**Current Handling**: Build fails

**Improvement Needed**:
- Validate GitHub token before deployment
- Test repository access in pre-deployment validation
- Better error message for private repos

### 11. Dockerfile Not Found
**Potential Error**: `unable to prepare context: unable to evaluate symlinks in Dockerfile path`

**Current Handling**: Build fails

**Improvement Needed**:
- Validate Dockerfile exists at specified path
- Check during application creation (API validation)
- Provide clear error message

### 12. Container Health Check Failures
**Potential Error**: Service starts but tasks keep failing health checks

**Current Handling**: Service stays in ACTIVE but doesn't work

**Improvement Needed**:
- Monitor task health after deployment
- Add health check endpoint validation
- Rollback if tasks fail repeatedly

### 13. Port Mismatch
**Potential Error**: Container exposes port 3000, but we configure 8000

**Current Handling**: Health checks fail, service doesn't work

**Improvement Needed**:
- Allow user to specify container port
- Add port field to Application model
- Validate port matches Dockerfile EXPOSE

### 14. Environment Variable Secrets
**Potential Error**: Sensitive data in plain text envs

**Current Handling**: Stored as plain text

**Improvement Needed**:
- Support AWS Secrets Manager references
- Encrypt sensitive environment variables
- Add `secrets` field separate from `envs`

### 15. Build Timeout
**Potential Error**: Large images take >20 minutes to build

**Current Handling**: CodeBuild times out (default 60 min)

**Improvement Needed**:
- Make build timeout configurable
- Show build progress to user
- Support build caching

### 16. Insufficient IAM Permissions
**Potential Error**: `AccessDeniedException` during deployment

**Current Handling**: Deployment fails with cryptic error

**Improvement Needed**:
- Pre-validate IAM permissions before deployment
- Check role has all required permissions
- Provide clear error message with missing permissions

### 17. VPC Quota Limits
**Potential Error**: `VpcLimitExceeded`, `SubnetLimitExceeded`

**Current Handling**: Infrastructure provisioning fails

**Improvement Needed**:
- Check quotas before provisioning
- Provide clear error with quota increase instructions
- Support existing VPC (don't create new)

### 18. Concurrent Deployments
**Potential Error**: Multiple deployments to same infrastructure

**Current Handling**: May cause conflicts

**Improvement Needed**:
- Add deployment locking per infrastructure
- Queue deployments for same infrastructure
- Prevent concurrent modifications

### 19. Rollback on Failure
**Potential Error**: Deployment fails mid-way, resources left in inconsistent state

**Current Handling**: Resources remain (orphaned)

**Improvement Needed**:
- Track created resources during deployment
- Rollback/cleanup on failure
- Add cleanup endpoint for failed deployments

### 20. Application Name Conflicts
**Potential Error**: Two apps with same name in same infrastructure

**Current Handling**: Resource name conflicts

**Improvement Needed**:
- Enforce unique app names per infrastructure
- Add database constraint
- Validate before deployment

## Validation Checklist

Before deployment starts, validate:

- [ ] Infrastructure status is ACTIVE
- [ ] Infrastructure is cloud authenticated
- [ ] GitHub repository is accessible
- [ ] Dockerfile exists at specified path
- [ ] ECR repository exists
- [ ] ECS cluster exists
- [ ] ALB exists with listener
- [ ] Subnets and security groups exist
- [ ] IAM roles have required permissions
- [ ] Application name is unique in infrastructure
- [ ] CPU/Memory values are valid
- [ ] No other deployment in progress for same infrastructure

## Monitoring & Observability

Add monitoring for:

- [ ] Build duration and success rate
- [ ] Deployment duration and success rate
- [ ] Service health check status
- [ ] Container restart count
- [ ] ALB target health
- [ ] Error rate by deployment phase
- [ ] Queue depth and processing time

## Testing Strategy

Test scenarios:

- [ ] First deployment (happy path)
- [ ] Redeployment (update existing)
- [ ] Concurrent deployments
- [ ] Deployment with invalid Dockerfile
- [ ] Deployment with private GitHub repo
- [ ] Deployment with large image (>1GB)
- [ ] Deployment with failing health checks
- [ ] Deployment with invalid CPU/memory
- [ ] Deployment to infrastructure without ALB
- [ ] Deployment with expired AWS credentials
