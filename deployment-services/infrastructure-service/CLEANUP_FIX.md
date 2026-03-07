# Infrastructure Cleanup Fix

## Issues Fixed

### 1. Missing KMS Permission: `kms:GetKeyPolicy`
**Error:** 
```
User: arn:aws:sts::221082203366:assumed-role/LaunchpadDeploymentRole/deployment-session 
is not authorized to perform: kms:GetKeyPolicy on resource: arn:aws:kms:us-west-2:221082203366:key/...
```

**Root Cause:**
Terraform's `aws_kms_key` resource with a `policy` attribute needs to read back the policy after setting it to verify it was applied correctly.

**Fix Applied:**
- Added `kms:GetKeyPolicy` to LaunchpadDeploymentPolicy
- Updated via `/home/aklamaash/Desktop/launchpad/app_scripts/update_aws_role.sh`
- Policy now at version v5

**Current KMS Permissions:**
```json
{
  "Effect": "Allow",
  "Action": [
    "kms:CreateKey",
    "kms:CreateAlias",
    "kms:DeleteAlias",
    "kms:DescribeKey",
    "kms:EnableKeyRotation",
    "kms:GetKeyRotationStatus",
    "kms:GetKeyPolicy",
    "kms:PutKeyPolicy",
    "kms:ScheduleKeyDeletion",
    "kms:TagResource",
    "kms:UntagResource"
  ],
  "Resource": "*"
}
```

### 2. Misleading Cleanup Messages
**Problem:**
Notification messages claimed "All partially created resources were automatically destroyed" even when cleanup might have failed.

**Fix Applied:**
1. **Enhanced `_handle_provision_failure` in `terraform_worker.py`:**
   - Now logs whether destroy succeeded or failed
   - Includes cleanup status in error message
   - Warns if manual cleanup is required

2. **Updated `send_provision_failure` in `notification.py`:**
   - Removed hardcoded misleading message
   - Now shows actual error from worker (includes cleanup status)

**New Behavior:**
- If destroy succeeds: Error message includes "All resources were destroyed."
- If destroy fails: Error message includes "WARNING: Cleanup failed. Manual cleanup required in AWS account."

## How Cleanup Works

### Automatic Cleanup on Provision Failure
1. Terraform apply fails
2. Worker detects permanent failure (non-retryable error)
3. Worker runs `terraform destroy -auto-approve`
4. Destroy result is logged and included in error message
5. User is notified with actual cleanup status

### State Management
- Terraform state is stored in S3: `s3://launchpad-tf-state-{account_id}/infra/{infra_id}/terraform.tfstate`
- Each infrastructure has isolated state
- Destroy command reads state from S3 to know what to clean up

### Manual Cleanup (if needed)
If automatic cleanup fails, check the logs in the Environment model for the destroy output, then:

```bash
# 1. Assume the deployment role
aws sts assume-role \
  --role-arn arn:aws:iam::221082203366:role/LaunchpadDeploymentRole \
  --role-session-name manual-cleanup

# 2. Export credentials from response
export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
export AWS_SESSION_TOKEN="..."

# 3. Download state file
aws s3 cp s3://launchpad-tf-state-221082203366/infra/{infra_id}/terraform.tfstate ./

# 4. Run terraform destroy manually
terraform destroy -auto-approve
```

## Testing
To verify the fix works:
1. Try provisioning infrastructure - should now succeed with all KMS permissions
2. If it fails, check the error_message field in Environment model - should include cleanup status
3. Check worker logs for "Successfully destroyed resources" or "Failed to destroy resources"

## Files Modified
1. `/home/aklamaash/Desktop/launchpad/app_scripts/create_aws_role.sh` - Already had kms:GetKeyPolicy
2. `/home/aklamaash/Desktop/launchpad/app_scripts/update_aws_role.sh` - Added kms:GetKeyPolicy
3. `/home/aklamaash/Desktop/launchpad/deployment-services/infrastructure-service/api/services/terraform_worker.py` - Enhanced cleanup logging
4. `/home/aklamaash/Desktop/launchpad/deployment-services/infrastructure-service/api/services/notification.py` - Removed misleading message
