# Deployment Idempotency Fix

## Problem
When deployments failed partway through, retrying would fail with:
- `InvalidParameterException: Creation of service was not idempotent`
- `PriorityInUseException` for listener rules
- `DuplicateTargetGroupNameException` for target groups

This happened because AWS resources from the failed attempt still existed.

## Root Cause
Deployment creates resources in sequence:
1. Task Definition
2. Target Group
3. Listener Rule
4. ECS Service

If step 4 fails, resources 1-3 remain. Retry attempts to create them again → conflict.

## Solution
Made all resource creation operations idempotent:

### 1. ECS Service (`aws/ecs.py`)
- Check if service exists before creating
- If exists and active → update with new task definition
- If creation fails with "not idempotent" → fetch existing service ARN
- Handles both new deployments and retries

### 2. Target Group (`aws/alb.py`)
- Already handled `DuplicateTargetGroupNameException`
- Returns existing target group ARN if duplicate

### 3. Listener Rule (`aws/alb.py`)
- Catch `PriorityInUseException`
- Find existing rule with same priority
- Update it to point to new target group
- Allows retry to reuse same routing rule

### 4. Retry Endpoint (`api/views/application.py`)
- Clean up partial deployment before retry
- Reset application ARN fields (service, task, target group, listener)
- Ensures fresh deployment state

## Files Modified
- `aws/ecs.py` - Idempotent service creation
- `aws/alb.py` - Idempotent listener rule creation
- `api/views/application.py` - Enhanced retry with cleanup

## Testing
1. Deploy application
2. Kill deployment worker mid-deployment (after target group created)
3. Retry deployment via `/applications/{id}/retry/`
4. Should succeed without "not idempotent" errors
