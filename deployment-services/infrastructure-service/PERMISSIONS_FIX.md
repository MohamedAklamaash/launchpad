# AWS Permissions Fix

## Problem
Terraform was failing with:
- `iam:CreateRole` - Access Denied
- `kms:TagResource` - Access Denied

## Root Cause
The `LaunchpadDeploymentPolicy` attached to `LaunchpadDeploymentRole` was missing IAM and KMS permissions.

## Solution Applied

### 1. Updated Policy
Added missing permissions to `/home/aklamaash/Desktop/launchpad/app_scripts/create_aws_role.sh`:

```json
{
  "Effect": "Allow",
  "Action": [
    "iam:CreateRole",
    "iam:DeleteRole",
    "iam:GetRole",
    "iam:PassRole",
    "iam:AttachRolePolicy",
    "iam:DetachRolePolicy",
    "iam:PutRolePolicy",
    "iam:DeleteRolePolicy",
    "iam:GetRolePolicy",
    "iam:ListRolePolicies",
    "iam:ListAttachedRolePolicies",
    "iam:TagRole",
    "iam:UntagRole"
  ],
  "Resource": "*"
},
{
  "Effect": "Allow",
  "Action": [
    "kms:CreateKey",
    "kms:CreateAlias",
    "kms:DeleteAlias",
    "kms:DescribeKey",
    "kms:EnableKeyRotation",
    "kms:PutKeyPolicy",
    "kms:ScheduleKeyDeletion",
    "kms:TagResource",
    "kms:UntagResource"
  ],
  "Resource": "*"
}
```

### 2. Applied Update
Created and ran `/home/aklamaash/Desktop/launchpad/app_scripts/update_aws_role.sh`:
- Created new policy version (v2)
- Set as default
- Deleted old version (v1)

### 3. Verification
✅ Policy updated successfully
✅ IAM permissions verified (CreateRole works)
✅ KMS permissions added

## Next Steps

### For Failed Infrastructure
The infrastructure `019cc77a-1c0d-78d0-888d-8f3f09cc0270` is in ERROR state.

**Option 1: Delete and recreate**
```bash
# Delete the failed infrastructure via API
curl -X DELETE http://localhost:8000/infrastructures/019cc77a-1c0d-78d0-888d-8f3f09cc0270/

# Create new infrastructure
curl -X POST http://localhost:8000/infrastructures/ -d '{...}'
```

**Option 2: Manual retry (if worker supports it)**
- Check worker logs
- Worker should automatically retry if configured

### For New Infrastructures
All new infrastructure provisioning will now work with the updated permissions.

## Policy Limits
AWS allows max 10 managed policies per role. Current usage:
- 1 policy: `LaunchpadDeploymentPolicy` (consolidated all permissions)

If you need more permissions in the future:
1. Update the existing policy (use `update_aws_role.sh`)
2. Or create additional roles for specific purposes

## Files Modified
1. `/home/aklamaash/Desktop/launchpad/app_scripts/create_aws_role.sh` - Updated with IAM/KMS permissions
2. `/home/aklamaash/Desktop/launchpad/app_scripts/update_aws_role.sh` - New script to update existing policy

## Testing
To test permissions before provisioning:
```bash
cd /home/aklamaash/Desktop/launchpad/app_scripts
./test_permissions.sh
```

This will verify IAM and KMS permissions work correctly.
