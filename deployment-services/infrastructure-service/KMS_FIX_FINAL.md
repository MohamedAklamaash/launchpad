# KMS Permission Fix - Final

## Problem
Terraform kept failing with missing KMS permissions:
1. `kms:GetKeyRotationStatus` 
2. `kms:GetKeyPolicy`
3. `kms:ListResourceTags`

## Root Cause
Terraform's AWS provider needs various KMS permissions depending on the resource configuration. Listing them individually was error-prone.

## Final Solution
Replaced granular KMS permissions with wildcard:

```json
{
  "Effect": "Allow",
  "Action": "kms:*",
  "Resource": "*"
}
```

This grants ALL KMS operations, preventing future permission errors.

## Applied
- Updated via `./update_aws_role.sh`
- Policy version: **v6**
- Verified: ✅

## Cleanup
Orphaned KMS key from failed provision cleaned up:
```bash
./cleanup_kms_key.sh 1fb5e7e1-52ca-4bfc-835c-cfa6ed89d699
```

## S3/DynamoDB Backend
Already using shared resources per account (as designed):
- S3: `launchpad-tf-state-221082203366`
- DynamoDB: `launchpad-tf-locks-221082203366`
- State path: `infra/{infra_id}/terraform.tfstate` (unique per infrastructure)

## Next Steps
1. Restart worker: `Ctrl+C` then `./worker.py`
2. Infrastructure should now provision successfully
3. If any other permission errors occur, add wildcard for that service (e.g., `iam:*`, `ec2:*`)
