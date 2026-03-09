# Application Deletion Fix

## Problem
When users deleted an application, only the database record was removed. All AWS resources remained in the user's account, causing:
- Orphaned ECS services consuming resources
- Unused target groups and listener rules
- Stale task definitions
- CloudWatch log groups accumulating data
- Continued AWS billing for unused resources

## Solution
Created `ApplicationCleanupService` that properly destroys all AWS resources before deleting the database record.

## Resources Cleaned Up (in order)

1. **ECS Service**
   - Scales service to 0 tasks
   - Force deletes the service

2. **ALB Listener Rule**
   - Removes path-based routing rule

3. **Target Group**
   - Deletes the ALB target group

4. **Task Definition**
   - Deregisters the ECS task definition

5. **CloudWatch Log Group**
   - Deletes application logs

## Files Modified

- `api/services/application_cleanup_service.py` (new)
- `api/services/application_service.py` (updated)

## Behavior

- Cleanup runs synchronously before database deletion
- If AWS cleanup fails, error is logged but database deletion continues
- Uses same AWS session/credentials as deployment
- Gracefully handles missing resources (already deleted)

## Resources NOT Deleted

These are shared across applications and managed at infrastructure level:
- CodeBuild project (shared by all apps in infrastructure)
- CodeBuild IAM role (shared)
- Security group rules (may be used by other apps)
- VPC, subnets, NAT gateway (infrastructure-level)
- ECR repository (infrastructure-level)
- ECS cluster (infrastructure-level)
- ALB (infrastructure-level)

## Testing

To verify the fix works:
1. Deploy an application
2. Verify resources exist in AWS console (ECS service, target group, etc.)
3. Delete the application via API
4. Verify all application-specific resources are removed from AWS
