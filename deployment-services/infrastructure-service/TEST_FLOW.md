# Test Infrastructure Provisioning Flow

## Current Architecture (Correct)

```
AWS Account: 221082203366
├── S3 Bucket: launchpad-tf-state-221082203366
│   ├── infra/019cc75b-.../terraform.tfstate  (Infrastructure 1)
│   ├── infra/019cc760-.../terraform.tfstate  (Infrastructure 2)
│   └── infra/019cc765-.../terraform.tfstate  (Infrastructure 3)
│
└── DynamoDB: launchpad-tf-locks-221082203366
    ├── Lock: infra/019cc75b-.../terraform.tfstate
    ├── Lock: infra/019cc760-.../terraform.tfstate
    └── Lock: infra/019cc765-.../terraform.tfstate
```

**This is correct!** Multiple infrastructures share the same bucket/table but have separate state files.

## Debug Current Error

The logs show apply failed but don't show the actual error. Let's get full logs:

```bash
# Check worker logs
tail -100 /path/to/worker.log

# Or if running in foreground
python worker.py
```

## Expected Successful Flow

### Provision Success Log:
```
INFO Worker {id} processing provision job: {infra_id}
INFO Running terraform apply for {infra_id}
INFO S3 bucket launchpad-tf-state-{account} exists and is accessible
INFO DynamoDB table launchpad-tf-locks-{account} exists
INFO [INIT]
Terraform has been successfully initialized!
INFO [COMMAND]
Apply complete! Resources: 15 added, 0 changed, 0 destroyed.
INFO Infrastructure {infra_id} provisioned successfully
INFO [NOTIFICATION] Infrastructure successfully provisioned.
```

### Destroy Success Log:
```
INFO Worker {id} processing destroy job: {infra_id}
INFO S3 bucket launchpad-tf-state-{account} exists and is accessible
INFO DynamoDB table launchpad-tf-locks-{account} exists
INFO [COMMAND]
Destroy complete! Resources: 15 destroyed.
INFO Infrastructure {infra_id} destroyed
INFO [NOTIFICATION] Infrastructure successfully destroyed.
```

## Common Issues & Fixes

### Issue 1: Terraform Module Errors
**Symptom**: "Missing required argument" or "Unsupported attribute"
**Fix**: Already fixed - IAM module added, ALB security group added

### Issue 2: AWS Permission Errors
**Symptom**: "AccessDenied" or "UnauthorizedOperation"
**Fix**: Ensure LaunchpadDeploymentRole has correct permissions

### Issue 3: Resource Already Exists
**Symptom**: "AlreadyExists" errors
**Fix**: Already fixed - unique resource names with infra_id

### Issue 4: VPC Deletion Fails
**Symptom**: "DependencyViolation" when deleting VPC
**Fix**: Already fixed - added depends_on for proper order

## Test Commands

### 1. Check Worker Status
```bash
ps aux | grep worker.py
```

### 2. Check Queue
```bash
redis-cli LLEN infra:provision
redis-cli LLEN infra:destroy
```

### 3. Check Database
```sql
SELECT id, status, error_message, retry_count 
FROM environments 
WHERE infrastructure_id = '{infra_id}';
```

### 4. Check AWS Resources
```bash
# Check S3 bucket
aws s3 ls s3://launchpad-tf-state-{account}/infra/

# Check DynamoDB table
aws dynamodb describe-table --table-name launchpad-tf-locks-{account}

# Check VPC
aws ec2 describe-vpcs --filters "Name=tag:InfraID,Values={infra_id}"
```

## Next Steps

1. **Restart worker** with latest code:
   ```bash
   pkill -f worker.py
   python worker.py
   ```

2. **Create new infrastructure** via API

3. **Watch logs** for full error details

4. **Share complete error logs** if it fails again

---

**Note**: The bucket/table naming is correct. One bucket per account is the standard pattern.
