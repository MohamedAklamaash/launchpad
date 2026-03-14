# Launchpad Quick Start

## 5-Minute Setup

### 1. AWS Setup (2 minutes)

**Option A: Automated Script (Recommended)**
```bash
# Download and run the setup script
curl -fsSL https://raw.githubusercontent.com/launchpad/setup/main/create_aws_role.sh | bash
```

Or manually download and run:
```bash
wget https://raw.githubusercontent.com/launchpad/setup/main/create_aws_role.sh
chmod +x create_aws_role.sh
./create_aws_role.sh
```

This script will:
- Create IAM role `LaunchpadDeploymentRole`
- Attach required policies
- Configure trust relationship with Launchpad platform
- Output your AWS Account ID

**Option B: Manual Setup**
1. AWS Console → IAM → Roles → Create role
2. Trusted entity: AWS account
3. Account ID: `221082203366` (Launchpad platform account)
4. Trusted user: `aklamaash-terraform`
5. Create inline policy with permissions:
   - `ec2:*`, `ecs:*`, `elasticloadbalancing:*`
   - `ecr:*`, `logs:*`, `codebuild:*`
   - `s3:*`, `dynamodb:*`
   - `iam:*`, `kms:*`
6. Role name: `LaunchpadDeploymentRole` (exact name required)
7. Note your AWS Account ID (12 digits)

### 2. Launchpad Setup (1 minute)

1. Go to https://launchpad.example.com
2. Click "Sign in with GitHub"
3. Authorize Launchpad

### 3. Create Infrastructure (1 minute)

```json
{
  "name": "production",
  "cloud_provider": "AWS",
  "code": "<YOUR_AWS_ACCOUNT_ID>",
  "max_cpu": 4,
  "max_memory": 8
}
```

Wait 5-10 minutes for provisioning.

### 4. Deploy Application (1 minute)

**Requirements**: Repository with Dockerfile

```json
{
  "name": "my-app",
  "infrastructure_id": "<INFRA_ID>",
  "project_remote_url": "https://github.com/user/repo",
  "project_branch": "main",
  "project_commit_hash": "HEAD",
  "alloted_cpu": 0.5,
  "alloted_memory": 1.0,
  "envs": {
    "NODE_ENV": "production"
  }
}
```

Wait 5-15 minutes for deployment.

### 5. Access Application

```
http://<alb-dns>/<app-name>
```

---

## Example Dockerfile

```dockerfile
FROM node:18-alpine
WORKDIR /app
COPY package*.json ./
RUN npm install --production
COPY . .
EXPOSE 8000
CMD ["npm", "start"]
```

---

## Resource Limits

| CPU (vCPU) | Memory (GB) | Cost/Month* |
|------------|-------------|-------------|
| 0.25       | 0.5         | ~$7         |
| 0.5        | 1.0         | ~$15        |
| 1.0        | 2.0         | ~$30        |
| 2.0        | 4.0         | ~$60        |

*Approximate ECS Fargate costs only

---

## Common Commands

### View Logs
```bash
aws logs tail /ecs/<app-name>-task --follow
```

### Check ECS Service
```bash
aws ecs describe-services \
  --cluster <cluster-name> \
  --services <app-name>-service
```

### Check ALB
```bash
aws elbv2 describe-load-balancers
```

---

## Troubleshooting

### Build Failed
- Check Dockerfile syntax
- Verify repository access
- Check CodeBuild logs in AWS Console

### Container Won't Start
- Check CloudWatch logs
- Verify PORT environment variable
- Ensure app listens on port 8000

### Can't Access Application
- Check application status is ACTIVE
- Verify ALB health checks passing
- Check security groups

---

## Support

- Docs: https://docs.launchpad.example.com
- Discord: https://discord.gg/launchpad
- Email: support@launchpad.example.com

---

## Security Checklist

- Use dedicated IAM role (not root)
- Enable CloudTrail
- Never commit AWS credentials
- Rotate credentials regularly
- Use environment variables for secrets
- ✅ Review IAM policies quarterly

---

## Cost Optimization

1. **Right-size resources**: Start small (0.25 vCPU)
2. **Delete unused apps**: Free up resources
3. **Use Spot instances**: Coming soon
4. **Monitor costs**: AWS Cost Explorer
5. **Set billing alerts**: AWS Budgets

---

## Next Steps

1. ✅ Deploy first application
2. 🚀 Add custom domain
3. 🚀 Enable HTTPS
4. 🚀 Set up CI/CD
5. 🚀 Configure auto-scaling
6. 🚀 Add monitoring
7. 🚀 Deploy database

---

**Version**: 1.0.0  
**Last Updated**: 2026-03-08
