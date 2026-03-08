# Docker Hub Rate Limiting

## Problem

CodeBuild pulls base images from Docker Hub (e.g., `node:21.6.2-alpine`). Docker Hub has rate limits:

- **Unauthenticated**: 100 pulls per 6 hours per IP
- **Authenticated (free)**: 200 pulls per 6 hours
- **Pro/Team**: Higher limits

Error message:
```
429 Too Many Requests - toomanyrequests: You have reached your unauthenticated pull rate limit
```

## Solutions

### Option 1: Wait and Retry (Current)

Simply wait 6 hours or retry later. Rate limits reset every 6 hours.

### Option 2: Use Docker Hub Authentication (Recommended)

Add Docker Hub credentials to CodeBuild:

1. **Create Docker Hub account** (free)
2. **Generate access token**: Docker Hub → Account Settings → Security → New Access Token
3. **Store in AWS Secrets Manager**:
   ```bash
   aws secretsmanager create-secret \
     --name dockerhub-credentials \
     --secret-string '{"username":"your-username","password":"your-token"}' \
     --region us-west-2
   ```

4. **Update buildspec** to login before build:
   ```yaml
   pre_build:
     commands:
       - echo "Logging in to Docker Hub..."
       - |
         DOCKER_USER=$(aws secretsmanager get-secret-value --secret-id dockerhub-credentials --query SecretString --output text | jq -r .username)
         DOCKER_PASS=$(aws secretsmanager get-secret-value --secret-id dockerhub-credentials --query SecretString --output text | jq -r .password)
         echo $DOCKER_PASS | docker login -u $DOCKER_USER --password-stdin
   ```

5. **Update IAM role** to allow Secrets Manager access:
   ```json
   {
     "Effect": "Allow",
     "Action": [
       "secretsmanager:GetSecretValue"
     ],
     "Resource": "arn:aws:secretsmanager:*:*:secret:dockerhub-credentials-*"
   }
   ```

### Option 3: Use ECR Public Gallery

Replace Docker Hub images with ECR Public equivalents:

**Dockerfile changes**:
```dockerfile
# Before
FROM node:21.6.2-alpine

# After
FROM public.ecr.aws/docker/library/node:21.6.2-alpine
```

**Benefits**:
- No rate limits
- Faster pulls (AWS network)
- No authentication needed

**Available images**: https://gallery.ecr.aws/

### Option 4: Mirror Images to Private ECR

Pull images once and store in your ECR:

```bash
# Pull from Docker Hub
docker pull node:21.6.2-alpine

# Tag for ECR
docker tag node:21.6.2-alpine 221082203366.dkr.ecr.us-west-2.amazonaws.com/base-images:node-21.6.2-alpine

# Push to ECR
docker push 221082203366.dkr.ecr.us-west-2.amazonaws.com/base-images:node-21.6.2-alpine
```

**Update Dockerfile**:
```dockerfile
FROM 221082203366.dkr.ecr.us-west-2.amazonaws.com/base-images:node-21.6.2-alpine
```

## Implementation Status

**Current**: No Docker Hub authentication (Option 1 - wait and retry)

**Recommended**: Implement Option 2 (Docker Hub auth) or Option 3 (ECR Public)

## Quick Fix for Users

Tell users to update their Dockerfile to use ECR Public:

```dockerfile
# Change this:
FROM node:21.6.2-alpine

# To this:
FROM public.ecr.aws/docker/library/node:21.6.2-alpine
```

No code changes needed on platform side!
