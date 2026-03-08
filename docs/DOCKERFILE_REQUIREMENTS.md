# Dockerfile Requirements

## Base Images

**IMPORTANT**: Use ECR Public Gallery for base images to avoid Docker Hub rate limits.

### Correct Base Images

```dockerfile
# Node.js
FROM public.ecr.aws/docker/library/node:21-alpine

# Python
FROM public.ecr.aws/docker/library/python:3.11-slim

# Go
FROM public.ecr.aws/docker/library/golang:1.21-alpine

# Java
FROM public.ecr.aws/docker/library/openjdk:17-slim

# Nginx
FROM public.ecr.aws/docker/library/nginx:alpine

# Alpine
FROM public.ecr.aws/docker/library/alpine:latest
```

### ❌ Don't Use Docker Hub

```dockerfile
# These will hit rate limits:
FROM node:21-alpine          # ❌
FROM python:3.11-slim        # ❌
FROM golang:1.21-alpine      # ❌
```

### ✅ Use ECR Public

```dockerfile
# These work without rate limits:
FROM public.ecr.aws/docker/library/node:21-alpine          # ✅
FROM public.ecr.aws/docker/library/python:3.11-slim        # ✅
FROM public.ecr.aws/docker/library/golang:1.21-alpine      # ✅
```

## Browse Available Images

Visit: https://gallery.ecr.aws/

Search for your base image (node, python, golang, etc.)

## Example Dockerfile

```dockerfile
# Use ECR Public Gallery
FROM public.ecr.aws/docker/library/node:21-alpine

WORKDIR /app

COPY package*.json ./
RUN npm ci --only=production

COPY . .

EXPOSE 8000

CMD ["node", "server.js"]
```

## Why ECR Public?

- ✅ No rate limits
- ✅ Faster pulls (AWS network)
- ✅ No authentication needed
- ✅ Same images as Docker Hub (official mirrors)

## Deployment Flow

1. CodeBuild pulls base image from ECR Public (no rate limit)
2. Builds your application image
3. Pushes to your private ECR repository
4. ECS pulls from your private ECR
5. Application runs

**No Docker Hub involved!**
