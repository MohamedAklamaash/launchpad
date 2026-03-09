# Dockerfile Requirements

## ⚠️ Critical: Use ECR Public Gallery for Base Images

**DO NOT use Docker Hub base images** - they have rate limits that will cause builds to fail.

### ❌ Wrong (Docker Hub - Rate Limited)
```dockerfile
FROM python:3.11-slim
FROM node:21-alpine
FROM nginx:alpine
FROM postgres:15
```

### ✅ Correct (ECR Public Gallery - No Rate Limits)
```dockerfile
FROM public.ecr.aws/docker/library/python:3.11-slim
FROM public.ecr.aws/docker/library/node:21-alpine
FROM public.ecr.aws/docker/library/nginx:alpine
FROM public.ecr.aws/docker/library/postgres:15
```

## Common Error

If you see this error in build logs:
```
429 Too Many Requests - toomanyrequests: You have reached your 
unauthenticated pull rate limit
```

**Fix:** Update your Dockerfile's `FROM` statement to use `public.ecr.aws/docker/library/` prefix.

## Port Configuration

Your application must:
1. Listen on the port specified in the `PORT` environment variable
2. Listen on `0.0.0.0`, not `localhost`
3. Respond to `GET /` for health checks (return 200-499 status)

### Example (Node.js)
```javascript
const PORT = process.env.PORT || 8080;
app.listen(PORT, '0.0.0.0', () => {
  console.log(`Server listening on port ${PORT}`);
});
```

### Example (Python/Flask)
```python
import os
port = int(os.environ.get('PORT', 8080))
app.run(host='0.0.0.0', port=port)
```

### Example (Python/FastAPI)
```python
import os
import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
```

## Complete Example Dockerfile (Python)

```dockerfile
FROM public.ecr.aws/docker/library/python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

CMD ["python", "app.py"]
```

## Complete Example Dockerfile (Node.js)

```dockerfile
FROM public.ecr.aws/docker/library/node:21-alpine

WORKDIR /app

COPY package*.json ./
RUN npm ci --only=production

COPY . .

EXPOSE 8080

CMD ["node", "server.js"]
```
