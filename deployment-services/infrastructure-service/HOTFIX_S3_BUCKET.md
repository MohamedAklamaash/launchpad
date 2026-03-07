# Hotfix: S3 Bucket Name Collision

## Issue

**Error**: `BucketAlreadyExists` when creating Terraform state bucket

**Root Cause**: 
- Bucket name `launchpad-tf-state` is globally shared across all AWS accounts
- Someone else already owns this bucket name

## Fix Applied

Changed bucket naming strategy to be **account-specific**:

**Before**:
```python
bucket = "launchpad-tf-state"
table = "launchpad-tf-locks"
```

**After**:
```python
bucket = f"launchpad-tf-state-{account_id}"
table = f"launchpad-tf-locks-{account_id}"
```

## Changes Made

**File**: `api/services/terraform_worker.py`

1. Updated `_ensure_backend()` to accept `account_id` parameter
2. Updated `_exec_tf()` to pass `account_id`
3. Updated `provision()` to extract account_id from `infra.code`
4. Updated `destroy()` to extract account_id from `infra.code`
5. Improved error handling for 403 (Forbidden) vs 404 (Not Found)

## Result

Each AWS account now gets its own unique bucket:
- Account `123456789012` → `launchpad-tf-state-123456789012`
- Account `987654321098` → `launchpad-tf-state-987654321098`

This prevents global namespace collisions.

## Testing

```bash
# Restart worker
pkill -f worker.py
python worker.py &

# Retry failed infrastructure
# The worker will automatically retry or you can re-create
```

## Verification

Check that bucket is created with account-specific name:
```bash
aws s3 ls | grep launchpad-tf-state
```

Should show: `launchpad-tf-state-{your-account-id}`

---

**Status**: ✅ Fixed
**Date**: 2026-03-07
