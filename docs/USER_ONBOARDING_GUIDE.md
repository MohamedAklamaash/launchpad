# Launchpad Platform - User Onboarding Guide

## Overview

Launchpad is a cloud deployment platform that deploys your applications into **your own AWS account**. You maintain full control and ownership of your infrastructure and data.

---

## Prerequisites

- AWS Account
- GitHub Account
- GitHub repository with a Dockerfile

---

## Step 1: Create IAM User for Launchpad

Launchpad needs access to your AWS account to provision infrastructure and deploy applications. You'll create a dedicated IAM user with specific permissions.

### 1.1 Sign in to AWS Console

1. Go to [AWS Console](https://console.aws.amazon.com)
2. Navigate to **IAM** service
3. Click **Users** → **Create user**

### 1.2 Create User

1. **User name**: `launchpad-deployment`
2. **Access type**: Select "Programmatic access"
3. Click **Next: Permissions**

### 1.3 Attach Policies

Click **Attach policies directly** and attach these AWS managed policies:

**Required Policies**:
- `AmazonVPCFullAccess` - For VPC, subnets, NAT gateway
- `AmazonECS_FullAccess` - For ECS clusters and services
- `AmazonEC2ContainerRegistryFullAccess` - For ECR repositories
- `ElasticLoadBalancingFullAccess` - For Application Load Balancer
- `IAMFullAccess` - For creating service roles
- `AWSCodeBuildAdminAccess` - For CodeBuild projects
- `CloudWatchLogsFullAccess` - For application logs
- `AmazonS3FullAccess` - For Terraform state storage
- `AmazonDynamoDBFullAccess` - For Terraform state locking

Click **Next: Tags** (optional) → **Next: Review** → **Create user**

### 1.4 Save Credentials

**IMPORTANT**: Save these credentials securely. You'll need them for Launchpad.

- **Access Key ID**: `AKIA...`
- **Secret Access Key**: `wJalr...`

⚠️ **Never share these credentials or commit them to Git**

---

## Step 2: Create Cross-Account Role

Launchpad assumes a role in your account for deployments. This provides better security than long-lived credentials.

### 2.1 Create Role

1. In AWS Console, go to **IAM** → **Roles** → **Create role**
2. **Trusted entity type**: Select "AWS account"
3. **An AWS account**: Select "Another AWS account"
4. **Account ID**: Enter Launchpad's AWS account ID (provided by Launchpad)
5. Click **Next**

### 2.2 Attach Policies

Attach the same policies as the IAM user:
- `AmazonVPCFullAccess`
- `AmazonECS_FullAccess`
- `AmazonEC2ContainerRegistryFullAccess`
- `ElasticLoadBalancingFullAccess`
- `IAMFullAccess`
- `AWSCodeBuildAdminAccess`
- `CloudWatchLogsFullAccess`
- `AmazonS3FullAccess`
- `AmazonDynamoDBFullAccess`

### 2.3 Name the Role

**Role name**: `LaunchpadDeploymentRole`

⚠️ **This exact name is required** - Launchpad expects this role name.

Click **Create role**

### 2.4 Note Your Account ID

You'll need your AWS Account ID for Launchpad:

1. Click your account name in top-right corner
2. Copy the **Account ID** (12-digit number)
3. Example: `123456789012`

---

## Step 3: Sign Up for Launchpad

1. Go to [Launchpad Platform](https://launchpad.example.com)
2. Click **Sign in with GitHub**
3. Authorize Launchpad to access your GitHub repositories

---

## Step 4: Create Infrastructure

Infrastructure represents your AWS environment where applications will run.

### 4.1 Create Infrastructure

1. In Launchpad dashboard, click **Create Infrastructure**
2. Fill in details:
   - **Name**: `production` (or any name)
   - **Cloud Provider**: AWS
   - **AWS Account ID**: Your 12-digit account ID
   - **Region**: `us-west-2` (or your preferred region)
   - **Max CPU**: `4` (vCPU limit for all applications)
   - **Max Memory**: `8` (GB limit for all applications)

3. Click **Create**

### 4.2 Wait for Provisioning

Launchpad will provision infrastructure in your AWS account:
- VPC with public/private subnets
- NAT Gateway
- ECS Cluster
- Application Load Balancer
- ECR Repository
- IAM Roles

**Time**: 5-10 minutes

**Status**: Watch the status change from `PENDING` → `PROVISIONING` → `ACTIVE`

---

## Step 5: Prepare Your Application

Your application must have a Dockerfile in the repository.

### 5.1 Create Dockerfile

Example for Node.js application:

```dockerfile
FROM node:18-alpine

WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies
RUN npm install --production

# Copy application code
COPY . .

# Expose port
EXPOSE 8000

# Start application
CMD ["npm", "start"]
```

### 5.2 Commit and Push

```bash
git add Dockerfile
git commit -m "Add Dockerfile"
git push origin main
```

---

## Step 6: Deploy Application

### 6.1 Create Application

1. In Launchpad, click **Create Application**
2. Fill in details:

```json
{
    "name": "my-api",
    "infrastructure_id": "<your-infrastructure-id>",
    "project_remote_url": "https://github.com/yourusername/your-repo",
    "project_branch": "main",
    "project_commit_hash": "HEAD",
    "dockerfile_path": "Dockerfile",
    "alloted_cpu": 0.5,
    "alloted_memory": 1.0,
    "alloted_storage": 0.5,
    "envs": {
        "NODE_ENV": "production",
        "PORT": "8000"
    }
}
```

**Field Descriptions**:
- `name`: Application name (used in URL)
- `infrastructure_id`: From Step 4
- `project_remote_url`: Your GitHub repository URL
- `project_branch`: Branch to deploy
- `project_commit_hash`: Commit hash or "HEAD"
- `dockerfile_path`: Path to Dockerfile (default: "Dockerfile")
- `alloted_cpu`: CPU allocation in vCPU (0.25, 0.5, 1, 2, 4)
- `alloted_memory`: Memory in GB (0.5, 1, 2, 4, 8, 16)
- `envs`: Environment variables for your application

3. Click **Create**

### 6.2 Deploy Application

1. Click **Deploy** on your application
2. Wait for deployment (5-15 minutes)

**Deployment Steps**:
- `BUILDING`: CodeBuild clones repo and builds Docker image
- `PUSHING_IMAGE`: Image pushed to ECR
- `DEPLOYING`: ECS service created
- `ACTIVE`: Application running

### 6.3 Access Your Application

Once status is `ACTIVE`, your application is accessible at:

```
http://<alb-dns>/<app-name>
```

Example:
```
http://my-alb-123456.us-west-2.elb.amazonaws.com/my-api
```

---

## Step 7: Monitor Your Application

### 7.1 CloudWatch Logs

View application logs in AWS Console:

1. Go to **CloudWatch** → **Log groups**
2. Find `/ecs/<app-name>-task`
3. View real-time logs

### 7.2 ECS Service

Monitor container health:

1. Go to **ECS** → **Clusters**
2. Click your cluster
3. View services and tasks

### 7.3 Cost Monitoring

All resources run in **your AWS account**. Monitor costs:

1. Go to **AWS Cost Explorer**
2. Filter by service: ECS, ECR, ALB, NAT Gateway

---

## Resource Limits

### CPU and Memory Combinations (Fargate)

| CPU (vCPU) | Memory (GB) |
|------------|-------------|
| 0.25       | 0.5 - 2     |
| 0.5        | 1 - 4       |
| 1          | 2 - 8       |
| 2          | 4 - 16      |
| 4          | 8 - 30      |

### Infrastructure Limits

Set during infrastructure creation:
- `max_cpu`: Total CPU across all applications
- `max_memory`: Total memory across all applications

Example: If `max_cpu = 4`, you can run:
- 4 apps with 1 vCPU each, OR
- 8 apps with 0.5 vCPU each, OR
- Any combination totaling ≤ 4 vCPU

---

## Security Best Practices

### 1. IAM Credentials

- ✅ Create dedicated IAM user for Launchpad
- ✅ Use cross-account role (AssumeRole)
- ✅ Never share credentials
- ✅ Rotate credentials regularly
- ❌ Don't use root account credentials

### 2. Secrets Management

- Store secrets in environment variables
- Use AWS Secrets Manager for sensitive data (future)
- Never commit secrets to Git

### 3. Network Security

- Applications run in private subnets
- Only ALB is publicly accessible
- Use security groups to restrict access

### 4. Access Control

- Use GitHub repository permissions
- Invite team members through Launchpad
- Review IAM policies regularly

---

## Troubleshooting

### Infrastructure Provisioning Failed

**Check**:
1. IAM role `LaunchpadDeploymentRole` exists
2. Role has correct policies attached
3. AWS Account ID is correct
4. Region is supported

**View Logs**:
- Launchpad dashboard shows error message
- Check AWS CloudTrail for API errors

### Application Deployment Failed

**Common Issues**:

1. **Build Failed**
   - Check Dockerfile syntax
   - Verify repository is accessible
   - Check CodeBuild logs in AWS Console

2. **Container Won't Start**
   - Check CloudWatch logs
   - Verify PORT environment variable
   - Ensure application listens on correct port

3. **Out of Resources**
   - Check infrastructure CPU/memory limits
   - Reduce application resource allocation
   - Delete unused applications

### Application Not Accessible

**Check**:
1. Application status is `ACTIVE`
2. ALB health checks passing
3. Security groups allow traffic
4. Application listens on port 8000

---

## Cost Estimation

### Monthly Costs (Example)

**Infrastructure** (always running):
- VPC: Free
- NAT Gateway: ~$32/month
- ALB: ~$16/month + data transfer
- ECS Cluster: Free (pay for tasks)

**Per Application**:
- ECS Fargate (0.5 vCPU, 1GB): ~$15/month
- ECR Storage: ~$0.10/GB/month
- CodeBuild: ~$0.005/build minute

**Example**: 3 small applications
- Infrastructure: ~$50/month
- Applications: ~$45/month
- **Total**: ~$95/month

💡 **Tip**: Use AWS Cost Calculator for accurate estimates

---

## Cleanup

### Delete Application

1. In Launchpad, click application
2. Click **Delete**
3. Confirm deletion

Resources deleted:
- ECS Service
- Task Definition
- Target Group
- ALB Listener Rule

### Delete Infrastructure

1. Click infrastructure
2. Click **Delete**
3. Wait for destruction (5-10 minutes)

Resources deleted:
- All applications
- ECS Cluster
- ALB
- NAT Gateway
- VPC
- ECR Repository

⚠️ **Warning**: This deletes all applications in the infrastructure

---

## Support

### Documentation
- [API Reference](https://docs.launchpad.example.com)
- [GitHub Examples](https://github.com/launchpad/examples)

### Community
- [Discord](https://discord.gg/launchpad)
- [GitHub Discussions](https://github.com/launchpad/discussions)

### Contact
- Email: support@launchpad.example.com
- Status: status.launchpad.example.com

---

## Next Steps

1. ✅ Set up AWS IAM user and role
2. ✅ Create infrastructure
3. ✅ Deploy first application
4. 🚀 Deploy more applications
5. 🚀 Set up custom domain (coming soon)
6. 🚀 Enable HTTPS (coming soon)
7. 🚀 Configure auto-scaling (coming soon)

---

## FAQ

**Q: Do you store my AWS credentials?**
A: We store temporary session credentials obtained via AssumeRole. These expire after 1 hour and are automatically refreshed.

**Q: Can I use my existing VPC?**
A: Not currently. Launchpad creates a dedicated VPC for isolation.

**Q: What regions are supported?**
A: All AWS regions. Specify during infrastructure creation.

**Q: Can I SSH into containers?**
A: No. Use CloudWatch Logs for debugging. ECS Exec coming soon.

**Q: How do I update my application?**
A: Update your code, push to GitHub, and click Deploy again in Launchpad.

**Q: Can I use private GitHub repositories?**
A: Yes. Launchpad uses your GitHub OAuth token to access private repos.

**Q: What if I hit resource limits?**
A: Increase `max_cpu` and `max_memory` in infrastructure settings, or delete unused applications.

**Q: Can I deploy databases?**
A: Yes, but we recommend using AWS RDS for production databases.

**Q: Is there a free tier?**
A: Launchpad platform is free. You pay only for AWS resources in your account.

**Q: Can I export my infrastructure?**
A: Yes. All infrastructure is defined in Terraform. Contact support for export.
